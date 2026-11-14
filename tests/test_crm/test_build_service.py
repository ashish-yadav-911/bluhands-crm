import pytest

from app.models.crm import AppliedObject, ApplyRun, CrmSpecRecord
from app.services.crm.applier import AppliedRecord, rollback
from app.services.crm.build_service import MissingCredentialsError, apply_spec_to_tenant
from app.services.crm.spec_models import CrmSpec
from app.services.crm.spec_validator import SpecValidationError
from tests.support.fake_frappe import FakeSite


def test_a_good_spec_is_applied_verified_and_recorded(db_session, tenant, golden_raw):
    site = FakeSite()

    outcome = apply_spec_to_tenant(db_session, tenant, golden_raw, client=site)

    assert outcome.deliverable
    assert outcome.result.status == "succeeded"
    assert outcome.run.status == "succeeded"
    assert outcome.spec_record.status == "applied"
    assert outcome.spec_record.version == 1
    assert outcome.run.finished_at is not None
    assert any(r["doctype"] == "DocType" and r["action"] == "created" for r in outcome.run.records)
    assert outcome.run.verification and all(c["status"] != "fail" for c in outcome.run.verification)


def test_the_stored_spec_is_the_normalized_one(db_session, tenant, golden_raw):
    outcome = apply_spec_to_tenant(db_session, tenant, golden_raw, client=FakeSite())

    stored = CrmSpec.model_validate(outcome.spec_record.spec)
    fields = {f.fieldname for d in stored.doctypes if d.name == "Service Visit" for f in d.fields}
    assert "naming_series" in fields and "visit_date" in fields  # filled in by the normalizer


def test_a_bad_spec_is_rejected_before_any_write_or_request(db_session, tenant, golden_raw):
    golden_raw["doctypes"][0]["fields"][0]["options"] = "Custmer"
    site = FakeSite()

    with pytest.raises(SpecValidationError):
        apply_spec_to_tenant(db_session, tenant, golden_raw, client=site)

    assert site.writes == []
    assert db_session.query(CrmSpecRecord).count() == 0
    assert db_session.query(ApplyRun).count() == 0


def test_versions_are_numbered_per_tenant(db_session, tenant, golden_raw):
    site = FakeSite()

    apply_spec_to_tenant(db_session, tenant, golden_raw, client=site)
    second = apply_spec_to_tenant(db_session, tenant, golden_raw, client=site)

    assert second.spec_record.version == 2


def test_the_ledger_is_persisted_so_a_rerun_writes_nothing(db_session, tenant, golden_raw):
    site = FakeSite()
    apply_spec_to_tenant(db_session, tenant, golden_raw, client=site)
    assert db_session.query(AppliedObject).count() > 0
    writes_before = len(site.writes)

    second = apply_spec_to_tenant(db_session, tenant, golden_raw, client=site)

    assert len(site.writes) == writes_before
    assert second.deliverable and second.result.writes == []


def test_a_failed_apply_is_recorded_and_not_deliverable(db_session, tenant, golden_raw):
    site = FakeSite()
    site.fail("insert", "DocType", "Service Visit")

    outcome = apply_spec_to_tenant(db_session, tenant, golden_raw, client=site)

    assert not outcome.deliverable
    assert outcome.report is None  # nothing to verify on an aborted apply
    assert outcome.run.status == "failed"
    assert outcome.spec_record.status == "failed"
    assert outcome.run.errors[0]["stage"] == "doctypes"


def test_a_cosmetic_failure_is_partial_yet_still_deliverable_with_warnings(db_session, tenant, golden_raw):
    site = FakeSite()
    site.fail("insert", "Workspace", "Field Service")

    outcome = apply_spec_to_tenant(db_session, tenant, golden_raw, client=site)

    assert outcome.result.status == "partial"
    assert outcome.deliverable
    assert outcome.spec_record.status == "partial"
    assert any(c["status"] == "warn" for c in outcome.run.verification)


def test_an_apply_that_passes_but_verifies_badly_is_not_deliverable(db_session, tenant, golden_raw):
    """Applied without an error is not the same as working."""
    site = FakeSite()
    site.list_failures.add("Service Visit")

    outcome = apply_spec_to_tenant(db_session, tenant, golden_raw, client=site)

    assert outcome.result.status == "succeeded"
    assert not outcome.deliverable
    assert outcome.run.status == "failed"


def test_errors_logged_by_the_site_during_the_apply_block_delivery(db_session, tenant, golden_raw):
    site = FakeSite()
    site.add_error_log(creation="2026-01-01 00:00:00", method="old news")
    real_insert = site.insert

    def insert_and_log(doctype, doc):
        result = real_insert(doctype, doc)
        if doctype == "Workspace":
            site.add_error_log(creation="2026-09-24 10:00:00", method="frappe.desk.workspace")
        return result

    site.insert = insert_and_log

    outcome = apply_spec_to_tenant(db_session, tenant, golden_raw, client=site)

    assert not outcome.deliverable
    assert any(c["name"] == "error-log-clean" and c["status"] == "fail" for c in outcome.run.verification)


def test_the_run_can_be_rolled_back_from_what_was_recorded(db_session, tenant, golden_raw):
    site = FakeSite()
    outcome = apply_spec_to_tenant(db_session, tenant, golden_raw, client=site)

    records = [AppliedRecord(r["doctype"], r["name"], r["action"], r["stage"]) for r in outcome.run.records]
    assert rollback(site, records) == []

    assert site.names("Workspace") == []


def test_cache_clearing_is_wired_through(db_session, tenant, golden_raw):
    calls = []

    apply_spec_to_tenant(db_session, tenant, golden_raw, client=FakeSite(), clear_cache=lambda: calls.append(1))

    assert calls == [1]


def test_a_tenant_that_has_not_finished_provisioning_has_no_credentials(db_session, tenant, golden_raw):
    with pytest.raises(MissingCredentialsError):
        apply_spec_to_tenant(db_session, tenant, golden_raw)  # no client injected


def test_credentials_are_decrypted_for_the_real_client(db_session, tenant, golden_raw, monkeypatch):
    from app.models.tenant_credential import TenantCredential
    from app.services import crypto

    db_session.add(
        TenantCredential(
            tenant_id=tenant.id, kind="api", api_key="KEY", api_secret_encrypted=crypto.encrypt("SECRET")
        )
    )
    db_session.commit()
    seen = {}

    class FakeFrappeClient(FakeSite):
        def close(self):
            seen["closed"] = True

    def fake_for_runtime(runtime, key, secret, **kw):
        seen.update(key=key, secret=secret, host=runtime.host_header)
        return FakeFrappeClient()

    monkeypatch.setattr("app.services.frappe.rest_client.FrappeClient.for_runtime", staticmethod(fake_for_runtime))
    monkeypatch.setattr("app.services.crm.build_service._default_clear_cache", lambda runtime: (lambda: None))

    outcome = apply_spec_to_tenant(db_session, tenant, golden_raw)

    assert outcome.deliverable
    assert seen == {"key": "KEY", "secret": "SECRET", "host": "acme-crm.tenants.localhost", "closed": True}
