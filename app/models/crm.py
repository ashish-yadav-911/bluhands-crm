import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

SPEC_STATUSES = ("draft", "applied", "partial", "failed")
APPLY_RUN_STATUSES = ("running", "succeeded", "partial", "failed")


class CrmSpecRecord(Base):
    """One version of a tenant's CRM spec, kept forever.

    Versions are immutable and numbered per tenant. A change request produces
    version N+1 as a whole new spec, never a patch, so any two versions can be
    diffed and any past one re-applied. `tenant.current_spec_id` (later) only
    advances when a version applies in full, so the recorded state stays
    truthful about what is actually on the site.
    """

    __tablename__ = "crm_specs"
    __table_args__ = (UniqueConstraint("tenant_id", "version", name="uq_crm_specs_tenant_version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # The *normalized* spec -- what the applier actually consumed -- so a
    # stored version is reproducible without re-running the normalizer of
    # some later release.
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))


class ApplyRun(Base):
    """One attempt to apply a spec version to a site.

    `records` is what makes object-level rollback possible: it says, for each
    object, whether this run *created* it (rollback may delete it) or merely
    *updated* it (rollback must not touch it).
    """

    __tablename__ = "crm_apply_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), nullable=False, index=True)
    spec_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("crm_specs.id"), nullable=False)

    status: Mapped[str] = mapped_column(String(50), nullable=False, default="running")
    records: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    errors: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    verification: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    started_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)


class AppliedObject(Base):
    """The applier's ledger: the content hash last applied for each object.

    A record and a diffing aid only. The live site is the source of truth; if
    this and the site disagree, the site wins (the applier re-creates what the
    site lacks). It exists so an unchanged object can be skipped without a PUT.
    """

    __tablename__ = "crm_applied_objects"
    __table_args__ = (
        UniqueConstraint("tenant_id", "doctype", "name", name="uq_crm_applied_objects_identity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), nullable=False, index=True)
    doctype: Mapped[str] = mapped_column(String(140), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
