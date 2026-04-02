from pydantic import BaseModel, Field, field_validator
from datetime import datetime

_VALID_POLICIES = {"accept", "drop"}


def _validate_policy(v: str | None) -> str | None:
    if v is not None and v not in _VALID_POLICIES:
        raise ValueError(f"Policy must be 'accept' or 'drop', got '{v}'")
    return v


class GroupCreate(BaseModel):
    name: str
    description: str | None = None
    category: str | None = None
    priority: int = Field(ge=0, le=2_147_483_647)
    input_policy: str | None = None
    output_policy: str | None = None

    @field_validator("input_policy", "output_policy")
    @classmethod
    def check_policy(cls, v: str | None) -> str | None:
        return _validate_policy(v)


class GroupUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    priority: int | None = Field(default=None, ge=0, le=2_147_483_647)
    input_policy: str | None = None
    output_policy: str | None = None

    @field_validator("input_policy", "output_policy")
    @classmethod
    def check_policy(cls, v: str | None) -> str | None:
        return _validate_policy(v)


class GroupPoliciesUpdate(BaseModel):
    input_policy: str | None = None
    output_policy: str | None = None

    @field_validator("input_policy", "output_policy")
    @classmethod
    def check_policy(cls, v: str | None) -> str | None:
        return _validate_policy(v)


class GroupResponse(BaseModel):
    id: int
    name: str
    description: str | None
    category: str | None
    priority: int
    input_policy: str | None = None
    output_policy: str | None = None
    created_at: datetime
    updated_at: datetime
    gitops_enabled: bool = False
    gitops_status: str | None = None
    gitops_error_message: str | None = None
    gitops_last_import_at: datetime | None = None
    gitops_file_path: str | None = None
    git_repository_id: int | None = None
    model_config = {"from_attributes": True}
