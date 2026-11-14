import uuid

from sqlalchemy.orm import Session

from app.models.application import Application
from app.models.deployment import Deployment
from app.models.tenant import Tenant
from app.services.bench_service import get_default_bench
from app.services.provisioning.naming import generate_secret, generate_tenant_slug


def create_tenant_and_deployment(
    db: Session,
    organization_id: uuid.UUID,
    org_slug: str,
    name: str,
    application_key: str,
) -> tuple[Tenant, Deployment, str]:
    """Creates the Tenant + a pending Deployment row. Does NOT enqueue the
    Celery task -- that's the router's job, so this stays a plain, testable
    DB operation. Returns the generated admin password so the router can
    surface it once in the API response."""
    application = db.query(Application).filter_by(key=application_key, is_active=True).first()
    if application is None:
        raise ValueError(f"Unknown or inactive application key: {application_key}")

    bench = get_default_bench(db)
    tenant_slug = generate_tenant_slug(db, org_slug, name)

    tenant = Tenant(
        organization_id=organization_id,
        application_id=application.id,
        name=name,
        slug=tenant_slug,
        # Fixed at creation and never recomputed. The site name is the
        # site's identity on disk and in MariaDB and there is no
        # `bench rename-site`, so deriving it later from a possibly-changed
        # base_domain would point at a site that does not exist.
        site_name=f"{tenant_slug}.{bench.base_domain}",
        bench_id=bench.id,
        hosting_mode="shared_bench",
    )
    db.add(tenant)
    db.flush()

    admin_password = generate_secret()

    deployment = Deployment(
        tenant_id=tenant.id,
        bench_id=bench.id,
        status="pending",
    )
    db.add(deployment)
    db.commit()
    db.refresh(tenant)
    db.refresh(deployment)

    return tenant, deployment, admin_password
