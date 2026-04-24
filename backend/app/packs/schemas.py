from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.packs.models import PackAuthType, PackRole, PackSourceType


def _validate_ref(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("ref must not be empty")
    # Git ref names disallow ASCII control chars, space, ~, ^, :, ?, *, [, \
    forbidden = set(" ~^:?*[\\")
    if any(c in forbidden for c in v) or any(ord(c) < 0x20 for c in v):
        raise ValueError("ref contains characters git won't accept")
    return v


def _validate_name(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("name must not be empty")
    if v == "bundled":
        raise ValueError("'bundled' is reserved for the built-in pack")
    return v


class ActionPackCreate(BaseModel):
    """Admin-supplied payload for creating a pack.

    ``ssh_private_key`` / ``ssh_known_hosts`` / ``token`` are write-only —
    the encrypted bytes live in the DB, responses expose only booleans.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    source_type: PackSourceType = PackSourceType.GIT
    repo_url: str
    ref: str = "main"
    role: PackRole = PackRole.OVERRIDE
    enabled: bool = True

    auth_type: PackAuthType = PackAuthType.NONE
    ssh_private_key: str | None = None
    ssh_known_hosts: str | None = None
    token: str | None = None

    _validate_name = field_validator("name")(classmethod(lambda cls, v: _validate_name(v)))
    _validate_ref = field_validator("ref")(classmethod(lambda cls, v: _validate_ref(v)))

    @model_validator(mode="after")
    def _check_auth_fields(self) -> ActionPackCreate:
        _enforce_source_fields(self)
        return _enforce_auth_fields(self, creating=True)


class ActionPackUpdate(BaseModel):
    """Partial update payload. All fields optional.

    Credential fields semantics:
      * ``ssh_private_key`` / ``token`` omitted → keep the stored secret.
      * Set to a non-empty string → replace.
      * Switching ``auth_type`` requires supplying the relevant secret in
        the same request; the mutex validator enforces this.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    source_type: PackSourceType | None = None
    repo_url: str | None = None
    ref: str | None = None
    role: PackRole | None = None
    enabled: bool | None = None

    auth_type: PackAuthType | None = None
    ssh_private_key: str | None = None
    ssh_known_hosts: str | None = None
    token: str | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str | None) -> str | None:
        return _validate_name(v) if v is not None else v

    @field_validator("ref")
    @classmethod
    def _check_ref(cls, v: str | None) -> str | None:
        return _validate_ref(v) if v is not None else v

    @model_validator(mode="after")
    def _check_auth_fields(self) -> ActionPackUpdate:
        _enforce_source_fields(self)
        return _enforce_auth_fields(self, creating=False)


def _enforce_source_fields(model) -> None:
    """Local packs must use auth_type='none' and supply no credentials.

    The DB-level check constraint backs this up; we reject early here so
    admins get a readable error instead of a 500 from the constraint.
    """
    st = getattr(model, "source_type", None)
    at = getattr(model, "auth_type", None)
    if st == PackSourceType.LOCAL:
        if at is not None and at != PackAuthType.NONE:
            raise ValueError(
                "source_type=local requires auth_type=none (nothing is cloned)"
            )
        if model.ssh_private_key or model.token:
            raise ValueError(
                "source_type=local does not accept credentials — the path is "
                "read in place"
            )


def _enforce_auth_fields(model, *, creating: bool):
    """Shared mutex check between auth_type and the secret fields.

    For create: the secret corresponding to ``auth_type`` must be present;
    the irrelevant secret must be absent. For update: rules only apply
    when ``auth_type`` is part of the payload — otherwise the caller is
    just editing non-auth fields.
    """
    at = model.auth_type
    if at is None and not creating:
        # Update without changing auth_type — nothing to enforce here.
        # (Supplying a secret without auth_type is still allowed: it's
        # treated as "rotate the existing key for the current auth_type",
        # which the API layer handles when it encrypts the new value.)
        return model

    if at == PackAuthType.NONE:
        if model.ssh_private_key or model.token:
            raise ValueError(
                "auth_type=none does not accept ssh_private_key or token"
            )
    elif at == PackAuthType.SSH:
        if creating and not model.ssh_private_key:
            raise ValueError("auth_type=ssh requires ssh_private_key")
        if model.token:
            raise ValueError("auth_type=ssh does not accept token")
    elif at == PackAuthType.HTTPS_TOKEN:
        if creating and not model.token:
            raise ValueError("auth_type=https_token requires token")
        if model.ssh_private_key:
            raise ValueError("auth_type=https_token does not accept ssh_private_key")
    return model


class ActionPackResponse(BaseModel):
    """Public read representation. Excludes every byte of credential material."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    source_type: PackSourceType
    repo_url: str
    ref: str
    role: PackRole
    priority: int = Field(
        description=(
            "Derived load-order priority (higher wins on collision). "
            "Set internally from source_type + role — read-only."
        )
    )
    enabled: bool

    auth_type: PackAuthType
    has_ssh_key: bool = Field(default=False)
    has_token: bool = Field(default=False)
    ssh_known_hosts: str | None = None

    last_synced_at: datetime | None
    last_sync_status: str | None
    last_sync_error: str | None
    current_sha: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, row) -> ActionPackResponse:
        from app.packs.service import derive_priority  # noqa: PLC0415

        return cls(
            id=row.id,
            name=row.name,
            source_type=row.source_type,
            repo_url=row.repo_url,
            ref=row.ref,
            role=row.role,
            priority=derive_priority(row),
            enabled=row.enabled,
            auth_type=row.auth_type,
            has_ssh_key=row.encrypted_ssh_key is not None,
            has_token=row.encrypted_token is not None,
            ssh_known_hosts=row.ssh_known_hosts,
            last_synced_at=row.last_synced_at,
            last_sync_status=row.last_sync_status,
            last_sync_error=row.last_sync_error,
            current_sha=row.current_sha,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class ActionPackTestRequest(BaseModel):
    """Pre-save connection test. Credentials travel in the request body,
    never touch the DB."""

    model_config = ConfigDict(extra="forbid")

    source_type: PackSourceType = PackSourceType.GIT
    repo_url: str
    ref: str = "main"
    auth_type: PackAuthType = PackAuthType.NONE
    ssh_private_key: str | None = None
    ssh_known_hosts: str | None = None
    token: str | None = None

    @field_validator("ref")
    @classmethod
    def _check_ref(cls, v: str) -> str:
        return _validate_ref(v)

    @model_validator(mode="after")
    def _check_auth_fields(self) -> ActionPackTestRequest:
        _enforce_source_fields(self)
        return _enforce_auth_fields(self, creating=True)


class ActionPackTestResponse(BaseModel):
    success: bool
    message: str
    commit_sha: str | None = None


class ActionPackSyncResponse(BaseModel):
    """Result of a manual sync or post-mutation sync attempt."""

    success: bool
    message: str
    current_sha: str | None = None
    last_synced_at: datetime | None = None
