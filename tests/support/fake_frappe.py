"""An in-memory stand-in for one Frappe site, speaking FrappeClient's interface.

Built before the applier because it is what makes the whole applier
unit-testable with no Docker and no network. It is not a Frappe clone: it
emulates only the behaviours the applier's correctness depends on, each of
which is a real Frappe rule the applier has to work around --

  * a Link/Table field's target DocType must exist when the doctype is saved
  * a DocType with a repeated fieldname is rejected
  * a PUT replaces each child table it mentions (Document.update() assigns
    the list): rows the payload omits are deleted, and a row keeps its
    identity only if the payload carries its `name`
  * Workflow needs its Workflow States, Action Masters and roles to exist
  * Workspace needs its Number Cards to exist
  * inserting a duplicate name is a DuplicateEntryError

Every write is recorded in `writes`, and `failures` injects errors, so tests
can assert "the second run issues zero writes" and "a failing stage aborts".
"""

from __future__ import annotations

import copy
import itertools

from app.services.frappe.rest_client import FrappeApiError

_CHILD_TABLES = {
    "DocType": ("fields", "permissions"),
    "Workflow": ("states", "transitions"),
    "Workspace": ("shortcuts", "number_cards"),
    "Notification": ("recipients",),
}

STANDARD_DOCTYPES = (
    "Address", "Company", "Contact", "Country", "Currency", "Customer", "Customer Group", "Employee",
    "Item", "Item Group", "Lead", "Opportunity", "Project", "Supplier", "Task", "Territory", "UOM", "User",
    "Role", "Workflow State", "Workflow Action Master", "Workflow", "Workspace", "Number Card",
    "Notification", "Custom Field", "Property Setter",
)
STANDARD_ROLES = ("System Manager", "Sales User", "Sales Manager", "Employee", "All")
STANDARD_WORKFLOW_STATES = ("Pending", "Approved", "Rejected")


def _error(status: int, exc_type: str, message: str) -> FrappeApiError:
    return FrappeApiError(status, exc_type, [message], message)


