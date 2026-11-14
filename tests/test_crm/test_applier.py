"""The applier against an in-memory site.

The fake enforces the Frappe rules the applier exists to satisfy (link targets
must exist, no repeated fieldnames, a PUT replaces child tables, workflows need
their states), so an ordering or reconciliation bug shows up here as a real
validation error rather than passing silently.
"""

import copy

import pytest

from app.services.crm.applier import CREATED, STAGES, UNCHANGED, UPDATED, Applier, InMemoryLedger, rollback
from app.services.crm.spec_validator import load_spec
from tests.support.fake_frappe import FakeSite


@pytest.fixture()
def site():
    return FakeSite()


def _apply(site, spec, ledger=None, **kwargs):
    return Applier(site, ledger or InMemoryLedger(), **kwargs).apply(spec)


def _inserted_order(site, doctype):
    return [name for _op, dt, name in site.writes_of("insert") if dt == doctype]


def test_golden_spec_applies_cleanly(site, golden_spec):
    result = _apply(site, golden_spec)

    assert result.status == "succeeded", [str(e) for e in result.errors]
    assert site.names("DocType") == sorted(
        {"Service Visit", "Visit Part", "Maintenance Contract"} | {n for (dt, n) in site.docs if dt == "DocType" and site.docs[(dt, n)].get("custom") == 0}
    )
    assert "Customer-cf_gate_code" in site.names("Custom Field")
    assert "Customer-customer_group-in_list_view" in site.names("Property Setter")
    assert site.names("Workflow") == ["Service Visit Flow"]
    assert site.names("Workspace") == ["Field Service"]
    assert site.names("Number Card") == ["Scheduled Visits"]
    assert site.names("Notification") == ["Visit Completed"]
    assert any(site.docs[("UOM", n)].get("uom_name") == "Visit Hour" for n in site.names("UOM"))


def test_custom_doctypes_are_created_as_custom(site, golden_spec):
    _apply(site, golden_spec)

    doc = site.doc("DocType", "Service Visit")
    assert doc["custom"] == 1
    assert doc["autoname"] == "naming_series:"
    assert site.doc("DocType", "Visit Part")["istable"] == 1


def test_child_tables_and_link_targets_are_created_before_their_parents(site, golden_spec):
    _apply(site, golden_spec)

    order = _inserted_order(site, "DocType")
    assert order.index("Visit Part") < order.index("Service Visit")


def test_stages_run_in_dependency_order(site, golden_spec):
    result = _apply(site, golden_spec)

    positions = [STAGES.index(r.stage) for r in result.records]
    assert positions == sorted(positions)
    # Number Card must precede the Workspace that shows it, or Frappe rejects the workspace.
    writes = [(dt, n) for _op, dt, n in site.writes_of("insert")]
    assert writes.index(("Number Card", "Scheduled Visits")) < writes.index(("Workspace", "Field Service"))


def test_workflow_masters_are_created_before_the_workflow(site, golden_spec):
    _apply(site, golden_spec)

    writes = [(dt, n) for _op, dt, n in site.writes_of("insert")]
    workflow = writes.index(("Workflow", "Service Visit Flow"))
    assert writes.index(("Workflow State", "In Progress")) < workflow
    assert writes.index(("Workflow Action Master", "Start Work")) < workflow


def test_existing_masters_are_left_alone(site, golden_spec):
    """Roles and workflow states may pre-exist and be shared; the applier must
    neither modify them nor claim to have created them."""
    site.docs[("Role", "Technician")] = {"name": "Technician", "role_name": "Technician", "marker": "keep"}

    result = _apply(site, golden_spec)

    assert site.doc("Role", "Technician")["marker"] == "keep"
    assert ("Role", "Technician", UNCHANGED) in {(r.doctype, r.name, r.action) for r in result.records}
    assert ("update", "Role", "Technician") not in site.writes


# --------------------------------------------------------------- idempotency


def test_second_run_issues_zero_writes(site, golden_spec):
    ledger = InMemoryLedger()
    _apply(site, golden_spec, ledger)
    writes_after_first = len(site.writes)

    second = _apply(site, golden_spec, ledger)

    assert second.status == "succeeded"
    assert len(site.writes) == writes_after_first
    assert second.writes == []


def test_reapplying_with_no_memory_of_the_first_run_does_not_duplicate_anything(site, golden_spec):
    """The ledger is a diffing aid, not the truth. A fresh ledger against a
    populated site must reconcile with what is already there, not stack a
    second copy of every field on top of it."""
    _apply(site, golden_spec, InMemoryLedger())

    second = _apply(site, golden_spec, InMemoryLedger())

    assert second.status == "succeeded", [str(e) for e in second.errors]
    fieldnames = [f["fieldname"] for f in site.doc("DocType", "Service Visit")["fields"]]
    assert len(fieldnames) == len(set(fieldnames))
    assert {r.action for r in second.records if r.doctype == "DocType"} == {UPDATED}


