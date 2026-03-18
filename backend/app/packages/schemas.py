import re
from typing import Literal, Optional

from pydantic import BaseModel, field_validator

from app.packages.constants import is_protected

_PKG_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._+:\-]*$")


class PackageRuleCreate(BaseModel):
    package_name: str
    version: Optional[str] = None
    state: Literal["present", "absent", "latest"] = "present"
    package_manager: Literal["auto", "apt", "dnf", "yum"] = "auto"
    priority: int = 0
    comment: Optional[str] = None

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
            raise ValueError(
                f"Package '{v}' is protected and cannot be managed via Barricade"
            )
        return v


class PackageRuleUpdate(BaseModel):
    version: Optional[str] = None
    state: Optional[Literal["present", "absent", "latest"]] = None
    package_manager: Optional[Literal["auto", "apt", "dnf", "yum"]] = None
    priority: Optional[int] = None
    comment: Optional[str] = None


class PackageRuleResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    group_id: Optional[int] = None
    host_id: Optional[int] = None
    package_name: str
    version: Optional[str] = None
    state: str
    package_manager: str
    priority: int
    comment: Optional[str] = None


class EffectivePackageResponse(BaseModel):
    package_name: str
    version: Optional[str] = None
    state: str
    package_manager: str
    priority: int
    source: str
    source_id: int
    source_name: str


class PackageRepositoryCreate(BaseModel):
    name: str
    url: str
    key_url: Optional[str] = None
    repo_type: Literal["apt", "yum"]
    distribution: Optional[str] = None
    components: Optional[str] = None
    state: Literal["present", "absent"] = "present"

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith(("https://", "http://")):
            raise ValueError("Repository URL must start with https:// or http://")
        return v


class PackageRepositoryUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    key_url: Optional[str] = None
    repo_type: Optional[Literal["apt", "yum"]] = None
    distribution: Optional[str] = None
    components: Optional[str] = None
    state: Optional[Literal["present", "absent"]] = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.startswith(("https://", "http://")):
            raise ValueError("Repository URL must start with https:// or http://")
        return v


class PackageRepositoryResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    group_id: int
    name: str
    url: str
    key_url: Optional[str] = None
    repo_type: str
    distribution: Optional[str] = None
    components: Optional[str] = None
    state: str
