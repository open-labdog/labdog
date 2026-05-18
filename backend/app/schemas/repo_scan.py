"""Pydantic response schemas for the repo-scan endpoint.

These mirror the dataclasses in ``app.packs.repo_scanner`` and
``app.packs.scan_conflicts``. Kept as a separate module so the API
surface evolves independently of the internal scan types — the
internal layer can grow fields freely as long as the schema layer
maps what the wizard needs.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ScanErrorOut(BaseModel):
    file: str
    message: str
    model_config = ConfigDict(from_attributes=True)


class DetectedPackOut(BaseModel):
    path: str
    name: str
    contributed_keys: list[str]
    pack_yml_present: bool
    errors: list[ScanErrorOut] = []
    model_config = ConfigDict(from_attributes=True)


class DetectedGitopsFileOut(BaseModel):
    path: str
    group_name: str | None
    errors: list[ScanErrorOut] = []
    model_config = ConfigDict(from_attributes=True)


class KeyOwnerOut(BaseModel):
    key: str
    source: Literal["bundled", "db_pack"]
    pack_name: str
    pack_id: int | None = None
    model_config = ConfigDict(from_attributes=True)


class KeyConflictOut(BaseModel):
    key: str
    contributing_packs: list[str]
    model_config = ConfigDict(from_attributes=True)


class RepoScanResponse(BaseModel):
    """The full annotated scan result the wizard renders in step (c).

    ``packs`` and ``gitops_files`` are the raw findings; the
    ``existing_key_winners`` map drives the wizard's per-key radio for
    every pack contributing a key that already has an owner; the
    ``intra_repo_key_conflicts`` list disables the Activate button
    while any conflict still has both contributing packs checked.
    ``scan_errors`` covers infrastructure-level issues (e.g. clone
    path not a directory) — should be empty in the happy path.
    """

    packs: list[DetectedPackOut]
    gitops_files: list[DetectedGitopsFileOut]
    existing_key_winners: dict[str, KeyOwnerOut]
    intra_repo_key_conflicts: list[KeyConflictOut]
    scan_errors: list[ScanErrorOut]
    head_sha: str | None


# ---------------------------------------------------------------------------
# Activation request / response
# ---------------------------------------------------------------------------


class ActivatePackSelection(BaseModel):
    """One operator-selected pack to activate from a scan result.

    ``path`` matches the corresponding ``DetectedPackOut.path``. ``name``
    is the desired ``ActionPack.name``; if it collides with an existing
    pack, the activation endpoint suffixes it (``-<repo_name>``, then
    ``-<short_sha>``) and reports the final name in the response.

    Packs have no inherent precedence — per-key pins via
    ``action_resolution`` decide every contested winner. The
    activation endpoint also writes a pin row for every key the
    submitted ``key_resolutions`` list identifies.
    """

    path: str
    name: str


class ActivateKeyResolution(BaseModel):
    """One operator decision for an action key contested by activation.

    The wizard surfaces a per-key radio for every key contributed by
    a newly-activated pack that collides with an existing pack
    (including bundled). ``winner`` identifies which pack should win
    the key on the rebuilt registry.

    ``winner_pack_path`` references one of the submitted ``packs[].path``
    entries — the activation endpoint resolves the path to the
    just-inserted pack id and writes an ``action_resolution`` row.
    Mutually exclusive with the bundled / existing-pack winner forms;
    exactly one of the four winner fields must be set.
    """

    action_key: str
    winner_pack_path: str | None = None
    """Path inside the submitted activation set whose pack wins."""
    winner_existing_pack_id: int | None = None
    """An existing DB pack wins (operator kept the prior winner)."""
    winner_is_bundled: bool = False
    """Bundled wins — emits a row with ``pack_id NULL``."""


class ActivateGitopsBinding(BaseModel):
    """Bind one detected gitops file to one existing HostGroup."""

    file_path: str
    host_group_id: int


class RepoActivateRequest(BaseModel):
    packs: list[ActivatePackSelection] = []
    gitops_bindings: list[ActivateGitopsBinding] = []
    key_resolutions: list[ActivateKeyResolution] = []
    """Per-key winner decisions for keys contested by this activation.
    The wizard must submit one row for every key that becomes
    contested when the requested packs are added; the activation
    endpoint rejects the request otherwise."""


class ActivatedPackOut(BaseModel):
    """One pack as actually persisted (with the post-collision name)."""

    pack_id: int
    name: str
    path: str
    requested_name: str
    name_was_disambiguated: bool
    model_config = ConfigDict(from_attributes=True)


class ActivatedGitopsBindingOut(BaseModel):
    host_group_id: int
    file_path: str
    model_config = ConfigDict(from_attributes=True)


class RepoActivateResponse(BaseModel):
    """What the wizard's Activate step renders in the success toast.

    Lists every pack that was actually inserted (disambiguated names
    visible) and every group binding that was applied. ``head_sha``
    is the commit the activation was validated against — operator
    can compare against the scan's ``head_sha`` to confirm nothing
    moved underneath them.
    """

    activated_packs: list[ActivatedPackOut]
    activated_gitops_bindings: list[ActivatedGitopsBindingOut]
    head_sha: str | None
