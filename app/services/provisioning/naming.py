import secrets
import string

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.deployment import Deployment
from app.models.tenant import Tenant
from app.services.org_service import slugify


def generate_tenant_slug(db: Session, org_slug: str, tenant_name: str) -> str:
    base = slugify(f"{org_slug}-{tenant_name}")
    slug = base
    suffix = 1
    while db.query(Tenant).filter_by(slug=slug).first() is not None:
        suffix += 1
        slug = f"{base}-{suffix}"
    return slug


def compose_project_name(tenant_slug: str) -> str:
    return f"tenant-{tenant_slug}"


def site_name(tenant_slug: str) -> str:
    return f"{tenant_slug}.{settings.base_domain}"


def allocate_http_port(db: Session) -> int:
    """Simplest possible v1 allocator: Postgres is the source of truth, so we
    just scan taken ports in the configured range and pick the first free one."""
    taken = {
        port
        for (port,) in db.execute(
            select(Deployment.http_port).where(Deployment.http_port.is_not(None))
        ).all()
    }
    for port in range(settings.tenant_port_range_start, settings.tenant_port_range_end + 1):
        if port not in taken:
            return port
    raise RuntimeError("No free ports left in the configured tenant port range")


def generate_secret(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))
