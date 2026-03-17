from pydantic import BaseModel, field_validator
from datetime import datetime
from typing import Literal, Optional
import re
from app.services.constants import PROTECTED_SERVICES


class ServiceRuleCreate(BaseModel):
    service_name: str
    state: Literal["running", "stopped"]
    enabled: bool = True
    priority: int = 0
    comment: Optional[str] = None

    @field_validator("service_name")
    @classmethod
    def validate_service_name(cls, v: str) -> str:
        # Strip .service suffix
        if v.endswith(".service"):
            v = v[:-8]
        # Reject shell metacharacters
        if not re.match(r'^[a-zA-Z0-9_@:.-]+$', v):
            raise ValueError(f"Invalid service name '{v}': only alphanumeric, underscore, @, colon, dot, and hyphen allowed")
        # Reject protected services
        if v in PROTECTED_SERVICES:
            raise ValueError(f"'{v}' is a protected service and cannot be managed")
        return v


class ServiceRuleUpdate(BaseModel):
    service_name: Optional[str] = None
    state: Optional[Literal["running", "stopped"]] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None
    comment: Optional[str] = None

    @field_validator("service_name")
    @classmethod
    def validate_service_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        # Strip .service suffix
        if v.endswith(".service"):
            v = v[:-8]
        # Reject shell metacharacters
        if not re.match(r'^[a-zA-Z0-9_@:.-]+$', v):
            raise ValueError(f"Invalid service name '{v}': only alphanumeric, underscore, @, colon, dot, and hyphen allowed")
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
    comment: Optional[str]
    group_id: Optional[int]
    host_id: Optional[int]
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class EffectiveServiceResponse(BaseModel):
    service_name: str
    state: str
    enabled: bool
    source: Literal["group", "host"]
    source_id: int
    source_name: str
    model_config = {"from_attributes": True}