class FakeSite:
    def __init__(self, setup_complete: bool = True, user: str = "Administrator", reachable: bool = True):
        self.docs: dict[tuple[str, str], dict] = {}
        self.writes: list[tuple[str, str, str]] = []
        # (op, doctype, name-or-None) -> exception to raise. name None = any.
        self.failures: dict[tuple[str, str, str | None], Exception] = {}
        self.error_logs: list[dict] = []
        self.list_failures: set[str] = set()
        self.form_failures: set[str] = set()
        self.setup_complete = setup_complete
        self.user = user
        self.reachable = reachable
        self._ids = itertools.count(1)

        for doctype in STANDARD_DOCTYPES:
            self.docs[("DocType", doctype)] = {"name": doctype, "custom": 0, "module": "Core", "fields": []}
        for role in STANDARD_ROLES:
            self.docs[("Role", role)] = {"name": role, "role_name": role}
        for state in STANDARD_WORKFLOW_STATES:
            self.docs[("Workflow State", state)] = {"name": state, "workflow_state_name": state}

    # ----------------------------------------------------------- inspection

    def names(self, doctype: str) -> list[str]:
        return sorted(n for (dt, n) in self.docs if dt == doctype)

    def doc(self, doctype: str, name: str) -> dict:
        return copy.deepcopy(self.docs[(doctype, name)])

    def writes_of(self, op: str | None = None, doctype: str | None = None) -> list[tuple[str, str, str]]:
        return [w for w in self.writes if (op is None or w[0] == op) and (doctype is None or w[1] == doctype)]

    def fail(self, op: str, doctype: str, name: str | None = None, exc: Exception | None = None) -> None:
        self.failures[(op, doctype, name)] = exc or _error(417, "ValidationError", f"injected {op} failure")

    def _maybe_fail(self, op: str, doctype: str, name: str | None) -> None:
        for key in ((op, doctype, name), (op, doctype, None)):
            if key in self.failures:
                raise self.failures[key]

    # --------------------------------------------------------------- client

    def ping(self) -> bool:
        return self.reachable

    def logged_user(self) -> str:
        return self.user

    def get_doc(self, doctype: str, name: str) -> dict | None:
        doc = self.docs.get((doctype, name))
        return copy.deepcopy(doc) if doc is not None else None

    def exists(self, doctype: str, name: str) -> bool:
        return (doctype, name) in self.docs

    def get_list(self, doctype, filters=None, fields=None, limit=20, order_by=None) -> list[dict]:
        if doctype in self.list_failures:
            raise _error(500, "ProgrammingError", f"Table 'tab{doctype}' doesn't exist")
        rows = []
        for (dt, name), doc in self.docs.items():
            if dt != doctype:
                continue
            if all(self._matches(doc, f) for f in (filters or [])):
                rows.append({"name": name, **({k: doc.get(k) for k in fields} if fields else {})})
        if order_by and order_by.endswith(" desc"):
            rows.sort(key=lambda r: str(self.docs[(doctype, r["name"])].get(order_by[:-5], "")), reverse=True)
        return rows[:limit]

    @staticmethod
    def _matches(doc: dict, condition: list) -> bool:
        _doctype, field, op, value = condition[:4]
        if op == ">":
            return str(doc.get(field, "")) > str(value)
        return doc.get(field) == value

    def add_error_log(self, creation: str, method: str = "boom") -> None:
        name = f"err-{next(self._ids)}"
        self.docs[("Error Log", name)] = {"name": name, "creation": creation, "method": method}

    def insert(self, doctype: str, doc: dict) -> dict:
        name = doc.get("name") or f"{doctype}-{next(self._ids):05d}"
        self._maybe_fail("insert", doctype, name)
        if (doctype, name) in self.docs:
            raise _error(409, "DuplicateEntryError", f"{doctype} {name} already exists")

        stored = self._with_child_names(doctype, {**copy.deepcopy(doc), "name": name})
        self._validate(doctype, stored)
        self.docs[(doctype, name)] = stored
        self.writes.append(("insert", doctype, name))
        return copy.deepcopy(stored)

    def update(self, doctype: str, name: str, doc: dict) -> dict:
        self._maybe_fail("update", doctype, name)
        current = self.docs.get((doctype, name))
        if current is None:
            raise _error(404, "DoesNotExistError", f"{doctype} {name} not found")

        merged = {**current, **{k: v for k, v in copy.deepcopy(doc).items() if k not in _CHILD_TABLES.get(doctype, ())}}
        for table in _CHILD_TABLES.get(doctype, ()):
            if table not in doc:
                continue
            merged[table] = self._merge_rows(current.get(table, []), copy.deepcopy(doc[table]))

        self._validate(doctype, merged)
        self.docs[(doctype, name)] = merged
        self.writes.append(("update", doctype, name))
        return copy.deepcopy(merged)

    def delete(self, doctype: str, name: str) -> None:
        self._maybe_fail("delete", doctype, name)
        if (doctype, name) not in self.docs:
            raise _error(404, "DoesNotExistError", f"{doctype} {name} not found")
        del self.docs[(doctype, name)]
        self.writes.append(("delete", doctype, name))

    def call_method(self, dotted_path: str, params: dict | None = None, http_method: str = "GET"):
        params = params or {}
        if dotted_path == "frappe.client.get_value":
            return {"message": {params["fieldname"]: 1 if self.setup_complete else 0}}
        if dotted_path == "frappe.desk.form.load.getdoctype":
            name = params["doctype"]
            if name in self.form_failures or (("DocType", name) not in self.docs):
                raise _error(500, "ValidationError", f"cannot render form for {name}")
            return {"docs": [self.doc("DocType", name)]}
        if dotted_path == "frappe.client.get_count":
            return {"message": len([1 for (dt, _n) in self.docs if dt == params["doctype"]])}
        raise _error(404, "NotFound", f"unknown method {dotted_path}")

    # ------------------------------------------------------------ behaviours

    def _with_child_names(self, doctype: str, doc: dict) -> dict:
        for table in _CHILD_TABLES.get(doctype, ()):
            for row in doc.get(table, []):
                row.setdefault("name", f"row-{next(self._ids):06d}")
        return doc

    def _merge_rows(self, existing: list[dict], incoming: list[dict]) -> list[dict]:
        """Frappe's rule: the payload *is* the new table. A row carrying an
        existing `name` keeps that identity; one without gets a fresh name;
        any existing row the payload omits is deleted."""
        by_name = {row["name"]: row for row in existing}
        rows = []
        for row in incoming:
            if row.get("name") in by_name:
                rows.append({**by_name[row["name"]], **row})
            else:
                rows.append({**row, "name": row.get("name") or f"row-{next(self._ids):06d}"})
        return rows

    def _require(self, condition: bool, message: str) -> None:
        if not condition:
            raise _error(417, "ValidationError", message)

    def _validate(self, doctype: str, doc: dict) -> None:
        validate = getattr(self, f"_validate_{doctype.lower().replace(' ', '_')}", None)
        if validate:
            validate(doc)

    def _validate_doctype(self, doc: dict) -> None:
        seen: set[str] = set()
        for field in doc.get("fields", []):
            name = field.get("fieldname")
            self._require(name not in seen, f"Fieldname {name} appears multiple times in {doc['name']}")
            seen.add(name)
            target = field.get("options")
            if field.get("fieldtype") == "Link" and target != doc["name"]:
                self._require(("DocType", target) in self.docs, f"Options must be a valid DocType for field {name}: {target}")
            if field.get("fieldtype") == "Table":
                child = self.docs.get(("DocType", target))
                self._require(child is not None and child.get("istable"), f"Table field {name} needs a child DocType, got {target}")
            if field.get("fieldtype") == "Link" and target == doc["name"]:
                # Self-links are only legal once the doctype exists.
                self._require(("DocType", doc["name"]) in self.docs, f"Options must be a valid DocType for field {name}")
        for perm in doc.get("permissions", []):
            self._require(("Role", perm["role"]) in self.docs, f"Role {perm['role']} not found")

    def _validate_workflow(self, doc: dict) -> None:
        for state in doc.get("states", []):
            self._require(("Workflow State", state["state"]) in self.docs, f"Workflow State {state['state']} not found")
            self._require(("Role", state["allow_edit"]) in self.docs, f"Role {state['allow_edit']} not found")
        for transition in doc.get("transitions", []):
            self._require(("Workflow Action Master", transition["action"]) in self.docs, f"Workflow Action {transition['action']} not found")
            self._require(("Role", transition["allowed"]) in self.docs, f"Role {transition['allowed']} not found")

    def _validate_workspace(self, doc: dict) -> None:
        for card in doc.get("number_cards", []):
            self._require(("Number Card", card["number_card_name"]) in self.docs, f"Number Card {card['number_card_name']} not found")

    def _validate_custom_field(self, doc: dict) -> None:
        self._require(("DocType", doc.get("dt")) in self.docs, f"DocType {doc.get('dt')} not found")

    def _validate_property_setter(self, doc: dict) -> None:
        self._require(("DocType", doc.get("doc_type")) in self.docs, f"DocType {doc.get('doc_type')} not found")

    def _validate_notification(self, doc: dict) -> None:
        self._require(("DocType", doc.get("document_type")) in self.docs, f"DocType {doc.get('document_type')} not found")
        for row in doc.get("recipients", []):
            self._require(("Role", row["receiver_by_role"]) in self.docs, f"Role {row['receiver_by_role']} not found")
