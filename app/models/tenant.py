import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Tenant(Base):
    """Identity/ownership only. Runtime/container state lives on Deployment,
    so a tenant can have multiple deployments over time (Phase 2/3 seam)."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), nullable=False
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("applications.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)

    # The Frappe site name, which is also the hostname the bench's nginx
    # matches on. Stored rather than derived from slug + base_domain: it is
    # the site's identity on disk and in MariaDB, there is no
    # `bench rename-site`, and a config change to base_domain must never
    # silently repoint existing tenants at sites that do not exist.
    site_name: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, index=True)

    # Which bench holds this tenant's site. Null only for rows predating the
    # shared-bench model.
    bench_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("benches.id"), nullable=True)

    # "shared_bench" (one bench, many sites) or "dedicated" (its own compose
    # stack). Only the former is implemented; app/services/runtime.py is the
    # single place that branches on it. A tenant needing real server-side
    # code is the trigger to move it to "dedicated", since installing a
    # custom app on a shared bench loads that tenant's Python into workers
    # serving everyone else.
    hosting_mode: Mapped[str] = mapped_column(String(50), nullable=False, default="shared_bench")

    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))

    organization: Mapped["Organization"] = relationship(back_populates="tenants")
    application: Mapped["Application"] = relationship()
    deployments: Mapped[list["Deployment"]] = relationship(back_populates="tenant")
    credentials: Mapped[list["TenantCredential"]] = relationship(back_populates="tenant")
