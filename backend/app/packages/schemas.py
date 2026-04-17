import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.packages.constants import is_protected

_PKG_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._+:\-]*$")


class PackageRuleCreate(BaseModel):
    package_name: str
    version: str | None = None
    state: Literal["present", "absent", "latest"] = "present"
    package_manager: Literal["auto", "apt", "dnf", "yum"] = "auto"
    priority: int = Field(default=0, ge=0, le=10000)
    comment: str | None = None
    hold: bool = False

    @field_validator("package_name")
    @classmethod
    def validate_package_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("package_name must not be empty")
        if not _PKG_NAME_RE.match(v):
            raise ValueError(
                f"Invalid package name '{v}': only alphanumeric, hyphens, "
                "dots, underscores, colons, and plus signs allowed"
            )
        if is_protected(v):
            raise ValueError(f"Package '{v}' is protected and cannot be managed via Barricade")
        return v


class PackageRuleUpdate(BaseModel):
    version: str | None = None
    state: Literal["present", "absent", "latest"] | None = None
    package_manager: Literal["auto", "apt", "dnf", "yum"] | None = None
    priority: int | None = Field(default=None, ge=0, le=10000)
    comment: str | None = None
    hold: bool | None = None


class PackageRuleResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    group_id: int | None = None
    host_id: int | None = None
    package_name: str
    version: str | None = None
    state: str
    package_manager: str
    priority: int
    comment: str | None = None
    hold: bool = False


class EffectivePackageResponse(BaseModel):
    package_name: str
    version: str | None = None
    state: str
    package_manager: str
    priority: int
    hold: bool = False
    source: str
    source_id: int
    source_name: str


class PackageRepositoryCreate(BaseModel):
    name: str
    url: str
    key_url: str | None = None
    repo_type: Literal["apt", "yum"]
    distribution: str | None = None
    components: str | None = None
    state: Literal["present", "absent"] = "present"

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith(("https://", "http://")):
            raise ValueError("Repository URL must start with https:// or http://")
        return v


class PackageRepositoryUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    key_url: str | None = None
    repo_type: Literal["apt", "yum"] | None = None
    distribution: str | None = None
    components: str | None = None
    state: Literal["present", "absent"] | None = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith(("https://", "http://")):
            raise ValueError("Repository URL must start with https:// or http://")
        return v


class PackageRepositoryResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    group_id: int
    name: str
    url: str
    key_url: str | None = None
    repo_type: str
    distribution: str | None = None
    components: str | None = None
    state: str
