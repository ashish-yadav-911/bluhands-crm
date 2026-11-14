import pytest

from app.services.crm.applier import Applier
from app.services.crm.verifier import Verifier
from tests.support.fake_frappe import FakeSite


@pytest.fixture()
def applied(golden_spec):
    site = FakeSite()
    assert Applier(site).apply(golden_spec).ok
    return site


def _failed(report):
    return {c.name for c in report.failures}


def test_a_correctly_applied_spec_passes_every_check(applied, golden_spec):
    report = Verifier(applied).verify(golden_spec)

    assert report.ok, [(c.name, c.detail) for c in report.failures]
    assert len(report.checks) > 15  # doctypes x3 checks, mods, workflow, workspace, ...


def test_a_missing_doctype_fails(applied, golden_spec):
    del applied.docs[("DocType", "Maintenance Contract")]

    report = Verifier(applied).verify(golden_spec)

    assert "doctype-exists:Maintenance Contract" in _failed(report)
    assert not report.ok


def test_a_doctype_that_saved_but_cannot_be_listed_fails(applied, golden_spec):
    """The nastiest silent failure: metadata exists, the table does not."""
    applied.list_failures.add("Service Visit")

    report = Verifier(applied).verify(golden_spec)

    assert _failed(report) == {"listable:Service Visit"}


def test_a_form_that_will_not_render_fails(applied, golden_spec):
    applied.form_failures.add("Maintenance Contract")

    report = Verifier(applied).verify(golden_spec)

    assert _failed(report) == {"form-renders:Maintenance Contract"}


def test_child_tables_are_not_required_to_be_listable(applied, golden_spec):
    """A child table cannot be listed over REST; demanding it would fail every
    spec that has one."""
    report = Verifier(applied).verify(golden_spec)

    assert "listable:Visit Part" not in {c.name for c in report.checks}
    assert "form-renders:Visit Part" in {c.name for c in report.checks}


def test_a_field_missing_from_the_site_fails(applied, golden_spec):
    doc = applied.docs[("DocType", "Service Visit")]
    doc["fields"] = [f for f in doc["fields"] if f["fieldname"] != "visit_date"]

    report = Verifier(applied).verify(golden_spec)

    failure = next(c for c in report.failures if c.name == "doctype-exists:Service Visit")
    assert "visit_date" in failure.detail


def test_a_doctype_that_is_not_custom_fails(applied, golden_spec):
    applied.docs[("DocType", "Service Visit")]["custom"] = 0

    assert "doctype-exists:Service Visit" in _failed(Verifier(applied).verify(golden_spec))


@pytest.mark.parametrize(
    "key,check",
    [
        (("Custom Field", "Customer-cf_gate_code"), "custom-field:Customer-cf_gate_code"),
        (("Property Setter", "Customer-customer_group-in_list_view"), "property-setter:Customer-customer_group-in_list_view"),
        (("Role", "Technician"), "role:Technician"),
    ],
)
def test_each_missing_essential_object_blocks_delivery(applied, golden_spec, key, check):
    del applied.docs[key]

    report = Verifier(applied).verify(golden_spec)

    assert check in _failed(report)
    assert not report.ok


@pytest.mark.parametrize(
    "key,check",
    [
        (("Workspace", "Field Service"), "workspace:Field Service"),
        (("Number Card", "Scheduled Visits"), "number-card:Scheduled Visits"),
        (("Notification", "Visit Completed"), "notification:Visit Completed"),
    ],
)
def test_a_missing_cosmetic_object_warns_but_does_not_block_delivery(applied, golden_spec, key, check):
    """A CRM missing one chart is deliverable; the customer is just told."""
    del applied.docs[key]

    report = Verifier(applied).verify(golden_spec)

    assert report.ok
    assert check in {c.name for c in report.warnings}


def test_an_inactive_workflow_fails(applied, golden_spec):
    applied.docs[("Workflow", "Service Visit Flow")]["is_active"] = 0

    report = Verifier(applied).verify(golden_spec)

    assert "workflow:Service Visit Flow" in _failed(report)


def test_a_missing_seed_row_warns(applied, golden_spec):
    for key in [k for k in applied.docs if k[0] == "UOM" and applied.docs[k].get("uom_name") == "Visit Hour"]:
        del applied.docs[key]

    report = Verifier(applied).verify(golden_spec)

    assert report.ok
    assert any(c.name.startswith("seed:UOM") for c in report.warnings)


def test_a_site_that_stops_answering_fails(applied, golden_spec):
    applied.reachable = False

    assert "bench-responds" in _failed(Verifier(applied).verify(golden_spec))


def test_server_errors_logged_during_the_apply_fail_the_run(applied, golden_spec):
    applied.add_error_log(creation="2026-09-24 10:00:05", method="frappe.model.document.save")

    report = Verifier(applied, error_log_watermark="2026-09-24 10:00:00").verify(golden_spec)

    failure = next(c for c in report.failures if c.name == "error-log-clean")
    assert "frappe.model.document.save" in failure.detail


def test_errors_from_before_the_apply_are_ignored(applied, golden_spec):
    applied.add_error_log(creation="2026-09-24 09:00:00")

    assert Verifier(applied, error_log_watermark="2026-09-24 10:00:00").verify(golden_spec).ok


def test_the_watermark_is_the_newest_error_already_on_the_site(applied):
    applied.add_error_log(creation="2026-09-20 08:00:00")
    applied.add_error_log(creation="2026-09-22 09:30:00")

    assert Verifier.error_log_watermark(applied) == "2026-09-22 09:30:00"


def test_the_watermark_of_a_clean_site_admits_every_later_error(applied):
    from app.services.crm.verifier import EPOCH

    assert Verifier.error_log_watermark(applied) == EPOCH


def test_the_error_log_check_is_skipped_without_a_watermark(applied, golden_spec):
    applied.add_error_log(creation="2026-09-24 10:00:05")

    report = Verifier(applied).verify(golden_spec)

    assert "error-log-clean" not in {c.name for c in report.checks}


def test_a_broken_site_yields_a_report_not_a_crash(golden_spec):
    """Every check is contained: a site in a bad state is the case the
    verifier exists for, so it must not be the case that breaks it."""

    class Exploding(FakeSite):
        def get_doc(self, doctype, name):
            raise RuntimeError("connection reset")

    report = Verifier(Exploding()).verify(golden_spec)

    assert not report.ok
    assert all(c.detail for c in report.failures)
    assert any("connection reset" in c.detail for c in report.failures)


def test_the_verifier_never_writes(applied, golden_spec):
    before = len(applied.writes)

    Verifier(applied, error_log_watermark="2026-09-24 10:00:00").verify(golden_spec)

    assert len(applied.writes) == before
