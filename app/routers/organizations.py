import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.organization import Organization
from app.models.user import User
from app.routers.deps import get_current_user
from app.schemas.organization import OrganizationRead

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.get("/{organization_id}", response_model=OrganizationRead)
def get_organization(
    organization_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Organization:
    # 404 rather than 403 for an org the caller doesn't belong to: the two
    # cases are deliberately indistinguishable, so this can't be used to
    # probe which organization ids exist. Same shape as the tenant routes.
    organization = db.get(Organization, organization_id)
    if organization is None or organization.id != current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return organization
