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

# Each field check below is written once and used by both the Create and the
# Update model. None (a field an update leaves out) passes every check.


def _check_username(v: str | None) -> str | None:
    if v is None:
        return v
    if v in PROTECTED_USERS:
        raise ValueError(f"'{v}' is a protected system user and cannot be managed")
    if not _USERNAME_RE.match(v) or len(v) > 32:
        raise ValueError(
            f"Invalid username '{v}': must match [a-z_][a-z0-9_-]* and be at most 32 characters"
        )
    return v


def _check_uid(v: int | None) -> int | None:
    if v is not None and v < 1000:
        raise ValueError("uid must be >= 1000 (system UIDs are reserved)")
    return v


def _check_shell(v: str | None) -> str | None:
    if v is not None and not v.startswith("/"):
        raise ValueError("shell must be an absolute path starting with '/'")
    return v


def _check_sudo_rule(v: str | None) -> str | None:
    if v is not None and SUDO_FORBIDDEN_PATTERN.search(v):
        raise ValueError(
            "sudo_rule contains forbidden shell metacharacters or a line "
            "break: ` $ ( ) ; | & < > CR LF NUL. A line break would write "
            "a second sudoers rule."
        )
    return v


def _check_authorized_keys(v: list[str] | None) -> list[str] | None:
    if v is not None:
        for key in v:
            if not key.startswith(tuple(VALID_KEY_TYPES)):
                raise ValueError(
                    f"Invalid SSH key: must start with one of {', '.join(VALID_KEY_TYPES)}"
                )
    return v


def _check_groupname(v: str | None) -> str | None:
    if v is None:
        return v
    if v in PROTECTED_GROUPS:
        raise ValueError(f"'{v}' is a protected system group and cannot be managed")
    if not _USERNAME_RE.match(v) or len(v) > 32:
        raise ValueError(
            f"Invalid groupname '{v}': must match [a-z_][a-z0-9_-]* and be at most 32 characters"
        )
    return v


def _check_gid(v: int | None) -> int | None:
    if v is not None and v < 1000:
        raise ValueError("gid must be >= 1000 (system GIDs are reserved)")
    return v


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

    validate_username = field_validator("username")(_check_username)
    validate_uid = field_validator("uid")(_check_uid)
    validate_shell = field_validator("shell")(_check_shell)
    validate_sudo_rule = field_validator("sudo_rule")(_check_sudo_rule)
    validate_authorized_keys = field_validator("authorized_keys")(_check_authorized_keys)


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

    validate_username = field_validator("username")(_check_username)
    validate_uid = field_validator("uid")(_check_uid)
    validate_shell = field_validator("shell")(_check_shell)
    validate_sudo_rule = field_validator("sudo_rule")(_check_sudo_rule)
    validate_authorized_keys = field_validator("authorized_keys")(_check_authorized_keys)


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

    validate_groupname = field_validator("groupname")(_check_groupname)
    validate_gid = field_validator("gid")(_check_gid)


class LinuxGroupUpdate(BaseModel):
    groupname: str | None = None
    gid: int | None = None
    state: Literal["present", "absent"] | None = None
    priority: int | None = Field(default=None, ge=0, le=10000)

    validate_groupname = field_validator("groupname")(_check_groupname)
    validate_gid = field_validator("gid")(_check_gid)


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
