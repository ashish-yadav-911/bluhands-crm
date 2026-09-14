import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

BENCH_STATUSES = ("provisioning", "ready", "draining", "dead")


class Bench(Base):
    """A shared ERPNext bench: one container set hosting many tenant sites,
    resolved per request by Host header.

    A row here is infrastructure, not a tenant. There is exactly one today,
    but it is a table rather than pure config because sites are pinned to
    the bench that created them -- their databases live in that bench's
    MariaDB -- so once a second bench exists (to spread load, or to run a
    newer ERPNext), every tenant has to record which one it belongs to.
    """

    __tablename__ = "benches"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)

    compose_project_name: Mapped[str] = mapped_column(String(255), nullable=False)
    compose_file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    backend_service: Mapped[str] = mapped_column(String(100), nullable=False, default="backend")
    frontend_http_port: Mapped[int] = mapped_column(Integer, nullable=False)
    base_domain: Mapped[str] = mapped_column(String(255), nullable=False)

    # Recorded per bench, not globally: sites are built against one ERPNext
    # version and the whole bench upgrades together.
    image_version: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[str] = mapped_column(String(50), nullable=False, default="ready")
    # A soft cap. Every site shares this bench's MariaDB connection pool and
    # its single queue worker, so capacity is about contention, not disk.
    max_sites: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
