from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas._shared import LINUX_USERNAME_RE as _USERNAME_RE
from app.user_mgmt.constants import (
    PROTECTED_GROUPS,
    PROTECTED_USERS,
    SUDO_FORBIDDEN_PATTERN,
    VALID_KEY_TYPES,
)


class LinuxUserCreate(BaseModel):
    username: str
    uid: int | None = None
    shell: str = "/bin/bash"
    home_dir: str | None = None
    state: Literal["present", "absent"] = "present"
    comment: str | None = None
    sudo_rule: str | None = None
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
    def validate_uid(cls, v: int | None) -> int | None:
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
    def validate_sudo_rule(cls, v: str | None) -> str | None:
        if v is not None and SUDO_FORBIDDEN_PATTERN.search(v):
            raise ValueError("sudo_rule contains forbidden shell metacharacters: ` $ ( ) ; | & < >")
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
    username: str | None = None
    uid: int | None = None
    shell: str | None = None
    home_dir: str | None = None
    state: Literal["present", "absent"] | None = None
    comment: str | None = None
    sudo_rule: str | None = None
    authorized_keys: list[str] | None = None
    supplementary_groups: list[str] | None = None
    priority: int | None = Field(default=None, ge=0, le=10000)

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str | None) -> str | None:
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
    def validate_uid(cls, v: int | None) -> int | None:
        if v is not None and v < 1000:
            raise ValueError("uid must be >= 1000 (system UIDs are reserved)")
        return v

    @field_validator("shell")
    @classmethod
    def validate_shell(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith("/"):
            raise ValueError("shell must be an absolute path starting with '/'")
        return v

    @field_validator("sudo_rule")
    @classmethod
    def validate_sudo_rule(cls, v: str | None) -> str | None:
        if v is not None and SUDO_FORBIDDEN_PATTERN.search(v):
            raise ValueError("sudo_rule contains forbidden shell metacharacters: ` $ ( ) ; | & < >")
        return v

    @field_validator("authorized_keys")
    @classmethod
    def validate_authorized_keys(cls, v: list[str] | None) -> list[str] | None:
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
    uid: int | None
    shell: str
    home_dir: str | None
    state: str
    comment: str | None
    sudo_rule: str | None
    authorized_keys: list[str]
    supplementary_groups: list[str]
    priority: int
    group_id: int | None
    host_id: int | None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class LinuxGroupCreate(BaseModel):
    groupname: str
    gid: int | None = None
    state: Literal["present", "absent"] = "present"
    priority: int = Field(default=0, ge=0, le=10000)

    @field_validator("groupname")
    @classmethod
    def validate_groupname(cls, v: str) -> str:
        if v in PROTECTED_GROUPS:
            raise ValueError(f"'{v}' is a protected system group and cannot be managed")
        if not _USERNAME_RE.match(v) or len(v) > 32:
            raise ValueError(
                f"Invalid groupname '{v}': must match [a-z_][a-z0-9_-]*"
                " and be at most 32 characters"
            )
        return v

    @field_validator("gid")
    @classmethod
    def validate_gid(cls, v: int | None) -> int | None:
        if v is not None and v < 1000:
            raise ValueError("gid must be >= 1000 (system GIDs are reserved)")
        return v


class LinuxGroupUpdate(BaseModel):
    groupname: str | None = None
    gid: int | None = None
    state: Literal["present", "absent"] | None = None
    priority: int | None = Field(default=None, ge=0, le=10000)

    @field_validator("groupname")
    @classmethod
    def validate_groupname(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v in PROTECTED_GROUPS:
            raise ValueError(f"'{v}' is a protected system group and cannot be managed")
        if not _USERNAME_RE.match(v) or len(v) > 32:
            raise ValueError(
                f"Invalid groupname '{v}': must match [a-z_][a-z0-9_-]*"
                " and be at most 32 characters"
            )
        return v

    @field_validator("gid")
    @classmethod
    def validate_gid(cls, v: int | None) -> int | None:
        if v is not None and v < 1000:
            raise ValueError("gid must be >= 1000 (system GIDs are reserved)")
        return v


class LinuxGroupResponse(BaseModel):
    id: int
    groupname: str
    gid: int | None
    state: str
    priority: int
    group_id: int | None
    host_id: int | None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class EffectiveLinuxUserResponse(BaseModel):
    username: str
    uid: int | None
    shell: str
    home_dir: str | None
    state: str
    sudo_rule: str | None
    authorized_keys: list[str]
    supplementary_groups: list[str]
    source: Literal["group", "host"]
    source_id: int
    source_name: str
    model_config = {"from_attributes": True}


class EffectiveLinuxGroupResponse(BaseModel):
    groupname: str
    gid: int | None
    state: str
    source: Literal["group", "host"]
    source_id: int
    source_name: str
    model_config = {"from_attributes": True}
