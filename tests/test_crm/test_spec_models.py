"""The contract with the planner: what a spec may and may not say.

Table-driven on purpose. Each row is one thing an LLM has plausibly gotten
wrong, mutated onto the golden spec, with the message the planner would be
retried on. Adding a rule means adding a row here.
"""

import pytest

from app.services.crm.spec_validator import SpecValidationError, load_spec


def _doctype(raw, name):
    return next(d for d in raw["doctypes"] if d["name"] == name)


def _field(raw, doctype, label):
    return next(f for f in _doctype(raw, doctype)["fields"] if f["label"] == label)


def _many_fields(raw):
    _doctype(raw, "Service Visit")["fields"] += [
        {"label": f"Extra {i}", "fieldtype": "Data"} for i in range(31)
    ]


def _cycle(raw):
    # Contract -> Service Visit -> Contract
    _doctype(raw, "Maintenance Contract")["fields"].append(
        {"label": "Last Visit", "fieldtype": "Link", "options": "Service Visit"}
    )
    _doctype(raw, "Service Visit")["fields"].append(
        {"label": "Contract", "fieldtype": "Link", "options": "Maintenance Contract"}
    )


# (id, mutate(raw), expected substring of the error)
INVALID_SPECS = [
    ("unknown-top-level-key", lambda r: r.update({"server_scripts": []}), "server_scripts"),
    ("bad-fieldtype", lambda r: _field(r, "Service Visit", "Notes").update({"fieldtype": "Button"}), "fieldtype"),
    ("link-to-unknown-doctype", lambda r: _field(r, "Service Visit", "Customer").update({"options": "Custmer"}), "Custmer"),
    ("link-without-options", lambda r: _field(r, "Service Visit", "Customer").pop("options"), "needs options"),
    ("link-to-child-table", lambda r: _field(r, "Maintenance Contract", "Customer").update({"fieldtype": "Link", "options": "Visit Part"}), "child table"),
    ("select-without-options", lambda r: _field(r, "Service Visit", "Status").pop("options"), "Select field needs options"),
    ("table-to-non-child", lambda r: _field(r, "Service Visit", "Parts Used").update({"options": "Maintenance Contract"}), "child-table doctype"),
    ("orphan-child-table", lambda r: _doctype(r, "Service Visit")["fields"].remove(_field(r, "Service Visit", "Parts Used")), "no Table field references it"),
    ("reserved-fieldname", lambda r: _field(r, "Service Visit", "Notes").update({"fieldname": "owner"}), "reserved by Frappe"),
    ("bad-fieldname-chars", lambda r: _field(r, "Service Visit", "Notes").update({"fieldname": "Visit Notes!"}), "lowercase letters"),
    # Both explicit: a label-derived name that clashes is quietly suffixed by the
    # normalizer, so only two *planner-chosen* identical names can reach the validator.
    ("duplicate-fieldname", lambda r: (_field(r, "Service Visit", "Notes").update({"fieldname": "customer"}), _field(r, "Service Visit", "Customer").update({"fieldname": "customer"})), "used twice"),
    ("duplicate-doctype", lambda r: r["doctypes"].append(dict(_doctype(r, "Maintenance Contract"))), "used twice"),
    ("doctype-name-too-long", lambda r: _doctype(r, "Maintenance Contract").update({"name": "A" * 62}), "longer than 61"),
    ("doctype-name-bad-chars", lambda r: _doctype(r, "Maintenance Contract").update({"name": "Contract/2024"}), "must start with a letter"),
    ("doctype-collides-with-standard", lambda r: _doctype(r, "Maintenance Contract").update({"name": "Customer"}), "collides with a standard"),
    ("too-many-doctypes", lambda r: r["doctypes"].extend({"name": f"Extra Type {i}", "fields": []} for i in range(12)), "exceeds the limit of 12"),
    ("too-many-fields", _many_fields, "exceeds the limit of 30"),
    ("circular-links", _cycle, "circular"),
    ("naming-field-missing", lambda r: _doctype(r, "Maintenance Contract").update({"naming": "field"}), "naming_field"),
    ("title-field-unknown", lambda r: _doctype(r, "Maintenance Contract").update({"title_field": "nope"}), "title_field"),
    ("custom-field-on-unsupported-doctype", lambda r: r["standard_doctype_mods"]["custom_fields"][0].update({"doctype": "Sales Invoice"}), "cannot add fields"),
    ("custom-field-table", lambda r: r["standard_doctype_mods"]["custom_fields"][0]["field"].update({"fieldtype": "Table", "options": "Visit Part"}), "Table fields cannot be added"),
    ("property-setter-bad-boolean", lambda r: r["standard_doctype_mods"]["property_setters"][0].update({"value": "yes"}), "'0' or '1'"),
    ("property-setter-bad-doctype", lambda r: r["standard_doctype_mods"]["property_setters"][0].update({"doctype": "Sales Invoice"}), "cannot change properties"),
    ("workflow-unknown-doctype", lambda r: r["workflows"][0].update({"doctype": "Nope"}), "unknown or unsupported doctype"),
    ("workflow-duplicate-per-doctype", lambda r: r["workflows"].append({**r["workflows"][0], "name": "Second"}), "already has a workflow"),
    ("workflow-without-transitions", lambda r: r["workflows"][0].update({"transitions": []}), "at least one state and one transition"),
    ("workflow-submitted-state", lambda r: r["workflows"][0]["states"][0].update({"doc_status": 1}), "submittable doctype"),
    ("workspace-unknown-doctype", lambda r: r["workspaces"][0]["shortcuts"][0].update({"doctype": "Nope"}), "unknown doctype"),
    ("number-card-unknown-field", lambda r: r["workspaces"][0]["number_cards"][0].update({"filters": {"nope": "x"}}), "unknown field"),
    ("notification-no-recipients", lambda r: r["notifications"][0].update({"recipient_roles": []}), "at least one recipient"),
    ("notification-value-change-unknown-field", lambda r: r["notifications"][0].update({"value_changed": "nope"}), "not a field"),
    ("notification-value-change-missing-field", lambda r: r["notifications"][0].pop("value_changed"), "needs value_changed"),
    ("seed-match-on-not-in-values", lambda r: r["seed_data"][0].update({"match_on": ["nope"]}), "match_on"),
    ("seed-unknown-doctype", lambda r: r["seed_data"][0].update({"doctype": "Nope"}), "unknown doctype"),
    ("seed-too-many", lambda r: r["seed_data"].extend({"doctype": "UOM", "values": {"uom_name": f"U{i}"}, "match_on": ["uom_name"]} for i in range(26)), "exceeds the limit of 25"),
    ("seed-missing-required-field", lambda r: r["seed_data"].append({"doctype": "Maintenance Contract", "values": {"customer": "X"}, "match_on": ["customer"]}), "required field"),
    ("declares-a-standard-role", lambda r: r["roles"].append({"name": "Sales User"}), "already exists on every site"),
]


