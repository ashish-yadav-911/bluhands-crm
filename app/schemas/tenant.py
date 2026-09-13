import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.deployment import DeploymentRead


class TenantCreate(BaseModel):
    organization_id: uuid.UUID
    name: str
    application_key: str


class TenantCreateResponse(BaseModel):
    """Returned once, at creation time. admin_password is never persisted
    in the control-plane DB -- only surfaced here and in the tenant's own
    on-disk .env file."""

    tenant_id: uuid.UUID
    deployment_id: uuid.UUID
    status: str
    admin_password: str


class TenantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    application_id: uuid.UUID
    name: str
    slug: str
    created_at: datetime
    latest_deployment: DeploymentRead | None = None
