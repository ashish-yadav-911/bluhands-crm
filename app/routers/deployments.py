import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.deployment import Deployment
from app.models.tenant import Tenant
from app.models.user import User
from app.routers.deps import get_current_user
from app.schemas.deployment import DeploymentRead

router = APIRouter(prefix="/deployments", tags=["deployments"])


@router.get("/{deployment_id}", response_model=DeploymentRead)
def get_deployment(
    deployment_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Deployment:
    deployment = db.get(Deployment, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")

    tenant = db.get(Tenant, deployment.tenant_id)
    if tenant is None or tenant.organization_id != current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")

    return deployment
