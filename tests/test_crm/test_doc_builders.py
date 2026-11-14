"""The exact payloads sent to Frappe. Pure functions, so pinned directly."""

import json

from app.services.crm import doc_builders as build


def _doctype(spec, name):
    return next(d for d in spec.doctypes if d.name == name)


def test_a_parent_doctype_is_custom_and_uses_its_naming_series(golden_spec):
    doc = build.doctype_doc(_doctype(golden_spec, "Service Visit"))

    assert doc["custom"] == 1 and doc["module"] == "Custom"
    assert doc["autoname"] == "naming_series:"
    assert "istable" not in doc


def test_a_child_table_is_marked_istable_and_hash_named(golden_spec):
    doc = build.doctype_doc(_doctype(golden_spec, "Visit Part"))

    assert doc["istable"] == 1 and doc["editable_grid"] == 1
    assert doc["autoname"] == "hash"
    assert doc["permissions"] == []


def test_field_rows_carry_flags_as_integers_and_omit_unset_options(golden_spec):
    rows = {r["fieldname"]: r for r in build.doctype_doc(_doctype(golden_spec, "Service Visit"))["fields"]}

    assert rows["customer"]["reqd"] == 1 and rows["customer"]["options"] == "Customer"
    assert rows["notes"]["reqd"] == 0 and "options" not in rows["notes"]
    assert rows["status"]["default"] == "Scheduled"


def test_permissions_imply_the_read_related_rights(golden_spec):
    perms = {p["role"]: p for p in build.doctype_doc(_doctype(golden_spec, "Service Visit"))["permissions"]}

    assert perms["Technician"]["read"] == 1 and perms["Technician"]["create"] == 0
    assert perms["Technician"]["export"] == 1 and perms["Technician"]["print"] == 1
    assert perms["Service Manager"]["delete"] == 1


def test_excluded_fields_are_left_out_for_the_first_pass(golden_spec):
    doc = build.doctype_doc(_doctype(golden_spec, "Service Visit"), exclude_fieldnames=frozenset({"notes"}))

    assert "notes" not in {r["fieldname"] for r in doc["fields"]}


def test_custom_field_names_follow_frappes_convention(golden_spec):
    entry = golden_spec.standard_doctype_mods.custom_fields[1]

    assert build.custom_field_name(entry.doctype, entry.field.fieldname) == "Customer-cf_gate_code"
    doc = build.custom_field_doc(entry.doctype, entry.field)
    assert doc["dt"] == "Customer" and doc["insert_after"] == "cf_preferred_contact_time"


def test_property_setters_carry_the_right_property_type(golden_spec):
    setter = golden_spec.standard_doctype_mods.property_setters[0]

    doc = build.property_setter_doc(setter)
    assert build.property_setter_name(setter) == "Customer-customer_group-in_list_view"
    assert doc["property_type"] == "Check" and doc["doctype_or_field"] == "DocField"


def test_workflow_doc_uses_frappes_field_names_and_string_doc_status(golden_spec):
    doc = build.workflow_doc(golden_spec.workflows[0])

    assert doc["document_type"] == "Service Visit" and doc["is_active"] == 1
    assert {s["state"] for s in doc["states"]} == {"Scheduled", "In Progress", "Completed"}
    assert all(isinstance(s["doc_status"], str) for s in doc["states"])
    start = next(t for t in doc["transitions"] if t["action"] == "Start Work")
    assert (start["state"], start["next_state"], start["allowed"]) == ("Scheduled", "In Progress", "Technician")


def test_number_card_filters_are_serialised_for_frappe(golden_spec):
    doc = build.number_card_doc(golden_spec.workspaces[0].number_cards[0])

    assert doc["function"] == "Count" and doc["type"] == "Document Type"
    assert json.loads(doc["filters_json"]) == [["Service Visit", "status", "=", "Scheduled", False]]


def test_workspace_content_blocks_agree_with_its_child_tables(golden_spec):
    doc = build.workspace_doc(golden_spec.workspaces[0])
    blocks = json.loads(doc["content"])

    drawn = {b["data"].get("shortcut_name") or b["data"].get("number_card_name") for b in blocks}
    defined = {s["label"] for s in doc["shortcuts"]} | {c["number_card_name"] for c in doc["number_cards"]}
    assert drawn == defined
    assert len({b["id"] for b in blocks}) == len(blocks)
    assert doc["public"] == 1


def test_workspace_block_ids_are_deterministic(golden_spec):
    first = json.loads(build.workspace_doc(golden_spec.workspaces[0])["content"])
    second = json.loads(build.workspace_doc(golden_spec.workspaces[0])["content"])

    assert first == second  # otherwise every re-apply looks like a change


def test_notification_doc(golden_spec):
    doc = build.notification_doc(golden_spec.notifications[0])

    assert doc["event"] == "Value Change" and doc["value_changed"] == "status"
    assert doc["recipients"] == [{"receiver_by_role": "Service Manager"}]


def test_content_hash_is_stable_and_order_independent():
    assert build.content_hash({"a": 1, "b": [1, 2]}) == build.content_hash({"b": [1, 2], "a": 1})
    assert build.content_hash({"a": 1}) != build.content_hash({"a": 2})
