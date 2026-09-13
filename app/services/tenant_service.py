import uuid

from sqlalchemy.orm import Session

from app.models.application import Application
from app.models.deployment import Deployment
from app.models.tenant import Tenant
from app.services.org_service import slugify
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

    tenant_slug = generate_tenant_slug(db, org_slug, name)

    tenant = Tenant(
        organization_id=organization_id,
        application_id=application.id,
        name=name,
        slug=tenant_slug,
    )
    db.add(tenant)
    db.flush()

    admin_password = generate_secret()

    deployment = Deployment(
        tenant_id=tenant.id,
        status="pending",
    )
    db.add(deployment)
    db.commit()
    db.refresh(tenant)
    db.refresh(deployment)

    return tenant, deployment, admin_password
