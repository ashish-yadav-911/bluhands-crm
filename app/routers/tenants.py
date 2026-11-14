import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.deployment import Deployment
from app.models.tenant import Tenant
from app.models.tenant_credential import TenantCredential
from app.models.user import User
from app.routers.deps import get_current_user
from app.schemas.deployment import DeploymentRead
from app.schemas.tenant import TenantCreate, TenantCreateResponse, TenantCredentialsRead, TenantRead
from app.services import crypto
from app.services.tenant_service import create_tenant_and_deployment
from app.tasks.provisioning_tasks import provision_tenant_task

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.post("", response_model=TenantCreateResponse, status_code=status.HTTP_201_CREATED)
def create_tenant(
    payload: TenantCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TenantCreateResponse:
    if payload.organization_id != current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot create a tenant for another organization")

    try:
        tenant, deployment, admin_password = create_tenant_and_deployment(
            db,
            organization_id=payload.organization_id,
            org_slug=current_user.organization.slug,
            name=payload.name,
            application_key=payload.application_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    provision_tenant_task.delay(str(deployment.id), admin_password)

    return TenantCreateResponse(
        tenant_id=tenant.id,
        deployment_id=deployment.id,
        status=deployment.status,
        admin_password=admin_password,
    )


def _get_owned_tenant(db: Session, tenant_id: uuid.UUID, current_user: User) -> Tenant:
    # 404 for both "missing" and "someone else's" so ids cannot be probed.
    tenant = db.get(Tenant, tenant_id)
    if tenant is None or tenant.organization_id != current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return tenant


def _latest_deployment(db: Session, tenant_id: uuid.UUID) -> Deployment | None:
    return (
        db.query(Deployment)
        .filter_by(tenant_id=tenant_id)
        .order_by(desc(Deployment.created_at))
        .first()
    )


def _to_read(db: Session, tenant: Tenant) -> TenantRead:
    tenant_read = TenantRead.model_validate(tenant)
    latest = _latest_deployment(db, tenant.id)
    if latest is not None:
        tenant_read.latest_deployment = DeploymentRead.model_validate(latest)
    return tenant_read


@router.get("", response_model=list[TenantRead])
def list_tenants(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TenantRead]:
    tenants = (
        db.query(Tenant)
        .filter_by(organization_id=current_user.organization_id)
        .order_by(desc(Tenant.created_at))
        .all()
    )
    return [_to_read(db, tenant) for tenant in tenants]


@router.get("/{tenant_id}", response_model=TenantRead)
def get_tenant(
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TenantRead:
    return _to_read(db, _get_owned_tenant(db, tenant_id, current_user))


@router.get("/{tenant_id}/credentials", response_model=TenantCredentialsRead)
def get_tenant_credentials(
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TenantCredentialsRead:
    tenant = _get_owned_tenant(db, tenant_id, current_user)

    admin = db.query(TenantCredential).filter_by(tenant_id=tenant.id, kind="admin").first()
    # Absent until provisioning has reached the credential step -- a client
    # polling before then gets a clear "not yet", not a 500.
    if admin is None or admin.password_encrypted is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credentials are not available yet")

    try:
        password = crypto.decrypt(admin.password_encrypted)
    except crypto.CredentialEncryptionError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    revealed_before = admin.revealed_at is not None
    admin.revealed_at = datetime.now(timezone.utc)
    db.commit()

    latest = _latest_deployment(db, tenant.id)
    return TenantCredentialsRead(
        site_name=tenant.site_name,
        url=latest.url if latest is not None else None,
        username=admin.username,
        password=password,
        revealed_before=revealed_before,
    )
