import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Plain-string status (not a native Postgres enum) so adding a new status
# value later is an application-level change, not a migration.
DEPLOYMENT_STATUSES = ("pending", "provisioning", "running", "failed", "stopped")


class Deployment(Base):
    """Runtime/container state for a tenant's provisioning attempt. The sole
    writer of status is the provisioning Celery task; the API only reads."""

    __tablename__ = "deployments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), nullable=False)

    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    status_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    compose_project_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    http_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    site_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    tenant: Mapped["Tenant"] = relationship(back_populates="deployments")
