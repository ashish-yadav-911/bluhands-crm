import pytest

from app.services.crm.spec_models import CrmSpec
from app.services.crm.spec_normalizer import normalize, slugify_fieldname
from app.services.crm.spec_validator import load_spec


def _doctype(spec, name):
    return next(d for d in spec.doctypes if d.name == name)


def _fields(spec, name):
    return {f.fieldname: f for f in _doctype(spec, name).fields}


@pytest.mark.parametrize(
    "label,expected",
    [
        ("Visit Date", "visit_date"),
        ("  Customer's  Name!! ", "customer_s_name"),
        ("2nd Contact", "f_2nd_contact"),
        ("!!!", "field"),
        ("A" * 80, "a" * 50),
    ],
)
def test_slugify_fieldname(label, expected):
    assert slugify_fieldname(label) == expected


def test_fieldnames_are_derived_from_labels(golden_spec):
    assert "visit_date" in _fields(golden_spec, "Service Visit")
    assert "annual_fee" in _fields(golden_spec, "Maintenance Contract")


def test_colliding_labels_get_distinct_fieldnames(golden_raw):
    fields = golden_raw["doctypes"][2]["fields"]
    fields += [{"label": "Site Name", "fieldtype": "Data"}, {"label": "Site  Name", "fieldtype": "Data"}]

    spec = load_spec(golden_raw)

    names = list(_fields(spec, "Maintenance Contract"))
    assert "site_name" in names and "site_name_2" in names


def test_a_label_that_slugifies_to_a_reserved_name_is_rescued(golden_raw):
    golden_raw["doctypes"][2]["fields"].append({"label": "Owner", "fieldtype": "Data"})

    spec = load_spec(golden_raw)

    assert "owner_value" in _fields(spec, "Maintenance Contract")


def test_naming_series_field_is_injected_first_with_the_declared_prefix(golden_spec):
    first = _doctype(golden_spec, "Service Visit").fields[0]

    assert first.fieldname == "naming_series"
    assert first.options == "SV-.YYYY.-.####"
    assert first.default == first.options
    assert first.reqd


def test_series_prefix_defaults_to_the_initials(golden_spec):
    assert _fields(golden_spec, "Maintenance Contract")["naming_series"].options.startswith("MC-")


def test_no_naming_series_on_child_tables_or_other_naming_rules(golden_spec):
    assert "naming_series" not in _fields(golden_spec, "Visit Part")


def test_workflow_state_field_is_injected_on_the_targeted_doctype_only(golden_spec):
    assert "workflow_state" in _fields(golden_spec, "Service Visit")
    assert "workflow_state" not in _fields(golden_spec, "Maintenance Contract")


def test_workflow_states_used_by_transitions_are_declared(golden_spec):
    states = {s.name for s in golden_spec.workflows[0].states}
    assert states == {"Scheduled", "In Progress", "Completed"}


def test_undeclared_custom_roles_are_declared_but_standard_ones_are_not(golden_spec):
    names = {r.name for r in golden_spec.roles}
    assert "Technician" in names          # used in permissions/workflow, never declared
    assert "Service Manager" in names
    assert "System Manager" not in names  # standard: exists on every site


def test_system_manager_permission_is_appended_to_parent_doctypes(golden_spec):
    for name in ("Service Visit", "Maintenance Contract"):
        roles = {p.role for p in _doctype(golden_spec, name).permissions}
        assert "System Manager" in roles


def test_system_manager_gets_full_permissions_and_child_tables_get_none(golden_spec):
    perm = next(p for p in _doctype(golden_spec, "Maintenance Contract").permissions if p.role == "System Manager")
    assert (perm.read, perm.write, perm.create, perm.delete) == (True, True, True, True)
    assert _doctype(golden_spec, "Visit Part").permissions == []


def test_custom_fields_on_standard_doctypes_get_the_prefix_and_are_chained(golden_spec):
    custom = [c.field for c in golden_spec.standard_doctype_mods.custom_fields]

    assert [f.fieldname for f in custom] == ["cf_preferred_contact_time", "cf_gate_code"]
    assert custom[0].insert_after is None
    assert custom[1].insert_after == "cf_preferred_contact_time"


def test_a_default_list_view_is_chosen_when_the_planner_chose_none(golden_spec):
    shown = [f.fieldname for f in _doctype(golden_spec, "Maintenance Contract").fields if f.in_list_view]
    assert shown and "naming_series" not in shown
    assert len(shown) <= 4


def test_a_workspace_is_created_when_the_planner_omitted_one(golden_raw):
    golden_raw["workspaces"] = []

    spec = load_spec(golden_raw)

    assert len(spec.workspaces) == 1
    shortcuts = {s.doctype for s in spec.workspaces[0].shortcuts}
    assert shortcuts == {"Service Visit", "Maintenance Contract"}  # never the child table


def test_normalize_is_idempotent(golden_raw):
    once = normalize(CrmSpec.model_validate(golden_raw))
    twice = normalize(once)

    assert once.model_dump() == twice.model_dump()


def test_normalize_does_not_mutate_its_input(golden_raw):
    spec = CrmSpec.model_validate(golden_raw)
    before = spec.model_dump()

    normalize(spec)

    assert spec.model_dump() == before
