import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.deployment import DeploymentRead


class TenantCreate(BaseModel):
    organization_id: uuid.UUID
    name: str
    application_key: str


class TenantCreateResponse(BaseModel):
    """Returned at creation time. admin_password is also stored, encrypted,
    and retrievable later via GET /tenants/{id}/credentials -- see
    app/services/crypto.py for why that changed."""

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
    site_name: str | None = None
    hosting_mode: str
    created_at: datetime
    latest_deployment: DeploymentRead | None = None


class TenantCredentialsRead(BaseModel):
    """What the customer is shown on the delivery screen.

    Only the Administrator login. The API key/secret pair the applier uses
    is a machine credential and is never returned to a client.
    """

    site_name: str | None
    url: str | None
    username: str
    password: str
    revealed_before: bool
