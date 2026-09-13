import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DeploymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    status: str
    status_detail: str | None
    url: str | None
    created_at: datetime
    updated_at: datetime
