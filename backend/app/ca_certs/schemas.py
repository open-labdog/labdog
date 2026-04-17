from datetime import datetime
from typing import Literal

from pydantic import BaseModel, field_validator

from app.ca_certs.pem_utils import validate_pem_content


class CACertRuleCreate(BaseModel):
    name: str
    pem_content: str
    state: Literal["present", "absent"] = "present"
    comment: str | None = None

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

    name: str | None = None
    state: Literal["present", "absent"] | None = None
    comment: str | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str | None) -> str | None:
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
    group_id: int | None = None
    host_id: int | None = None
    name: str
    fingerprint_sha256: str
    subject: str | None = None
    issuer: str | None = None
    not_before: datetime | None = None
    not_after: datetime | None = None
    state: str
    comment: str | None = None


class EffectiveCACertResponse(BaseModel):
    name: str
    fingerprint_sha256: str
    subject: str | None = None
    issuer: str | None = None
    not_before: datetime | None = None
    not_after: datetime | None = None
    state: str
    pem_content: str  # included so the deploy task can write it to disk
    source: str  # "group" or "host"
    source_id: int
    source_name: str
