"""Validate -> apply -> verify -> record, for one tenant.

The one place the pure pieces (spec, applier, verifier) meet the database, the
tenant's credentials and its live site. Kept thin on purpose: everything with
logic in it lives in the modules it calls, which are each testable without a
database or a site.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.crm import ApplyRun, CrmSpecRecord
from app.models.tenant import Tenant
from app.models.tenant_credential import TenantCredential
from app.services import crypto
from app.services.crm.applier import Applier, ApplyResult, SiteClient
from app.services.crm.ledger import DbLedger
from app.services.crm.spec_validator import load_spec
from app.services.crm.verifier import VerifyReport, Verifier


class MissingCredentialsError(RuntimeError):
    pass


@dataclass
class BuildOutcome:
    spec_record: CrmSpecRecord
    run: ApplyRun
    result: ApplyResult
    report: VerifyReport | None

    @property
    def deliverable(self) -> bool:
        """The gate for handing a customer a URL: the apply got as far as
        producing a usable site AND the verifier found nothing essential
        missing. Cosmetic problems (warnings) do not block."""
        return self.result.status in ("succeeded", "partial") and self.report is not None and self.report.ok


def _open_site_client(db: Session, tenant: Tenant):
    from app.services.frappe.rest_client import FrappeClient
    from app.services.runtime import resolve

    credential = db.query(TenantCredential).filter_by(tenant_id=tenant.id, kind="api").first()
    if credential is None or not credential.api_key or not credential.api_secret_encrypted:
        raise MissingCredentialsError(
            f"Tenant {tenant.slug!r} has no API credential; it has not finished provisioning."
        )
    runtime = resolve(tenant)
    return FrappeClient.for_runtime(runtime, credential.api_key, crypto.decrypt(credential.api_secret_encrypted)), runtime


def _default_clear_cache(runtime) -> Callable[[], None]:
    from app.services.frappe.bench_client import BenchClient

    return BenchClient(runtime).clear_cache


def apply_spec_to_tenant(
    db: Session,
    tenant: Tenant,
    raw_spec: dict,
    client: SiteClient | None = None,
    clear_cache: Callable[[], None] | None = None,
    on_progress: Callable[[str, str], None] | None = None,
) -> BuildOutcome:
    """Raises SpecValidationError for a bad spec *before* anything is written
    to the database or sent to the site.

    `client` and `clear_cache` are injectable so the flow is testable against
    an in-memory site; by default both come from the tenant's own runtime.
    """
    spec = load_spec(raw_spec)  # validation first: a bad spec costs nothing

    owns_client = client is None
    if client is None:
        client, runtime = _open_site_client(db, tenant)
        clear_cache = clear_cache or _default_clear_cache(runtime)

    try:
        latest = db.query(func.max(CrmSpecRecord.version)).filter_by(tenant_id=tenant.id).scalar() or 0
        spec_record = CrmSpecRecord(
            tenant_id=tenant.id, version=latest + 1, spec=spec.model_dump(mode="json"), status="draft"
        )
        db.add(spec_record)
        db.flush()
        run = ApplyRun(tenant_id=tenant.id, spec_id=spec_record.id, status="running")
        db.add(run)
        # Committed before touching the site, so a worker that dies mid-apply
        # leaves a visible `running` run rather than no trace at all.
        db.commit()

        # Read before applying: the verifier attributes only *new* Error Log
        # entries to this run, and the site keeps its own clock.
        watermark = Verifier.error_log_watermark(client)

        result = Applier(client, DbLedger(db, tenant.id), clear_cache=clear_cache, on_progress=on_progress).apply(spec)

        # Verifying a site whose apply aborted only adds noise: the failure
        # is already recorded and the doctypes it names are not there.
        report = Verifier(client, watermark).verify(spec) if result.status != "failed" else None

        outcome = BuildOutcome(spec_record, run, result, report)
        run.records = [r.as_dict() for r in result.records]
        run.errors = [e.as_dict() for e in result.errors]
        run.verification = [c.as_dict() for c in report.checks] if report else []
        run.status = result.status if outcome.deliverable else "failed"
        run.finished_at = datetime.now(timezone.utc)
        spec_record.status = (
            "applied" if outcome.deliverable and result.ok else "partial" if outcome.deliverable else "failed"
        )
        db.commit()
        return outcome
    finally:
        if owns_client and hasattr(client, "close"):
            client.close()
