from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, field_validator

from app.ca_certs.pem_utils import validate_pem_content


class CACertRuleCreate(BaseModel):
    name: str
    pem_content: str
    state: Literal["present", "absent"] = "present"
    comment: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("name must not be empty")
        if len(v) > 200:
            raise ValueError("name must be 200 characters or fewer")
        return v

    @field_validator("pem_content")
    @classmethod
    def _validate_pem(cls, v: str) -> str:
        # Raises ValueError on invalid PEM, non-cert, or non-CA input
        return validate_pem_content(v)


class CACertRuleUpdate(BaseModel):
    """Update only mutable fields. PEM content is immutable —
    a different cert means a different fingerprint, which is a new entry."""

    name: Optional[str] = None
    state: Optional[Literal["present", "absent"]] = None
    comment: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        if len(v) > 200:
            raise ValueError("name must be 200 characters or fewer")
        return v


class CACertRuleResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    group_id: Optional[int] = None
    host_id: Optional[int] = None
    name: str
    fingerprint_sha256: str
    subject: Optional[str] = None
    issuer: Optional[str] = None
    not_before: Optional[datetime] = None
    not_after: Optional[datetime] = None
    state: str
    comment: Optional[str] = None


class EffectiveCACertResponse(BaseModel):
    name: str
    fingerprint_sha256: str
    subject: Optional[str] = None
    issuer: Optional[str] = None
    not_before: Optional[datetime] = None
    not_after: Optional[datetime] = None
    state: str
    pem_content: str  # included so the deploy task can write it to disk
    source: str  # "group" or "host"
    source_id: int
    source_name: str
