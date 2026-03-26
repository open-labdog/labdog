from pydantic import BaseModel, Field
from datetime import datetime


class GroupCreate(BaseModel):
    name: str
    description: str | None = None
    category: str | None = None
    priority: int = Field(ge=0, le=2_147_483_647)


class GroupUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    priority: int | None = Field(default=None, ge=0, le=2_147_483_647)


class GroupResponse(BaseModel):
    id: int
    name: str
    description: str | None
    category: str | None
    priority: int
    created_at: datetime
    updated_at: datetime
    gitops_enabled: bool = False
    gitops_status: str | None = None
    gitops_error_message: str | None = None
    gitops_last_import_at: datetime | None = None
    gitops_file_path: str | None = None
    git_repository_id: int | None = None
    model_config = {"from_attributes": True}
