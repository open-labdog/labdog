import re
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.cron.validators import validate_cron_expression

_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
_USER_RE = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$")


class CronJobCreate(BaseModel):
    name: str
    user: str = "root"
    schedule: str
    command: str
    environment: dict[str, str] = {}
    state: Literal["present", "absent"] = "present"
    priority: int = Field(default=0, ge=0, le=10000)
    comment: Optional[str] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not _NAME_RE.match(v) or len(v) > 100:
            raise ValueError(
                f"Invalid cron job name '{v}': must match "
                "[a-zA-Z0-9][a-zA-Z0-9_-]* and be at most 100 characters"
            )
        return v

    @field_validator("user")
    @classmethod
    def validate_user(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("user must not be empty")
        if not _USER_RE.match(v) or len(v) > 32:
            raise ValueError(
                f"Invalid user '{v}': must match [a-zA-Z0-9_][a-zA-Z0-9_.-]* "
                "and be at most 32 characters (no shell metacharacters)"
            )
        return v

    @field_validator("schedule")
    @classmethod
    def validate_schedule(cls, v: str) -> str:
        validate_cron_expression(v)
        return v

    @field_validator("command")
    @classmethod
    def validate_command(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("command must not be empty")
        return v


class CronJobUpdate(BaseModel):
    name: Optional[str] = None
    user: Optional[str] = None
    schedule: Optional[str] = None
    command: Optional[str] = None
    environment: Optional[dict[str, str]] = None
    state: Optional[Literal["present", "absent"]] = None
    priority: Optional[int] = Field(default=None, ge=0, le=10000)
    comment: Optional[str] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not _NAME_RE.match(v) or len(v) > 100:
            raise ValueError(
                f"Invalid cron job name '{v}': must match "
                "[a-zA-Z0-9][a-zA-Z0-9_-]* and be at most 100 characters"
            )
        return v

    @field_validator("user")
    @classmethod
    def validate_user(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not v.strip():
            raise ValueError("user must not be empty")
        if not _USER_RE.match(v) or len(v) > 32:
            raise ValueError(
                f"Invalid user '{v}': must match [a-zA-Z0-9_][a-zA-Z0-9_.-]* "
                "and be at most 32 characters (no shell metacharacters)"
            )
        return v

    @field_validator("schedule")
    @classmethod
    def validate_schedule(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            validate_cron_expression(v)
        return v

    @field_validator("command")
    @classmethod
    def validate_command(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("command must not be empty")
        return v


class CronJobResponse(BaseModel):
    id: int
    name: str
    user: str
    schedule: str
    command: str
    environment: dict[str, str]
    state: str
    priority: int
    comment: Optional[str]
    group_id: Optional[int]
    host_id: Optional[int]
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class EffectiveCronJobResponse(BaseModel):
    name: str
    user: str
    schedule: str
    command: str
    environment: dict[str, str]
    state: str
    priority: int
    comment: Optional[str]
    source: Literal["group", "host"]
    source_id: int
    source_name: str
    model_config = {"from_attributes": True}
