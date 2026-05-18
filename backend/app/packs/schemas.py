from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.packs.models import PackSourceType


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
