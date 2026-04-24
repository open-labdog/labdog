from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.packs.models import PackRole, PackSourceType


def _validate_name(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("name must not be empty")
    if v == "bundled":
        raise ValueError("'bundled' is reserved for the built-in pack")
    return v


class ActionPackCreate(BaseModel):
    """Admin-supplied payload for creating a pack.

    Git packs reference a configured ``GitRepository`` via
    ``git_repository_id`` and name a subpath via ``path``. Local packs
    supply ``local_path`` — the absolute filesystem path on the LabDog
    host. Credentials never appear here; they live on the
    ``GitRepository`` row managed on the Git Repos page.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    source_type: PackSourceType = PackSourceType.GIT
    git_repository_id: int | None = None
    path: str = ""
    local_path: str | None = None
    role: PackRole = PackRole.OVERRIDE
    enabled: bool = True

    _validate_name = field_validator("name")(classmethod(lambda cls, v: _validate_name(v)))

    @model_validator(mode="after")
    def _check_source_fields(self) -> ActionPackCreate:
        _enforce_source_fields(self, creating=True)
        return self


class ActionPackUpdate(BaseModel):
    """Partial update payload. All fields optional.

    When ``source_type`` switches from git→local or vice versa, the
    caller must supply the fields the new source needs; the mutex
    validator refuses otherwise.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    source_type: PackSourceType | None = None
    git_repository_id: int | None = None
    path: str | None = None
    local_path: str | None = None
    role: PackRole | None = None
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str | None) -> str | None:
        return _validate_name(v) if v is not None else v

    @model_validator(mode="after")
    def _check_source_fields(self) -> ActionPackUpdate:
        _enforce_source_fields(self, creating=False)
        return self


def _enforce_source_fields(model, *, creating: bool) -> None:
    """Enforce the shape of the source-dependent fields.

    Git packs must reference a GitRepository and must not carry a
    local_path. Local packs must carry an absolute local_path and must
    not reference a GitRepository. On update (``creating=False``),
    rules apply only when the relevant field appears in the payload —
    the caller may be editing unrelated fields.
    """
    st = model.source_type
    if st is None and not creating:
        # Update not touching source_type; no cross-field rules to check.
        return

    if st == PackSourceType.GIT:
        if creating and model.git_repository_id is None:
            raise ValueError(
                "source_type=git requires git_repository_id to reference a "
                "configured Git repository"
            )
        if model.local_path not in (None, ""):
            raise ValueError("source_type=git does not accept local_path")
    elif st == PackSourceType.LOCAL:
        if creating and not model.local_path:
            raise ValueError(
                "source_type=local requires local_path (absolute filesystem "
                "path on the LabDog host)"
            )
        if model.git_repository_id is not None:
            raise ValueError("source_type=local does not accept git_repository_id")


class ActionPackResponse(BaseModel):
    """Public read representation."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    source_type: PackSourceType
    git_repository_id: int | None
    git_repository_name: str | None = None
    """Convenience — the repo's display name when linked; the UI can
    render this directly instead of having to look up the repo by id."""
    path: str
    local_path: str | None
    role: PackRole
    priority: int = Field(
        description=(
            "Derived load-order priority (higher wins on collision). "
            "Set internally from source_type + role — read-only."
        )
    )
    enabled: bool

    last_synced_at: datetime | None
    last_sync_status: str | None
    last_sync_error: str | None
    current_sha: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, row, *, repo_name: str | None = None) -> ActionPackResponse:
        from app.packs.service import derive_priority  # noqa: PLC0415

        return cls(
            id=row.id,
            name=row.name,
            source_type=row.source_type,
            git_repository_id=row.git_repository_id,
            git_repository_name=repo_name,
            path=row.path,
            local_path=row.local_path,
            role=row.role,
            priority=derive_priority(row),
            enabled=row.enabled,
            last_synced_at=row.last_synced_at,
            last_sync_status=row.last_sync_status,
            last_sync_error=row.last_sync_error,
            current_sha=row.current_sha,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class ActionPackSyncResponse(BaseModel):
    """Result of a manual sync or post-mutation sync attempt."""

    success: bool
    message: str
    current_sha: str | None = None
    last_synced_at: datetime | None = None
