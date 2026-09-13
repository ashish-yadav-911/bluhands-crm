import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AgentJobCreate(BaseModel):
    prompt: str


class AgentJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    status: str
    status_detail: str | None
    summary: str | None
    diff: str | None
    created_at: datetime
    updated_at: datetime
