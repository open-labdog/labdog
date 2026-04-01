"""Schemas for live service inventory and ad-hoc command execution."""

from typing import Literal

import re
from pydantic import BaseModel, field_validator


ServiceCommandAction = Literal["start", "stop", "restart"]


class ServiceInventoryItem(BaseModel):
    unit: str
    load_state: str
    active_state: str
    sub_state: str
    description: str
    is_managed: bool
    is_protected: bool
    is_system: bool


class ServiceCommandRequest(BaseModel):
    service_name: str
    action: ServiceCommandAction

    @field_validator("service_name")
    @classmethod
    def validate_service_name(cls, v: str) -> str:
        # Strip .service suffix
        if v.endswith(".service"):
            v = v[:-8]
        # Reject shell metacharacters
        if not re.match(r"^[a-zA-Z0-9_@:.-]+$", v):
            raise ValueError(
                f"Invalid service name '{v}': only alphanumeric, underscore, @, colon, dot, and hyphen allowed"
            )
        # Max length check
        if len(v) > 100:
            raise ValueError("Service name must be 100 characters or fewer")
        return v


class ServiceCommandResponse(BaseModel):
    success: bool
    exit_code: int
    stdout: str
    stderr: str
    service_name: str
    action: str
    is_protected: bool
