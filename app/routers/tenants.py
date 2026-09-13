import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.deployment import Deployment
from app.models.tenant import Tenant
from app.models.user import User
from app.routers.deps import get_current_user
from app.schemas.tenant import TenantCreate, TenantCreateResponse, TenantRead
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


@router.get("/{tenant_id}", response_model=TenantRead)
def get_tenant(
    tenant_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TenantRead:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None or tenant.organization_id != current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    latest_deployment = (
        db.query(Deployment)
        .filter_by(tenant_id=tenant.id)
        .order_by(desc(Deployment.created_at))
        .first()
    )

    tenant_read = TenantRead.model_validate(tenant)
    if latest_deployment is not None:
        from app.schemas.deployment import DeploymentRead

        tenant_read.latest_deployment = DeploymentRead.model_validate(latest_deployment)
    return tenant_read
