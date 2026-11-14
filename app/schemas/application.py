import uuid

from pydantic import BaseModel, ConfigDict


class ApplicationRead(BaseModel):
    """Deliberately omits compose_template_path -- that is a server-side
    filesystem detail with no meaning to a client."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    display_name: str
    default_version: str
