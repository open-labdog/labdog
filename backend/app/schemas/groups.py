from pydantic import BaseModel
from datetime import datetime


class GroupCreate(BaseModel):
    name: str
    description: str | None = None
    priority: int


class GroupUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    priority: int | None = None


class GroupResponse(BaseModel):
    id: int
    name: str
    description: str | None
    priority: int
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}
