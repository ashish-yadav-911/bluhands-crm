"""Applies a validated CRM spec to a tenant's site over Frappe's REST API.

Deterministic: the same spec against the same site yields the same site. The
planner proposes; nothing here ever consults it, and (enforced by
test_architecture) nothing here may import it.

Stage order is dependency order, and each stage sits where it does for a
reason:

    preflight -> roles -> doctypes (child tables and link targets first)
    -> custom fields -> property setters -> workflows (Workflow State and
    Workflow Action Master before Workflow) -> workspaces (Number Card before
    the Workspace that shows it) -> notifications -> seed -> clear cache

Skipping the final cache clear serves stale doctype meta and the customer
sees a half-built CRM.

Idempotency, three layers: deterministic natural keys (look up, then PUT or
POST); a content-hash short-circuit that skips an object whose desired state
has not changed since the ledger last recorded it; and the ledger itself.
The ledger is a record and a diffing aid -- the *site* is the source of truth,
so an object the ledger remembers but the site lacks is simply created again.

Partial failure: fail fast in the stages everything else depends on (a
missing DocType makes the rest meaningless) and keep going in cosmetic ones
(a CRM missing one number card is still deliverable), ending `partial`.

Updates never remove anything: rows present on the site but absent from the
spec are kept. Deleting is destructive and belongs to a reviewed change
request, not to a re-apply.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from app.services.crm import doc_builders as build
from app.services.crm.spec_models import CrmSpec, DoctypeSpec
from app.services.frappe.rest_client import FrappeApiError

logger = logging.getLogger(__name__)

# Stages whose failure makes everything downstream meaningless.
CRITICAL_STAGES = ("preflight", "roles", "doctypes", "custom_fields", "property_setters", "workflows")
STAGES = (
    "preflight",
    "roles",
    "doctypes",
    "custom_fields",
    "property_setters",
    "workflows",
    "workspaces",
    "notifications",
    "seed",
    "cache",
)

CREATED, UPDATED, UNCHANGED = "created", "updated", "unchanged"


class SiteClient(Protocol):
    """The slice of FrappeClient the applier uses. A Protocol so tests supply
    an in-memory fake with no Docker and no network."""

    def ping(self) -> bool: ...
    def logged_user(self) -> str: ...
    def get_doc(self, doctype: str, name: str) -> dict | None: ...
    def get_list(self, doctype: str, filters: list | None = None, fields: list[str] | None = None, limit: int = 20, order_by: str | None = None) -> list[dict]: ...
    def insert(self, doctype: str, doc: dict) -> dict: ...
    def update(self, doctype: str, name: str, doc: dict) -> dict: ...
    def delete(self, doctype: str, name: str) -> None: ...
    def call_method(self, dotted_path: str, params: dict | None = None, http_method: str = "GET") -> Any: ...


class Ledger(Protocol):
    def get_hash(self, doctype: str, name: str) -> str | None: ...
    def set_hash(self, doctype: str, name: str, content_hash: str) -> None: ...


class InMemoryLedger:
    def __init__(self) -> None:
        self._hashes: dict[tuple[str, str], str] = {}

    def get_hash(self, doctype: str, name: str) -> str | None:
        return self._hashes.get((doctype, name))

    def set_hash(self, doctype: str, name: str, content_hash: str) -> None:
        self._hashes[(doctype, name)] = content_hash


@dataclass(frozen=True)
class AppliedRecord:
    doctype: str
    name: str
    action: str  # created | updated | unchanged
    stage: str

    def as_dict(self) -> dict:
        return {"doctype": self.doctype, "name": self.name, "action": self.action, "stage": self.stage}


@dataclass(frozen=True)
class StageError:
    stage: str
    doctype: str
    name: str
    message: str
    exc_type: str = ""

    def as_dict(self) -> dict:
        return {
            "stage": self.stage,
            "doctype": self.doctype,
            "name": self.name,
            "message": self.message,
            "exc_type": self.exc_type,
        }

    def __str__(self) -> str:
        return f"[{self.stage}] {self.doctype} {self.name!r}: {self.message}"


@dataclass
class ApplyResult:
    status: str = "succeeded"  # succeeded | partial | failed
    records: list[AppliedRecord] = field(default_factory=list)
    errors: list[StageError] = field(default_factory=list)

    @property
    def writes(self) -> list[AppliedRecord]:
        return [r for r in self.records if r.action != UNCHANGED]

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


class _Abort(Exception):
    """Raised internally to stop after a critical-stage failure."""


class Applier:
    def __init__(
        self,
        client: SiteClient,
        ledger: Ledger | None = None,
        clear_cache: Callable[[], None] | None = None,
        on_progress: Callable[[str, str], None] | None = None,
    ):
        self.client = client
        self.ledger = ledger or InMemoryLedger()
        self._clear_cache = clear_cache
        self._on_progress = on_progress or (lambda stage, message: None)
        self._result = ApplyResult()
        self._stage = ""

    # ------------------------------------------------------------------ run

    def apply(self, spec: CrmSpec) -> ApplyResult:
        self._result = ApplyResult()
        stages: list[tuple[str, Callable[[CrmSpec], None]]] = [
            ("preflight", self._preflight),
            ("roles", self._roles),
            ("doctypes", self._doctypes),
            ("custom_fields", self._custom_fields),
            ("property_setters", self._property_setters),
            ("workflows", self._workflows),
            ("workspaces", self._workspaces),
            ("notifications", self._notifications),
            ("seed", self._seed),
            ("cache", self._cache),
        ]
        try:
            for name, run in stages:
                self._stage = name
                self._on_progress(name, "started")
                run(spec)
                self._on_progress(name, "finished")
        except _Abort:
            self._result.status = "failed"
            return self._result

        self._result.status = "partial" if self._result.errors else "succeeded"
        return self._result

    # ------------------------------------------------------------ plumbing

    def _record(self, doctype: str, name: str, action: str) -> None:
        self._result.records.append(AppliedRecord(doctype, name, action, self._stage))

    def _fail(self, doctype: str, name: str, exc: Exception) -> None:
        exc_type = exc.exc_type if isinstance(exc, FrappeApiError) else type(exc).__name__
        error = StageError(self._stage, doctype, name, str(exc), exc_type)
        self._result.errors.append(error)
        logger.warning("apply failed: %s", error)
        if self._stage in CRITICAL_STAGES:
            raise _Abort() from exc

    def _guard(self, doctype: str, name: str, fn: Callable[[], None]) -> None:
        """Run one object's work. A failure is recorded; in a critical stage
        it also aborts the whole apply, in a cosmetic one it moves on."""
        try:
            fn()
        except _Abort:
            raise
        except Exception as exc:  # noqa: BLE001 - one object must not take down independent ones
            self._fail(doctype, name, exc)

    def _merge_children(self, doctype: str, existing: dict, desired: dict) -> dict:
        """Build the PUT payload for an existing document.

        Frappe applies a PUT with `doc.update(data)`, and assigning a list to
        a table field *replaces* the table: any row the payload omits is
        deleted. Two consequences drive this method:

        * Rows on the site that the spec does not mention are re-sent, not
          dropped -- otherwise a re-apply would silently delete fields (and
          their data) that were added by hand or by an earlier spec.
        * Rows the spec does mention are matched to the site's by identity
          and keep their existing `name`, so a re-apply does not churn every
          row's identity and version history.
        """
        merged = dict(desired)
        for table, key in build.CHILD_KEYS.get(doctype, {}).items():
            if table not in desired:
                continue
            existing_rows = list(existing.get(table) or [])
            by_key = {key(row): row for row in existing_rows}
            matched: set[Any] = set()
            rows: list[dict] = []
            for row in desired[table]:
                identity = key(row)
                current = by_key.get(identity)
                rows.append({**row, "name": current["name"]} if current and "name" in current else dict(row))
                matched.add(identity)
            rows += [row for row in existing_rows if key(row) not in matched]
            merged[table] = rows
        return merged

    def _apply_object(self, doctype: str, name: str, doc: dict) -> str:
        """Create-or-update by natural key, skipping if nothing changed."""
        desired_hash = build.content_hash(doc)
        existing = self.client.get_doc(doctype, name)

        if existing is None:
            self.client.insert(doctype, {**doc, "name": name})
            action = CREATED
        elif self.ledger.get_hash(doctype, name) == desired_hash:
            action = UNCHANGED
        else:
            self.client.update(doctype, name, self._merge_children(doctype, existing, doc))
            action = UPDATED

        self.ledger.set_hash(doctype, name, desired_hash)
        self._record(doctype, name, action)
        return action

    def _ensure_exists(self, doctype: str, name: str, doc: dict) -> None:
        """Create only if missing, never touch what is there.

        For master data the spec merely *depends on* -- a role, a workflow
        state named "Approved" -- which may pre-exist, may be shared with
        other tenants' specs, and must never be modified or later rolled back
        by us unless we were the ones who made it.
        """
        if self.client.get_doc(doctype, name) is not None:
            self._record(doctype, name, UNCHANGED)
            return
        self.client.insert(doctype, {**doc, "name": name})
        self._record(doctype, name, CREATED)

    # -------------------------------------------------------------- stages

    def _preflight(self, spec: CrmSpec) -> None:
        try:
            if not self.client.ping():
                raise RuntimeError("site did not answer ping")
            user = self.client.logged_user()
            if user != "Administrator":
                raise RuntimeError(f"authenticated as {user!r}, expected the Administrator API user")
            setup = self.client.call_method(
                "frappe.client.get_value", {"doctype": "System Settings", "fieldname": "setup_complete"}
            )
            if str(((setup or {}).get("message") or {}).get("setup_complete", "0")) != "1":
                raise RuntimeError("the setup wizard has not been completed on this site")
        except Exception as exc:  # noqa: BLE001
            self._fail("Site", "preflight", exc)

    def _roles(self, spec: CrmSpec) -> None:
        for role in spec.roles:
            self._guard("Role", role.name, lambda role=role: self._ensure_exists("Role", role.name, build.role_doc(role.name)))

    def _doctypes(self, spec: CrmSpec) -> None:
        for doctype in _creation_order(spec.doctypes):
            self._guard("DocType", doctype.name, lambda d=doctype: self._apply_doctype(d))

    def _apply_doctype(self, doctype: DoctypeSpec) -> None:
        existing = self.client.get_doc("DocType", doctype.name)
        if existing is not None and not existing.get("custom"):
            raise RuntimeError(
                f"a standard DocType named {doctype.name!r} already exists on this site; "
                "refusing to modify it. Rename the doctype in the spec."
            )

        # Frappe checks a Link's target when the doctype is saved, and the
        # doctype does not exist yet while it is being created -- so a Link
        # to itself is added in a second pass.
        self_refs = frozenset(
            f.fieldname
            for f in doctype.fields
            if f.fieldtype in ("Link", "Table") and f.options == doctype.name
        )
        full = build.doctype_doc(doctype)
        if self_refs and existing is None:
            base = build.doctype_doc(doctype, exclude_fieldnames=self_refs)
            self._apply_doctype_two_pass(doctype.name, base, full)
        else:
            self._apply_object("DocType", doctype.name, full)

    def _apply_doctype_two_pass(self, name: str, base: dict, full: dict) -> None:
        self.client.insert("DocType", {**base, "name": name})
        created = self.client.get_doc("DocType", name) or {}
        self.client.update("DocType", name, self._merge_children("DocType", created, full))
        self.ledger.set_hash("DocType", name, build.content_hash(full))
        self._record("DocType", name, CREATED)

    def _custom_fields(self, spec: CrmSpec) -> None:
        for entry in spec.standard_doctype_mods.custom_fields:
            name = build.custom_field_name(entry.doctype, entry.field.fieldname)
            doc = build.custom_field_doc(entry.doctype, entry.field)
            self._guard("Custom Field", name, lambda n=name, d=doc: self._apply_object("Custom Field", n, d))

    def _property_setters(self, spec: CrmSpec) -> None:
        for setter in spec.standard_doctype_mods.property_setters:
            name = build.property_setter_name(setter)
            doc = build.property_setter_doc(setter)
            self._guard("Property Setter", name, lambda n=name, d=doc: self._apply_object("Property Setter", n, d))

    def _workflows(self, spec: CrmSpec) -> None:
        for workflow in spec.workflows:
            for state in workflow.states:
                self._guard(
                    "Workflow State",
                    state.name,
                    lambda s=state: self._ensure_exists("Workflow State", s.name, build.workflow_state_doc(s.name)),
                )
            for action in sorted({t.action for t in workflow.transitions}):
                self._guard(
                    "Workflow Action Master",
                    action,
                    lambda a=action: self._ensure_exists("Workflow Action Master", a, build.workflow_action_doc(a)),
                )
            self._guard(
                "Workflow",
                workflow.name,
                lambda w=workflow: self._apply_object("Workflow", w.name, build.workflow_doc(w)),
            )

    def _workspaces(self, spec: CrmSpec) -> None:
        for workspace in spec.workspaces:
            for card in workspace.number_cards:
                self._guard(
                    "Number Card",
                    card.label,
                    lambda c=card: self._apply_object("Number Card", c.label, build.number_card_doc(c)),
                )
            self._guard(
                "Workspace",
                workspace.title,
                lambda w=workspace: self._apply_object("Workspace", w.title, build.workspace_doc(w)),
            )

    def _notifications(self, spec: CrmSpec) -> None:
        for notification in spec.notifications:
            self._guard(
                "Notification",
                notification.name,
                lambda n=notification: self._apply_object("Notification", n.name, build.notification_doc(n)),
            )

    def _seed(self, spec: CrmSpec) -> None:
        for row in spec.seed_data:
            label = ", ".join(f"{k}={row.values[k]}" for k in row.match_on)
            self._guard(row.doctype, label, lambda r=row: self._apply_seed_row(r))

    def _apply_seed_row(self, row) -> None:
        filters = [[row.doctype, key, "=", row.values[key]] for key in row.match_on]
        found = self.client.get_list(row.doctype, filters=filters, fields=["name"], limit=1)
        if found:
            self._record(row.doctype, found[0]["name"], UNCHANGED)
            return
        created = self.client.insert(row.doctype, dict(row.values))
        self._record(row.doctype, created.get("name", ""), CREATED)

    def _cache(self, spec: CrmSpec) -> None:
        if self._clear_cache is None:
            return
        self._guard("Cache", "clear-cache", self._clear_cache)


def _creation_order(doctypes: list[DoctypeSpec]) -> list[DoctypeSpec]:
    """Dependencies first: a doctype after every in-spec doctype it links to
    or embeds. Stable, so ties keep the order the spec was written in.
    Cycles are rejected by the validator, so this always terminates."""
    by_name = {d.name: d for d in doctypes}
    deps = {
        d.name: {
            f.options
            for f in d.fields
            if f.fieldtype in ("Link", "Table") and f.options in by_name and f.options != d.name
        }
        for d in doctypes
    }
    ordered: list[DoctypeSpec] = []
    placed: set[str] = set()
    while len(ordered) < len(doctypes):
        progressed = False
        for doctype in doctypes:
            if doctype.name in placed or not deps[doctype.name] <= placed:
                continue
            ordered.append(doctype)
            placed.add(doctype.name)
            progressed = True
        if not progressed:  # unreachable for a validated spec; never loop forever
            ordered += [d for d in doctypes if d.name not in placed]
            break
    return ordered


def rollback(client: SiteClient, records: list[AppliedRecord]) -> list[StageError]:
    """Undo what one apply run *created*, in reverse order.

    Only `created` records are deleted. `updated` ones are never touched:
    the applier merely modified something that was already there, and
    deleting it would destroy state that pre-dates this run. `unchanged`
    ones were never ours. A missing object is fine -- it is already gone.

    This is object-level cleanup for a failed change against a live tenant.
    A failed *initial* build does not use it: the site is seconds old and is
    dropped whole, which is cleaner and cheaper.
    """
    errors: list[StageError] = []
    for record in reversed(records):
        if record.action != CREATED:
            continue
        try:
            client.delete(record.doctype, record.name)
        except FrappeApiError as exc:
            if exc.status_code == 404 or exc.exc_type == "DoesNotExistError":
                continue
            errors.append(StageError("rollback", record.doctype, record.name, str(exc), exc.exc_type))
        except Exception as exc:  # noqa: BLE001
            errors.append(StageError("rollback", record.doctype, record.name, str(exc), type(exc).__name__))
    return errors
