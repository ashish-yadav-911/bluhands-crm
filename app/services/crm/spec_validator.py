"""Semantic validation of a CRM spec.

The second of two validation stages. Pydantic (`spec_models`) has already
checked shapes and closed enums; this checks everything that needs the whole
document in view -- cross references, naming rules, Link cycles, budgets. It
runs on a *normalized* spec, so it never has to complain about things the
normalizer can fix (missing fieldnames, undeclared roles).

Every message is written to be fed back to a planner as a retry prompt, so
each says what is wrong and where, in the spec's own terms.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import ValidationError

from app.services.crm import catalog
from app.services.crm.spec_models import CrmSpec, DoctypeSpec
from app.services.crm.spec_normalizer import normalize

_DOCTYPE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]*$")
_FIELDNAME = re.compile(r"^[a-z][a-z0-9_]*$")
# Fields the normalizer injects; legitimate only in their injected shape.
_INJECTED = {"naming_series": ("Select", None), "workflow_state": ("Link", "Workflow State")}


@dataclass(frozen=True)
class SpecIssue:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


class SpecValidationError(ValueError):
    def __init__(self, issues: list[SpecIssue]):
        self.issues = issues
        super().__init__("Invalid CRM spec:\n" + "\n".join(f"  - {i}" for i in issues))


def load_spec(raw: dict | CrmSpec) -> CrmSpec:
    """The single entry point: structural parse -> normalize -> semantic check.

    Raises SpecValidationError with every problem found, not just the first,
    because a planner retry that fixes one error at a time is a slow retry.
    """
    try:
        spec = raw if isinstance(raw, CrmSpec) else CrmSpec.model_validate(raw)
    except ValidationError as exc:
        raise SpecValidationError(
            [SpecIssue(".".join(str(p) for p in e["loc"]) or "spec", e["msg"]) for e in exc.errors()]
        ) from exc

    spec = normalize(spec)
    issues = validate_spec(spec)
    if issues:
        raise SpecValidationError(issues)
    return spec


def _own_planner_fields(doctype: DoctypeSpec):
    return [f for f in doctype.fields if f.fieldname not in _INJECTED]


def validate_spec(spec: CrmSpec) -> list[SpecIssue]:
    issues: list[SpecIssue] = []

    def add(path: str, message: str) -> None:
        issues.append(SpecIssue(path, message))

    own = {d.name: d for d in spec.doctypes}
    own_parents = {d.name for d in spec.doctypes if not d.is_child_table}
    own_children = {d.name for d in spec.doctypes if d.is_child_table}
    declared_roles = {r.name for r in spec.roles}
    known_roles = declared_roles | set(catalog.STANDARD_ROLES)
    link_targets = own_parents | set(catalog.STANDARD_LINK_TARGETS)
    reachable_doctypes = own_parents | set(catalog.STANDARD_MODIFIABLE_DOCTYPES) | set(catalog.STANDARD_LINK_TARGETS)

    # --- budgets ------------------------------------------------------------
    if len(spec.doctypes) > catalog.MAX_DOCTYPES:
        add("doctypes", f"{len(spec.doctypes)} doctypes exceeds the limit of {catalog.MAX_DOCTYPES}; remove the least important")
    if len(spec.workflows) > catalog.MAX_WORKFLOWS:
        add("workflows", f"{len(spec.workflows)} workflows exceeds the limit of {catalog.MAX_WORKFLOWS}")
    if len(spec.seed_data) > catalog.MAX_SEED_ROWS:
        add("seed_data", f"{len(spec.seed_data)} seed rows exceeds the limit of {catalog.MAX_SEED_ROWS}")

    # --- roles --------------------------------------------------------------
    seen_roles: set[str] = set()
    for index, role in enumerate(spec.roles):
        if role.name in seen_roles:
            add(f"roles[{index}]", f"role {role.name!r} is declared twice")
        seen_roles.add(role.name)
        if role.name in catalog.STANDARD_ROLES:
            add(f"roles[{index}]", f"{role.name!r} already exists on every site; do not declare it")

    # --- doctypes -----------------------------------------------------------
    seen_doctypes: set[str] = set()
    referenced_children: set[str] = set()

    for d_index, doctype in enumerate(spec.doctypes):
        where = f"doctypes[{d_index}] ({doctype.name})"

        if doctype.name in seen_doctypes:
            add(where, "doctype name is used twice")
        seen_doctypes.add(doctype.name)
        if not _DOCTYPE_NAME.match(doctype.name):
            add(where, "name must start with a letter and contain only letters, digits, spaces, hyphens or underscores")
        if len(doctype.name) > catalog.MAX_DOCTYPE_NAME_LENGTH:
            add(where, f"name is longer than {catalog.MAX_DOCTYPE_NAME_LENGTH} characters (MariaDB table-name limit)")
        if doctype.name in catalog.STANDARD_LINK_TARGETS or doctype.name in catalog.STANDARD_MODIFIABLE_DOCTYPES:
            add(where, "name collides with a standard ERPNext doctype; choose a distinct name")

        planner_fields = _own_planner_fields(doctype)
        if len(planner_fields) > catalog.MAX_FIELDS_PER_DOCTYPE:
            add(where, f"{len(planner_fields)} fields exceeds the limit of {catalog.MAX_FIELDS_PER_DOCTYPE}")

        seen_fields: set[str] = set()
        for f_index, field in enumerate(doctype.fields):
            fpath = f"{where}.fields[{f_index}] ({field.label})"
            name = field.fieldname or ""

            if name in seen_fields:
                add(fpath, f"fieldname {name!r} is used twice in this doctype")
            seen_fields.add(name)

            if name in _INJECTED:
                expected_type, expected_options = _INJECTED[name]
                if field.fieldtype != expected_type or (expected_options and field.options != expected_options):
                    add(fpath, f"{name!r} is reserved for the automatic {name.replace('_', ' ')} field")
            else:
                if not _FIELDNAME.match(name) or len(name) > catalog.MAX_FIELDNAME_LENGTH:
                    add(fpath, f"fieldname {name!r} must be lowercase letters, digits and underscores, starting with a letter, up to {catalog.MAX_FIELDNAME_LENGTH} characters")
                if name in catalog.RESERVED_FIELDNAMES:
                    add(fpath, f"fieldname {name!r} is reserved by Frappe")

            _check_field_options(field, fpath, add, own_parents, own_children, link_targets, referenced_children)

        _check_doctype_settings(doctype, where, add, seen_fields, known_roles)

    for child in own_children:
        if child not in referenced_children:
            add(f"doctypes ({child})", "is a child table but no Table field references it; either reference it or remove it")

    _check_link_cycles(spec, own, add)

    # --- standard doctype mods ---------------------------------------------
    taken_custom: set[tuple[str, str]] = set()
    for index, entry in enumerate(spec.standard_doctype_mods.custom_fields):
        where = f"standard_doctype_mods.custom_fields[{index}] ({entry.doctype}.{entry.field.label})"
        field = entry.field
        name = field.fieldname or ""

        if entry.doctype not in catalog.STANDARD_MODIFIABLE_DOCTYPES:
            add(where, f"cannot add fields to {entry.doctype!r}; allowed: {', '.join(catalog.STANDARD_MODIFIABLE_DOCTYPES)}")
        if not name.startswith(catalog.CUSTOM_FIELD_PREFIX) or not _FIELDNAME.match(name):
            add(where, f"custom fieldname {name!r} must start with {catalog.CUSTOM_FIELD_PREFIX!r} and be lowercase letters, digits and underscores")
        if (entry.doctype, name) in taken_custom:
            add(where, f"custom field {name!r} is added to {entry.doctype} twice")
        taken_custom.add((entry.doctype, name))
        if field.fieldtype == "Table":
            add(where, "Table fields cannot be added to a standard doctype")
        _check_field_options(field, where, add, own_parents, own_children, link_targets, set())

    for index, setter in enumerate(spec.standard_doctype_mods.property_setters):
        where = f"standard_doctype_mods.property_setters[{index}] ({setter.doctype}.{setter.fieldname})"
        if setter.doctype not in catalog.STANDARD_MODIFIABLE_DOCTYPES:
            add(where, f"cannot change properties on {setter.doctype!r}")
        if catalog.PROPERTY_SETTER_TYPES.get(setter.property) == "Check" and setter.value not in ("0", "1"):
            add(where, f"{setter.property} takes '0' or '1', not {setter.value!r}")

    # --- workflows ----------------------------------------------------------
    workflow_targets: set[str] = set()
    for index, workflow in enumerate(spec.workflows):
        where = f"workflows[{index}] ({workflow.name})"
        if workflow.doctype not in own_parents and workflow.doctype not in catalog.STANDARD_MODIFIABLE_DOCTYPES:
            add(where, f"targets unknown or unsupported doctype {workflow.doctype!r}")
        if workflow.doctype in workflow_targets:
            add(where, f"{workflow.doctype!r} already has a workflow; Frappe allows one active workflow per doctype")
        workflow_targets.add(workflow.doctype)

        if not workflow.states or not workflow.transitions:
            add(where, "needs at least one state and one transition")

        state_names = [s.name for s in workflow.states]
        if len(state_names) != len(set(state_names)):
            add(where, "a state name is used twice")
        for state in workflow.states:
            if state.doc_status != 0:
                add(where, f"state {state.name!r}: submitted/cancelled states need a submittable doctype, which is not supported yet")
            if state.allow_edit_role not in known_roles:
                add(where, f"state {state.name!r}: unknown role {state.allow_edit_role!r}")
        for transition in workflow.transitions:
            for state_name in (transition.from_state, transition.to_state):
                if state_name not in state_names:
                    add(where, f"transition {transition.action!r} references unknown state {state_name!r}")
            if transition.allowed_role not in known_roles:
                add(where, f"transition {transition.action!r}: unknown role {transition.allowed_role!r}")

    # --- workspaces ---------------------------------------------------------
    for index, workspace in enumerate(spec.workspaces):
        where = f"workspaces[{index}] ({workspace.title})"
        for shortcut in workspace.shortcuts:
            if shortcut.doctype not in reachable_doctypes:
                add(where, f"shortcut {shortcut.label!r} points at unknown doctype {shortcut.doctype!r}")
        for card in workspace.number_cards:
            if card.doctype not in reachable_doctypes:
                add(where, f"number card {card.label!r} points at unknown doctype {card.doctype!r}")
            elif card.doctype in own:
                fields = {f.fieldname for f in own[card.doctype].fields}
                for key in card.filters:
                    if key not in fields:
                        add(where, f"number card {card.label!r} filters on unknown field {key!r} of {card.doctype}")

    # --- notifications ------------------------------------------------------
    for index, notification in enumerate(spec.notifications):
        where = f"notifications[{index}] ({notification.name})"
        if notification.doctype not in reachable_doctypes:
            add(where, f"targets unknown doctype {notification.doctype!r}")
        if not notification.recipient_roles:
            add(where, "needs at least one recipient role")
        for role in notification.recipient_roles:
            if role not in known_roles:
                add(where, f"unknown recipient role {role!r}")
        if notification.event == "Value Change":
            if not notification.value_changed:
                add(where, "event 'Value Change' needs value_changed (the field to watch)")
            elif notification.doctype in own and notification.value_changed not in {f.fieldname for f in own[notification.doctype].fields}:
                add(where, f"value_changed {notification.value_changed!r} is not a field of {notification.doctype}")

    # --- seed data ----------------------------------------------------------
    for index, row in enumerate(spec.seed_data):
        where = f"seed_data[{index}] ({row.doctype})"
        if row.doctype not in own_parents and row.doctype not in catalog.STANDARD_LINK_TARGETS:
            add(where, f"unknown doctype {row.doctype!r}")
        for key in row.match_on:
            if key not in row.values:
                add(where, f"match_on key {key!r} is not one of the row's values")
        if row.doctype in own:
            fields = {f.fieldname: f for f in own[row.doctype].fields}
            for key in row.values:
                if key not in fields:
                    add(where, f"value {key!r} is not a field of {row.doctype}")
            for name, field in fields.items():
                if field.reqd and name not in _INJECTED and name not in row.values:
                    add(where, f"required field {name!r} is missing; Frappe would reject the row")

    return issues


def _check_field_options(field, where, add, own_parents, own_children, link_targets, referenced_children) -> None:
    options = (field.options or "").strip()

    if field.fieldtype == "Select":
        choices = [c for c in options.splitlines() if c.strip()]
        if not choices and field.fieldname != "naming_series":
            add(where, "Select field needs options: one choice per line")
    elif field.fieldtype == "Link":
        if not options:
            add(where, "Link field needs options set to the doctype it links to")
        elif options in own_children:
            add(where, f"cannot Link to child table {options!r}; link to a parent doctype")
        elif options not in link_targets and options != "Workflow State":
            add(where, f"Link target {options!r} is neither defined in this spec nor an allowed standard doctype ({', '.join(catalog.STANDARD_LINK_TARGETS)})")
    elif field.fieldtype == "Table":
        if options not in own_children:
            add(where, f"Table field needs options set to a child-table doctype defined in this spec (is_child_table=true); {options!r} is not one")
        else:
            referenced_children.add(options)


def _check_doctype_settings(doctype, where, add, field_names, known_roles) -> None:
    if doctype.naming == "field":
        if not doctype.naming_field or doctype.naming_field not in field_names:
            add(where, "naming 'field' needs naming_field set to one of this doctype's fieldnames")
    if doctype.title_field and doctype.title_field not in field_names:
        add(where, f"title_field {doctype.title_field!r} is not a field of this doctype")
    for permission in doctype.permissions:
        if permission.role not in known_roles:
            add(where, f"permission references unknown role {permission.role!r}")


def _check_link_cycles(spec: CrmSpec, own: dict[str, DoctypeSpec], add) -> None:
    """Reject A -> B -> A. Frappe validates a Link target exists when the
    doctype is saved, so mutually dependent doctypes have no valid creation
    order. Self-links are fine -- the applier defers those fields."""
    edges: dict[str, set[str]] = {name: set() for name in own}
    for name, doctype in own.items():
        for field in doctype.fields:
            if field.fieldtype in catalog.DOCTYPE_REFERENCE_TYPES and field.options in own and field.options != name:
                edges[name].add(field.options)

    WHITE, GREY, BLACK = 0, 1, 2
    color = {name: WHITE for name in own}

    def visit(node: str, trail: list[str]) -> list[str] | None:
        color[node] = GREY
        for nxt in sorted(edges[node]):
            if color[nxt] == GREY:
                return trail + [node, nxt]
            if color[nxt] == WHITE:
                found = visit(nxt, trail + [node])
                if found:
                    return found
        color[node] = BLACK
        return None

    for name in own:
        if color[name] == WHITE:
            cycle = visit(name, [])
            if cycle:
                start = cycle.index(cycle[-1])
                add(
                    "doctypes",
                    "circular Link/Table references " + " -> ".join(cycle[start:])
                    + "; keep the reference on one side only",
                )
                return
