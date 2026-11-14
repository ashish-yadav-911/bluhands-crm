"""Deterministically fills in everything a planner should not have to get right.

This is the biggest reliability lever in the pipeline. Every rule moved here
is a class of planner retry that never has to be paid for: fieldnames from
labels, insert_after chains, naming-series boilerplate, the workflow_state
field, the System Manager permission, roles a spec uses but did not declare,
a sidebar entry so the customer can actually reach what was built.

Pure and idempotent: `normalize(normalize(spec)) == normalize(spec)`. It
never raises on a malformed-but-parseable spec -- judging that is
`spec_validator`'s job, and it runs afterwards, so the normalizer must be
tolerant of what it is about to be checked on.
"""

from __future__ import annotations

import re

from app.services.crm import catalog
from app.services.crm.spec_models import (
    CrmSpec,
    DoctypeSpec,
    FieldSpec,
    PermissionSpec,
    RoleSpec,
    WorkflowStateSpec,
    WorkspaceShortcutSpec,
    WorkspaceSpec,
)

_LIST_VIEW_TYPES = {"Data", "Select", "Link", "Date", "Datetime", "Int", "Currency", "Phone", "Check"}
_MAX_LIST_VIEW_COLUMNS = 4
_INJECTED_FIELDNAMES = {"naming_series"}


