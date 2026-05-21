from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.packs.models import PackSourceType

logger = logging.getLogger(__name__)

# Dangerous top-level absolute path prefixes that local_path must not sit under.
# Subpaths like /home/operator/packs are deliberately allowed — only the
# obviously-hazardous system locations are blocked.
_DANGEROUS_LOCAL_PATH_PREFIXES = (
    "/proc",
    "/sys",
    "/dev",
    "/etc",
    "/root",
    "/var/log",
    "/boot",
    "/run",
)

# Bare top-level directories that are themselves dangerous as pack roots
# (subpaths such as /home/operator/packs are fine).
_DANGEROUS_LOCAL_PATH_EXACT = {
    "/",
    "/usr",
    "/var",
    "/home",
}


def _validate_name(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("name must not be empty")
    if v == "bundled":
        raise ValueError("'bundled' is reserved for the built-in pack")
    return v


def _validate_pack_path(v: str) -> str:
    """Validate a git-pack ``path`` field (subpath within a checkout).

    - Empty string is allowed (means "pack lives at the repo root").
    - Rejects NUL bytes, control characters, backslashes, leading ``/``,
      and any ``..`` path component.
    - Enforces a 512-character length cap.
    """
    if "\x00" in v:
        raise ValueError("path must not contain NUL bytes")
    for ch in v:
        if ord(ch) < 0x20:
            raise ValueError(
                f"path must not contain control characters (found U+{ord(ch):04X})"
            )
    if "\\" in v:
        raise ValueError("path must not contain backslashes")
    if len(v) > 512:
        raise ValueError("path must be at most 512 characters")
    if v.startswith("/"):
        raise ValueError("path must be a relative path (must not start with '/')")
    # Reject any '..' component regardless of surrounding slashes or OS separator.
    for component in v.replace("\\", "/").split("/"):
        if component == "..":
            raise ValueError(
                "path must not contain '..' components (directory traversal rejected)"
            )
    return v


def _validate_local_path(v: str | None) -> str | None:
    """Validate a local-pack ``local_path`` field (absolute filesystem path).

    - ``None`` is allowed (field is optional on the model; the cross-field
      validator enforces presence when ``source_type=local``).
    - Rejects NUL bytes, control characters, backslashes, non-absolute paths,
      and paths that resolve under obviously-dangerous system directories.
    - Logs an advisory warning when the path does not currently exist on disk
      (the operator may be configuring it before the directory is created).
    """
    if v is None:
        return v
    if "\x00" in v:
        raise ValueError("local_path must not contain NUL bytes")
    for ch in v:
        if ord(ch) < 0x20:
            raise ValueError(
                f"local_path must not contain control characters (found U+{ord(ch):04X})"
            )
    if "\\" in v:
        raise ValueError("local_path must not contain backslashes")
    if len(v) > 512:
        raise ValueError("local_path must be at most 512 characters")
    if not v.startswith("/"):
        raise ValueError("local_path must be an absolute path (must start with '/')")

    resolved = str(Path(v).resolve())

    # Exact-match deny-list: the path itself is one of the bare top-level dirs.
    if resolved in _DANGEROUS_LOCAL_PATH_EXACT:
        raise ValueError(
            f"local_path {v!r} resolves to a dangerous system directory: {resolved}"
        )

    # Prefix deny-list: the path sits under a protected system subtree.
    for prefix in _DANGEROUS_LOCAL_PATH_PREFIXES:
        if resolved == prefix or resolved.startswith(prefix + "/"):
            raise ValueError(
                f"local_path {v!r} resolves under the protected prefix {prefix!r}"
            )

    # Advisory-only: warn if the path does not exist yet.
    if not Path(v).exists():
        logger.warning(
            "local_path %r does not exist on disk; it will be accepted but "
            "the pack will report a sync failure until the directory is created",
            v,
        )

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
    path: str = Field(default="", max_length=512)
    local_path: str | None = Field(default=None, max_length=512)
    enabled: bool = True

    _validate_name = field_validator("name")(classmethod(lambda cls, v: _validate_name(v)))

    @field_validator("path")
    @classmethod
    def _check_path(cls, v: str) -> str:
        return _validate_pack_path(v)

    @field_validator("local_path")
    @classmethod
    def _check_local_path(cls, v: str | None) -> str | None:
        return _validate_local_path(v)

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
    path: str | None = Field(default=None, max_length=512)
    local_path: str | None = Field(default=None, max_length=512)
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str | None) -> str | None:
        return _validate_name(v) if v is not None else v

    @field_validator("path")
    @classmethod
    def _check_path(cls, v: str | None) -> str | None:
        return _validate_pack_path(v) if v is not None else v

    @field_validator("local_path")
    @classmethod
    def _check_local_path(cls, v: str | None) -> str | None:
        return _validate_local_path(v)

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
    enabled: bool

    last_synced_at: datetime | None
    last_sync_status: str | None
    last_sync_error: str | None
    current_sha: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, row, *, repo_name: str | None = None) -> ActionPackResponse:
        return cls(
            id=row.id,
            name=row.name,
            source_type=row.source_type,
            git_repository_id=row.git_repository_id,
            git_repository_name=repo_name,
            path=row.path,
            local_path=row.local_path,
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


class ClaimAllKeysResponse(BaseModel):
    """Outcome of ``POST /api/action-packs/{id}/claim-all-keys``.

    The endpoint pins every action key contributed by the pack to
    this pack via ``action_resolution`` rows, overwriting any prior
    pins on other packs. Idempotent.

    - ``created`` — number of brand-new resolution rows inserted.
    - ``updated`` — number of pre-existing rows flipped to this pack.
    - ``skipped`` — number of keys that already pointed at this pack
      (no write needed).
    """

    model_config = ConfigDict(extra="forbid")

    created: int
    updated: int
    skipped: int


class ActionResolutionRequest(BaseModel):
    """Body for ``PUT /api/action-resolutions/{action_key}``.

    ``pack_id`` chooses the winner — ``None`` means bundled. The
    server validates the chosen pack actually contributes the key
    before persisting; otherwise the operator could pin a pack that
    doesn't even define the action.
    """

    model_config = ConfigDict(extra="forbid")

    pack_id: int | None = None


class ActionResolutionPackOut(BaseModel):
    """One pack participating in a contested key."""

    model_config = ConfigDict(from_attributes=True)

    pack_id: int | None
    """``None`` for bundled — bundled has no DB row."""
    pack_name: str


class ContestedActionKeyOut(BaseModel):
    """One row in ``GET /api/action-resolutions``.

    Surfaces every action key currently contributed by more than one
    pack, with the candidates, the operator's explicit pick (if any),
    and whether the key is currently unresolved (no winner; the
    action is unrunnable).
    """

    action_key: str
    candidates: list[ActionResolutionPackOut]
    current_winner: ActionResolutionPackOut | None = None
    """The pinned winner, or ``None`` when the key is unresolved."""
    resolution: ActionResolutionPackOut | None = None
    """Set when an explicit ``action_resolution`` row pins a winner;
    ``None`` means there is no pin (and the key is unresolved)."""
    is_frozen: bool = False
    """True when the live winner was auto-pinned by the
    freeze-on-fresh-conflict logic (a sync introduced a fresh
    contestant; the previous winner was pinned to preserve behaviour).
    The UI surfaces a "needs your confirmation" badge for these."""
    is_unresolved: bool = False
    """True when the key has multiple contributors and no operator pin.
    The action is unrunnable until ``resolution`` is set."""
    decided_at: datetime | None = None
    decided_by_user_id: int | None = None
