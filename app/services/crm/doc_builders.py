"""Pure translation from spec objects to Frappe document payloads.

No I/O and no Frappe client here, so the exact payload shapes are testable in
isolation and the applier stays about *ordering and reconciliation* rather
than about what a Workspace looks like.

Payloads omit the `doctype` key: the REST endpoint supplies it, and child
rows are typed by the table field they sit in.

Child-table rows are described by `CHILD_KEYS`, which the applier uses to
match desired rows to the rows already on a site by identity (not position).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from app.services.crm import catalog
from app.services.crm.spec_models import (
    DoctypeSpec,
    FieldSpec,
    NotificationSpec,
    NumberCardSpec,
    PropertySetterSpec,
    WorkflowSpec,
    WorkspaceSpec,
)

# doctype -> {child table fieldname -> identity of a row}. A row is "the same
# row" if this returns equal values, regardless of where it sits.
CHILD_KEYS: dict[str, dict[str, Callable[[dict], Any]]] = {
    "DocType": {
        "fields": lambda r: r.get("fieldname"),
        "permissions": lambda r: (r.get("role"), r.get("permlevel", 0)),
    },
    "Workflow": {
        "states": lambda r: r.get("state"),
        "transitions": lambda r: (r.get("state"), r.get("action"), r.get("next_state")),
    },
    "Workspace": {
        "shortcuts": lambda r: r.get("label"),
        "number_cards": lambda r: r.get("number_card_name"),
    },
    "Notification": {
        "recipients": lambda r: r.get("receiver_by_role"),
    },
}


def content_hash(doc: dict) -> str:
    """Stable fingerprint of a desired document, for the applier's
    'unchanged, skip it' short-circuit."""
    return hashlib.sha256(json.dumps(doc, sort_keys=True, default=str).encode()).hexdigest()


def _flag(value: bool) -> int:
    return 1 if value else 0


def field_row(field: FieldSpec, include_insert_after: bool = False) -> dict:
    row: dict[str, Any] = {
        "fieldname": field.fieldname,
        "label": field.label,
        "fieldtype": field.fieldtype,
        "reqd": _flag(field.reqd),
        "unique": _flag(field.unique),
        "in_list_view": _flag(field.in_list_view),
    }
    if field.options:
        row["options"] = field.options
    if field.default is not None:
        row["default"] = field.default
    if field.description:
        row["description"] = field.description
    if include_insert_after and field.insert_after:
        row["insert_after"] = field.insert_after
    return row


def _autoname(doctype: DoctypeSpec) -> str:
    if doctype.is_child_table:
        return "hash"
    return {
        "series": "naming_series:",
        "field": f"field:{doctype.naming_field}",
        "prompt": "Prompt",
        "random": "hash",
    }[doctype.naming]


def doctype_doc(doctype: DoctypeSpec, exclude_fieldnames: frozenset[str] = frozenset()) -> dict:
    """`exclude_fieldnames` lets the applier create a doctype without its
    self-referencing Link fields and add them in a second pass."""
    doc: dict[str, Any] = {
        "module": "Custom",
        # custom=1 is what makes this legal with developer_mode off.
        "custom": 1,
        "autoname": _autoname(doctype),
        "track_changes": 1,
        "fields": [field_row(f) for f in doctype.fields if f.fieldname not in exclude_fieldnames],
        "permissions": [
            {
                "role": p.role,
                "permlevel": 0,
                "read": _flag(p.read),
                "write": _flag(p.write),
                "create": _flag(p.create),
                "delete": _flag(p.delete),
                # Reading implies being able to report/export/print/share it;
                # exposing five more switches to a planner buys nothing.
                "report": _flag(p.read),
                "export": _flag(p.read),
                "print": _flag(p.read),
                "email": _flag(p.read),
                "share": _flag(p.read),
            }
            for p in doctype.permissions
        ],
    }
    if doctype.is_child_table:
        doc["istable"] = 1
        doc["editable_grid"] = 1
    if doctype.title_field:
        doc["title_field"] = doctype.title_field
    return doc


def custom_field_name(doctype: str, fieldname: str) -> str:
    """Frappe's own naming for a Custom Field, reproduced so the applier can
    look one up by a deterministic name instead of searching."""
    return f"{doctype}-{fieldname}"


def custom_field_doc(doctype: str, field: FieldSpec) -> dict:
    row = field_row(field, include_insert_after=True)
    return {"dt": doctype, **row}


def property_setter_name(setter: PropertySetterSpec) -> str:
    return f"{setter.doctype}-{setter.fieldname}-{setter.property}"


def property_setter_doc(setter: PropertySetterSpec) -> dict:
    return {
        "doc_type": setter.doctype,
        "doctype_or_field": "DocField",
        "field_name": setter.fieldname,
        "property": setter.property,
        "property_type": catalog.PROPERTY_SETTER_TYPES[setter.property],
        "value": setter.value,
    }


def role_doc(name: str) -> dict:
    return {"role_name": name, "desk_access": 1}


def workflow_state_doc(name: str) -> dict:
    return {"workflow_state_name": name}


def workflow_action_doc(name: str) -> dict:
    return {"workflow_action_name": name}


def workflow_doc(workflow: WorkflowSpec) -> dict:
    return {
        "workflow_name": workflow.name,
        "document_type": workflow.doctype,
        "is_active": 1,
        "workflow_state_field": "workflow_state",
        "send_email_alert": 0,
        "states": [
            {"state": s.name, "doc_status": str(s.doc_status), "allow_edit": s.allow_edit_role}
            for s in workflow.states
        ],
        "transitions": [
            {
                "state": t.from_state,
                "action": t.action,
                "next_state": t.to_state,
                "allowed": t.allowed_role,
                "allow_self_approval": 1,
            }
            for t in workflow.transitions
        ],
    }


def number_card_doc(card: NumberCardSpec) -> dict:
    filters = [[card.doctype, key, "=", value, False] for key, value in card.filters.items()]
    return {
        "label": card.label,
        "document_type": card.doctype,
        "type": "Document Type",
        "function": "Count",
        "is_public": 1,
        "show_percentage_stats": 0,
        "filters_json": json.dumps(filters),
    }


def _block_id(kind: str, label: str) -> str:
    return hashlib.sha1(f"{kind}:{label}".encode()).hexdigest()[:10]


def workspace_doc(workspace: WorkspaceSpec) -> dict:
    """A public Workspace. `content` is the JSON block layout the v15 desk
    actually renders -- the `shortcuts` / `number_cards` child tables alone
    define what exists but not what is drawn, so both must agree."""
    blocks = [
        {"id": _block_id("number_card", c.label), "type": "number_card", "data": {"number_card_name": c.label, "col": 4}}
        for c in workspace.number_cards
    ] + [
        {"id": _block_id("shortcut", s.label), "type": "shortcut", "data": {"shortcut_name": s.label, "col": 3}}
        for s in workspace.shortcuts
    ]
    return {
        "label": workspace.title,
        "title": workspace.title,
        "module": "Custom",
        "public": 1,
        "is_hidden": 0,
        "content": json.dumps(blocks),
        "shortcuts": [
            {"label": s.label, "type": "DocType", "link_to": s.doctype, "doc_view": "List"}
            for s in workspace.shortcuts
        ],
        "number_cards": [
            {"label": c.label, "number_card_name": c.label} for c in workspace.number_cards
        ],
    }


def notification_doc(notification: NotificationSpec) -> dict:
    doc: dict[str, Any] = {
        "subject": notification.subject,
        "document_type": notification.doctype,
        "event": notification.event,
        "channel": "Email",
        "enabled": 1,
        "message": notification.message or notification.subject,
        "recipients": [{"receiver_by_role": role} for role in notification.recipient_roles],
    }
    if notification.value_changed:
        doc["value_changed"] = notification.value_changed
    return doc