def slugify_fieldname(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    if not slug:
        slug = "field"
    if slug[0].isdigit():
        slug = f"f_{slug}"
    return slug[: catalog.MAX_FIELDNAME_LENGTH].rstrip("_")


def _unique(name: str, taken: set[str]) -> str:
    """Suffix rather than fail: two labels slugifying alike is the normalizer's
    problem to resolve, not the planner's."""
    candidate, counter = name, 2
    while candidate in taken:
        suffix = f"_{counter}"
        candidate = f"{name[: catalog.MAX_FIELDNAME_LENGTH - len(suffix)]}{suffix}"
        counter += 1
    taken.add(candidate)
    return candidate


def _series_prefix(doctype: DoctypeSpec) -> str:
    if doctype.series_prefix:
        cleaned = re.sub(r"[^A-Za-z0-9]", "", doctype.series_prefix).upper()
        if cleaned:
            return cleaned[:6]
    words = re.findall(r"[A-Za-z0-9]+", doctype.name)
    initials = "".join(word[0] for word in words).upper()
    return (initials if len(initials) >= 2 else "".join(words).upper()[:3]) or "DOC"


def _fill_own_doctype_fields(doctype: DoctypeSpec) -> None:
    taken = {f.fieldname for f in doctype.fields if f.fieldname}
    for field in doctype.fields:
        if field.fieldname:
            continue
        slug = slugify_fieldname(field.label)
        if slug in catalog.RESERVED_FIELDNAMES or slug in _INJECTED_FIELDNAMES:
            slug = f"{slug}_value"
        field.fieldname = _unique(slug, taken)


def _inject_naming_series(doctype: DoctypeSpec) -> None:
    if doctype.is_child_table or doctype.naming != "series":
        return
    if any(f.fieldname == "naming_series" for f in doctype.fields):
        return
    series = f"{_series_prefix(doctype)}-.YYYY.-.####"
    doctype.fields.insert(
        0,
        FieldSpec(
            label="Series",
            fieldname="naming_series",
            fieldtype="Select",
            options=series,
            default=series,
            reqd=True,
        ),
    )


def _default_list_view(doctype: DoctypeSpec) -> None:
    if doctype.is_child_table or any(f.in_list_view for f in doctype.fields):
        return
    shown = 0
    for field in doctype.fields:
        if field.fieldname == "naming_series" or field.fieldtype not in _LIST_VIEW_TYPES:
            continue
        field.in_list_view = True
        shown += 1
        if shown >= _MAX_LIST_VIEW_COLUMNS:
            break


def _ensure_system_manager(doctype: DoctypeSpec) -> None:
    if doctype.is_child_table:
        return
    if any(p.role == "System Manager" for p in doctype.permissions):
        return
    doctype.permissions.append(PermissionSpec(role="System Manager", read=True, write=True, create=True, delete=True))


def _normalize_custom_fields(spec: CrmSpec) -> None:
    taken_by_doctype: dict[str, set[str]] = {}
    last_by_doctype: dict[str, str] = {}

    for entry in spec.standard_doctype_mods.custom_fields:
        field = entry.field
        taken = taken_by_doctype.setdefault(entry.doctype, set())

        if not field.fieldname:
            base = slugify_fieldname(field.label)
            field.fieldname = base
        if not field.fieldname.startswith(catalog.CUSTOM_FIELD_PREFIX):
            field.fieldname = f"{catalog.CUSTOM_FIELD_PREFIX}{field.fieldname}"
        field.fieldname = field.fieldname[: catalog.MAX_FIELDNAME_LENGTH]
        field.fieldname = _unique(field.fieldname, taken)

        # Chain each custom field after the previous one on the same doctype,
        # so they keep the order the planner wrote them in instead of all
        # piling up at the end of the form in arbitrary order.
        if not field.insert_after and entry.doctype in last_by_doctype:
            field.insert_after = last_by_doctype[entry.doctype]
        last_by_doctype[entry.doctype] = field.fieldname


def _normalize_workflows(spec: CrmSpec) -> None:
    own = {d.name: d for d in spec.doctypes}

    for workflow in spec.workflows:
        declared = {s.name for s in workflow.states}
        for transition in workflow.transitions:
            for state_name in (transition.from_state, transition.to_state):
                if state_name not in declared:
                    workflow.states.append(WorkflowStateSpec(name=state_name))
                    declared.add(state_name)

        target = own.get(workflow.doctype)
        if target is not None and not any(f.fieldname == "workflow_state" for f in target.fields):
            # Frappe would add this itself on first workflow save, but doing
            # it up front means the column exists before anything references
            # it and the doctype's own field list matches the site.
            target.fields.append(
                FieldSpec(
                    label="Workflow State",
                    fieldname="workflow_state",
                    fieldtype="Link",
                    options="Workflow State",
                )
            )


def _referenced_roles(spec: CrmSpec) -> list[str]:
    roles: list[str] = []
    for doctype in spec.doctypes:
        roles += [p.role for p in doctype.permissions]
    for workflow in spec.workflows:
        roles += [s.allow_edit_role for s in workflow.states]
        roles += [t.allowed_role for t in workflow.transitions]
    for notification in spec.notifications:
        roles += notification.recipient_roles
    return roles


def _declare_missing_roles(spec: CrmSpec) -> None:
    declared = {r.name for r in spec.roles}
    for role in _referenced_roles(spec):
        if role in catalog.STANDARD_ROLES or role in declared:
            continue
        spec.roles.append(RoleSpec(name=role))
        declared.add(role)


def _ensure_workspace(spec: CrmSpec) -> None:
    """A CRM whose doctypes are not on any sidebar is unreachable in practice."""
    if spec.workspaces:
        return
    reachable = [d for d in spec.doctypes if not d.is_child_table]
    if not reachable:
        return
    spec.workspaces.append(
        WorkspaceSpec(
            title=spec.meta.name,
            shortcuts=[WorkspaceShortcutSpec(label=d.name, doctype=d.name) for d in reachable],
        )
    )


def normalize(spec: CrmSpec) -> CrmSpec:
    result = spec.model_copy(deep=True)

    for doctype in result.doctypes:
        _fill_own_doctype_fields(doctype)
        _inject_naming_series(doctype)
        _default_list_view(doctype)
        _ensure_system_manager(doctype)

    _normalize_custom_fields(result)
    _normalize_workflows(result)
    _declare_missing_roles(result)
    _ensure_workspace(result)
    return result
