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
    ``existing_key_winners`` map pre-selects ``role=override`` for any
    pack contributing a key that already has an owner; the
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
