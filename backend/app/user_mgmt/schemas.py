from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from typing import Literal, Optional
import re

from app.user_mgmt.constants import (
    PROTECTED_USERS,
    PROTECTED_GROUPS,
    SUDO_FORBIDDEN_PATTERN,
    VALID_KEY_TYPES,
)

_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]*$")


class LinuxUserCreate(BaseModel):
    username: str
    uid: Optional[int] = None
    shell: str = "/bin/bash"
    home_dir: Optional[str] = None
    state: Literal["present", "absent"] = "present"
    comment: Optional[str] = None
    sudo_rule: Optional[str] = None
    authorized_keys: list[str] = []
    supplementary_groups: list[str] = []
    priority: int = Field(default=0, ge=0, le=10000)

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        if v in PROTECTED_USERS:
            raise ValueError(f"'{v}' is a protected system user and cannot be managed")
        if not _USERNAME_RE.match(v) or len(v) > 32:
            raise ValueError(
                f"Invalid username '{v}': must match [a-z_][a-z0-9_-]* and be at most 32 characters"
            )
        return v

    @field_validator("uid")
    @classmethod
    def validate_uid(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 1000:
            raise ValueError("uid must be >= 1000 (system UIDs are reserved)")
        return v

    @field_validator("shell")
    @classmethod
    def validate_shell(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError("shell must be an absolute path starting with '/'")
        return v

    @field_validator("sudo_rule")
    @classmethod
    def validate_sudo_rule(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and SUDO_FORBIDDEN_PATTERN.search(v):
            raise ValueError(
                "sudo_rule contains forbidden shell metacharacters: ` $ ( ) ; | & < >"
            )
        return v

    @field_validator("authorized_keys")
    @classmethod
    def validate_authorized_keys(cls, v: list[str]) -> list[str]:
        for key in v:
            if not key.startswith(tuple(VALID_KEY_TYPES)):
                raise ValueError(
                    f"Invalid SSH key: must start with one of {', '.join(VALID_KEY_TYPES)}"
                )
        return v


class LinuxUserUpdate(BaseModel):
    username: Optional[str] = None
    uid: Optional[int] = None
    shell: Optional[str] = None
    home_dir: Optional[str] = None
    state: Optional[Literal["present", "absent"]] = None
    comment: Optional[str] = None
    sudo_rule: Optional[str] = None
    authorized_keys: Optional[list[str]] = None
    supplementary_groups: Optional[list[str]] = None
    priority: Optional[int] = Field(default=None, ge=0, le=10000)

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v in PROTECTED_USERS:
            raise ValueError(f"'{v}' is a protected system user and cannot be managed")
        if not _USERNAME_RE.match(v) or len(v) > 32:
            raise ValueError(
                f"Invalid username '{v}': must match [a-z_][a-z0-9_-]* and be at most 32 characters"
            )
        return v

    @field_validator("uid")
    @classmethod
    def validate_uid(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 1000:
            raise ValueError("uid must be >= 1000 (system UIDs are reserved)")
        return v

    @field_validator("shell")
    @classmethod
    def validate_shell(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.startswith("/"):
            raise ValueError("shell must be an absolute path starting with '/'")
        return v

    @field_validator("sudo_rule")
    @classmethod
    def validate_sudo_rule(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and SUDO_FORBIDDEN_PATTERN.search(v):
            raise ValueError(
                "sudo_rule contains forbidden shell metacharacters: ` $ ( ) ; | & < >"
            )
        return v

    @field_validator("authorized_keys")
    @classmethod
    def validate_authorized_keys(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is not None:
            for key in v:
                if not key.startswith(tuple(VALID_KEY_TYPES)):
                    raise ValueError(
                        f"Invalid SSH key: must start with one of {', '.join(VALID_KEY_TYPES)}"
                    )
        return v


class LinuxUserResponse(BaseModel):
    id: int
    username: str
    uid: Optional[int]
    shell: str
    home_dir: Optional[str]
    state: str
    comment: Optional[str]
    sudo_rule: Optional[str]
    authorized_keys: list[str]
    supplementary_groups: list[str]
    priority: int
    group_id: Optional[int]
    host_id: Optional[int]
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class LinuxGroupCreate(BaseModel):
    groupname: str
    gid: Optional[int] = None
    state: Literal["present", "absent"] = "present"
    priority: int = Field(default=0, ge=0, le=10000)

    @field_validator("groupname")
    @classmethod
    def validate_groupname(cls, v: str) -> str:
        if v in PROTECTED_GROUPS:
            raise ValueError(f"'{v}' is a protected system group and cannot be managed")
        if not _USERNAME_RE.match(v) or len(v) > 32:
            raise ValueError(
                f"Invalid groupname '{v}': must match [a-z_][a-z0-9_-]* and be at most 32 characters"
            )
        return v

    @field_validator("gid")
    @classmethod
    def validate_gid(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 1000:
            raise ValueError("gid must be >= 1000 (system GIDs are reserved)")
        return v


class LinuxGroupUpdate(BaseModel):
    groupname: Optional[str] = None
    gid: Optional[int] = None
    state: Optional[Literal["present", "absent"]] = None
    priority: Optional[int] = Field(default=None, ge=0, le=10000)

    @field_validator("groupname")
    @classmethod
    def validate_groupname(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v in PROTECTED_GROUPS:
            raise ValueError(f"'{v}' is a protected system group and cannot be managed")
        if not _USERNAME_RE.match(v) or len(v) > 32:
            raise ValueError(
                f"Invalid groupname '{v}': must match [a-z_][a-z0-9_-]* and be at most 32 characters"
            )
        return v

    @field_validator("gid")
    @classmethod
    def validate_gid(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 1000:
            raise ValueError("gid must be >= 1000 (system GIDs are reserved)")
        return v


class LinuxGroupResponse(BaseModel):
    id: int
    groupname: str
    gid: Optional[int]
    state: str
    priority: int
    group_id: Optional[int]
    host_id: Optional[int]
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class EffectiveLinuxUserResponse(BaseModel):
    username: str
    uid: Optional[int]
    shell: str
    home_dir: Optional[str]
    state: str
    sudo_rule: Optional[str]
    authorized_keys: list[str]
    supplementary_groups: list[str]
    source: Literal["group", "host"]
    source_id: int
    source_name: str
    model_config = {"from_attributes": True}


class EffectiveLinuxGroupResponse(BaseModel):
    groupname: str
    gid: Optional[int]
    state: str
    source: Literal["group", "host"]
    source_id: int
    source_name: str
    model_config = {"from_attributes": True}