def test_golden_spec_is_valid(golden_raw):
    spec = load_spec(golden_raw)
    assert {d.name for d in spec.doctypes} == {"Service Visit", "Visit Part", "Maintenance Contract"}


@pytest.mark.parametrize("mutate,expected", [(m, e) for _id, m, e in INVALID_SPECS], ids=[i for i, _m, _e in INVALID_SPECS])
def test_invalid_spec_is_rejected_with_an_actionable_message(golden_raw, mutate, expected):
    mutate(golden_raw)

    with pytest.raises(SpecValidationError) as excinfo:
        load_spec(golden_raw)

    assert expected in str(excinfo.value)


def test_all_problems_are_reported_together(golden_raw):
    """A retry loop that surfaces one error per attempt is a slow retry loop."""
    _field(golden_raw, "Service Visit", "Notes").update({"fieldname": "owner"})
    _field(golden_raw, "Service Visit", "Customer").update({"options": "Custmer"})

    with pytest.raises(SpecValidationError) as excinfo:
        load_spec(golden_raw)

    assert len(excinfo.value.issues) >= 2


def test_the_error_names_where_the_problem_is(golden_raw):
    _field(golden_raw, "Service Visit", "Customer").update({"options": "Custmer"})

    with pytest.raises(SpecValidationError) as excinfo:
        load_spec(golden_raw)

    assert "Service Visit" in excinfo.value.issues[0].path
    assert "Customer" in excinfo.value.issues[0].path


def test_json_schema_is_generated_from_the_models():
    from app.services.crm.spec_models import spec_json_schema

    schema = spec_json_schema()
    assert "doctypes" in schema["properties"]
    # No server_scripts section exists at all in v1.
    assert "server_scripts" not in schema["properties"]