def test_reapplying_keeps_the_identity_of_rows_already_on_the_site(site, golden_spec):
    _apply(site, golden_spec, InMemoryLedger())
    before = {f["fieldname"]: f["name"] for f in site.doc("DocType", "Service Visit")["fields"]}

    _apply(site, golden_spec, InMemoryLedger())

    after = {f["fieldname"]: f["name"] for f in site.doc("DocType", "Service Visit")["fields"]}
    assert after == before


def test_an_object_the_ledger_remembers_but_the_site_lacks_is_created_again(site, golden_spec):
    ledger = InMemoryLedger()
    _apply(site, golden_spec, ledger)
    del site.docs[("Workspace", "Field Service")]

    result = _apply(site, golden_spec, ledger)

    assert ("Workspace", "Field Service", CREATED) in {(r.doctype, r.name, r.action) for r in result.records}
    assert ("Workspace", "Field Service") in site.docs


def test_a_changed_spec_updates_only_what_changed(site, golden_raw):
    ledger = InMemoryLedger()
    _apply(site, load_spec(golden_raw), ledger)

    changed = copy.deepcopy(golden_raw)
    next(d for d in changed["doctypes"] if d["name"] == "Maintenance Contract")["fields"].append(
        {"label": "Renewal Notes", "fieldtype": "Text"}
    )
    result = _apply(site, load_spec(changed), ledger)

    assert [(r.doctype, r.name) for r in result.writes] == [("DocType", "Maintenance Contract")]
    assert "renewal_notes" in [f["fieldname"] for f in site.doc("DocType", "Maintenance Contract")["fields"]]


def test_fields_removed_from_the_spec_are_kept_on_the_site(site, golden_raw):
    """Dropping a column destroys customer data. A re-apply never does it;
    that belongs to a reviewed change request."""
    ledger = InMemoryLedger()
    _apply(site, load_spec(golden_raw), ledger)

    trimmed = copy.deepcopy(golden_raw)
    contract = next(d for d in trimmed["doctypes"] if d["name"] == "Maintenance Contract")
    contract["fields"] = [f for f in contract["fields"] if f["label"] != "Annual Fee"]
    result = _apply(site, load_spec(trimmed), ledger)

    assert result.status == "succeeded"
    assert "annual_fee" in [f["fieldname"] for f in site.doc("DocType", "Maintenance Contract")["fields"]]


def test_a_doctype_linking_to_itself_is_created_in_two_passes(site, golden_raw):
    golden_raw["doctypes"].append(
        {
            "name": "Service Area",
            "fields": [
                {"label": "Area Name", "fieldtype": "Data", "reqd": True},
                {"label": "Parent Area", "fieldtype": "Link", "options": "Service Area"},
            ],
        }
    )

    result = _apply(site, load_spec(golden_raw))

    assert result.status == "succeeded", [str(e) for e in result.errors]
    assert "parent_area" in [f["fieldname"] for f in site.doc("DocType", "Service Area")["fields"]]
    # Inserted without the self-link, then updated to add it.
    assert ("update", "DocType", "Service Area") in site.writes


def test_refuses_to_modify_a_standard_doctype_of_the_same_name(site, golden_spec):
    site.docs[("DocType", "Maintenance Contract")] = {"name": "Maintenance Contract", "custom": 0, "fields": []}

    result = _apply(site, golden_spec)

    assert result.status == "failed"
    assert "standard DocType" in result.errors[0].message
    assert ("update", "DocType", "Maintenance Contract") not in site.writes


# ---------------------------------------------------------------- failure


def test_a_doctype_failure_aborts_everything_downstream(site, golden_spec):
    site.fail("insert", "DocType", "Service Visit")

    result = _apply(site, golden_spec)

    assert result.status == "failed"
    assert result.errors[0].stage == "doctypes"
    assert result.errors[0].name == "Service Visit"
    # Nothing that depends on the doctype is attempted.
    for doctype in ("Workflow", "Workspace", "Notification", "Custom Field"):
        assert site.names(doctype) == []


def test_a_cosmetic_failure_does_not_stop_independent_work(site, golden_spec):
    site.fail("insert", "Workspace", "Field Service")

    result = _apply(site, golden_spec)

    assert result.status == "partial"
    assert [e.stage for e in result.errors] == ["workspaces"]
    # The stages after it still ran.
    assert site.names("Notification") == ["Visit Completed"]
    assert any(r.stage == "seed" for r in result.records)


