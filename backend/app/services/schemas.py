import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.services.constants import PROTECTED_SERVICES


class ServiceRuleCreate(BaseModel):
    service_name: str
    state: Literal["running", "stopped"]
    enabled: bool = True
    priority: int = Field(default=0, ge=0, le=10000)
    comment: str | None = None
    unit_content: str | None = None
    deploy_mode: Literal["full", "override"] = "override"

    @field_validator("service_name")
    @classmethod
    def validate_service_name(cls, v: str) -> str:
        # Strip .service suffix
        if v.endswith(".service"):
            v = v[:-8]
        # Reject shell metacharacters
        if not re.match(r"^[a-zA-Z0-9_@:.-]+$", v):
            raise ValueError(
                f"Invalid service name '{v}': only alphanumeric,"
                " underscore, @, colon, dot, and hyphen allowed"
            )
        # Reject protected services
        if v in PROTECTED_SERVICES:
            raise ValueError(f"'{v}' is a protected service and cannot be managed")
        return v


class ServiceRuleUpdate(BaseModel):
    service_name: str | None = None
    state: Literal["running", "stopped"] | None = None
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=10000)
    comment: str | None = None
    unit_content: str | None = None
    deploy_mode: Literal["full", "override"] | None = None

    @field_validator("service_name")
    @classmethod
    def validate_service_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        # Strip .service suffix
        if v.endswith(".service"):
            v = v[:-8]
        # Reject shell metacharacters
        if not re.match(r"^[a-zA-Z0-9_@:.-]+$", v):
            raise ValueError(
                f"Invalid service name '{v}': only alphanumeric,"
                " underscore, @, colon, dot, and hyphen allowed"
            )
        # Reject protected services
        if v in PROTECTED_SERVICES:
            raise ValueError(f"'{v}' is a protected service and cannot be managed")
        return v


class ServiceRuleResponse(BaseModel):
    id: int
    service_name: str
    state: str
    enabled: bool
    priority: int
    comment: str | None
    unit_content: str | None
    deploy_mode: str
    group_id: int | None
    host_id: int | None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class EffectiveServiceResponse(BaseModel):
    service_name: str
    state: str
    enabled: bool
    unit_content: str | None
    deploy_mode: str
    source: Literal["group", "host"]
    source_id: int
    source_name: str
    model_config = {"from_attributes": True}