def test_a_failing_notification_does_not_block_seed_data(site, golden_spec):
    site.fail("insert", "Notification", "Visit Completed")

    result = _apply(site, golden_spec)

    assert result.status == "partial"
    assert any(r.doctype == "UOM" and r.action == CREATED for r in result.records)


def test_errors_carry_frappes_own_exception_type(site, golden_spec):
    site.fail("insert", "DocType", "Visit Part")

    result = _apply(site, golden_spec)

    assert result.errors[0].exc_type == "ValidationError"


@pytest.mark.parametrize(
    "site_kwargs,expected",
    [
        ({"reachable": False}, "did not answer ping"),
        ({"user": "Guest"}, "expected the Administrator"),
        ({"setup_complete": False}, "setup wizard"),
    ],
)
def test_preflight_failures_stop_before_any_write(golden_spec, site_kwargs, expected):
    site = FakeSite(**site_kwargs)

    result = _apply(site, golden_spec)

    assert result.status == "failed"
    assert result.errors[0].stage == "preflight"
    assert expected in result.errors[0].message
    assert site.writes == []


# ------------------------------------------------------------ cache + events


def test_cache_is_cleared_once_at_the_very_end(site, golden_spec):
    calls = []
    result = _apply(site, golden_spec, clear_cache=lambda: calls.append(len(site.writes)))

    assert result.status == "succeeded"
    assert calls == [len(site.writes)]  # after every write, exactly once


def test_a_failed_cache_clear_makes_the_result_partial(site, golden_spec):
    def boom():
        raise RuntimeError("bench unavailable")

    result = _apply(site, golden_spec, clear_cache=boom)

    assert result.status == "partial"
    assert result.errors[0].stage == "cache"


def test_progress_is_reported_per_stage_in_order(site, golden_spec):
    events = []

    _apply(site, golden_spec, on_progress=lambda stage, message: events.append((stage, message)))

    assert [s for s, m in events if m == "started"] == list(STAGES)
    assert events[0] == ("preflight", "started")
    assert events[-1] == ("cache", "finished")


# ------------------------------------------------------------------ rollback


def test_rollback_deletes_what_was_created_children_before_parents(site, golden_spec):
    result = _apply(site, golden_spec)

    errors = rollback(site, result.records)

    assert errors == []
    assert site.names("Workspace") == [] and site.names("Workflow") == []
    assert not any(site.docs[k].get("custom") == 1 for k in site.docs if k[0] == "DocType")
    # Reverse creation order: the parent goes before the child table it embeds.
    deletes = [n for _op, dt, n in site.writes_of("delete", "DocType")]
    assert deletes.index("Service Visit") < deletes.index("Visit Part")


def test_rollback_never_touches_updated_or_pre_existing_objects(site, golden_raw):
    ledger = InMemoryLedger()
    first = _apply(site, load_spec(golden_raw), ledger)
    assert first.status == "succeeded"

    changed = copy.deepcopy(golden_raw)
    next(d for d in changed["doctypes"] if d["name"] == "Maintenance Contract")["fields"].append(
        {"label": "Renewal Notes", "fieldtype": "Text"}
    )
    second = _apply(site, load_spec(changed), ledger)
    assert [r.action for r in second.writes] == [UPDATED]

    rollback(site, second.records)

    # The second run created nothing, so rolling it back deletes nothing:
    # the doctype it merely modified must survive.
    assert ("DocType", "Maintenance Contract") in site.docs
    assert site.writes_of("delete") == []


def test_rollback_does_not_delete_a_role_that_pre_existed(site, golden_spec):
    site.docs[("Role", "Technician")] = {"name": "Technician", "role_name": "Technician"}
    result = _apply(site, golden_spec)

    rollback(site, result.records)

    assert ("Role", "Technician") in site.docs
    assert ("Role", "Service Manager") not in site.docs  # created by us, so removed


def test_rollback_tolerates_objects_that_are_already_gone(site, golden_spec):
    result = _apply(site, golden_spec)
    del site.docs[("Workspace", "Field Service")]

    assert rollback(site, result.records) == []


def test_rollback_reports_what_it_could_not_delete(site, golden_spec):
    result = _apply(site, golden_spec)
    site.fail("delete", "Workflow", "Service Visit Flow")

    errors = rollback(site, result.records)

    assert [(e.doctype, e.name) for e in errors] == [("Workflow", "Service Visit Flow")]
    assert ("Workflow", "Service Visit Flow") in site.docs
    assert ("Workspace", "Field Service") not in site.docs  # the rest still went
