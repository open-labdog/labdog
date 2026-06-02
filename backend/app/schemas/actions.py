from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ActionParameterOut(BaseModel):
    key: str
    label: str
    type: str
    default: Any = None
    required: bool = False
    choices: list[str] | None = None
    help_text: str | None = None


class ActionDefinitionOut(BaseModel):
    key: str
    name: str
    description: str
    icon: str
    version: str
    estimated_duration: str
    destructive: bool
    supports_group: bool
    supports_host: bool
    #: Whether this action makes sense across the entire fleet. Drives
    #: the Fleet target option in the schedule dialog.
    supports_fleet: bool = False
    parameters: list[ActionParameterOut]
    #: Pack whose manifest is currently active for this action key.
    pack_name: str
    #: ``ActionPack.id`` of the winning pack — ``None`` when the key
    #: is **unresolved** (multiple packs declare it and no operator pin
    #: exists yet). Also ``None`` for built-in pseudo-actions and for
    #: bundled-pack actions (the bundled pack has no DB row); the
    #: frontend distinguishes these via ``pack_name`` /
    #: ``unresolved``.
    winning_pack_id: int | None = None
    #: True when the action key is contested by multiple packs and the
    #: operator has not pinned a winner. The Run button must be
    #: disabled with a "Pick winner first" prompt; ``POST
    #: /api/actions/runs`` rejects unresolved actions with HTTP 409.
    unresolved: bool = False
    #: Pack names that also declare this key (in stable sorted order).
    #: For uncontested keys, empty. For contested+pinned keys, every
    #: other contributor. For unresolved keys, every contributor
    #: (including the placeholder one whose metadata was used for
    #: display).
    overridden_from: list[str] = []
    #: Canonical module names that will re-sync against the target host
    #: after a successful run (per-host fan-out for group dispatch).
    #: Empty list means no post-run sync. Surfaced as a chip on the
    #: action card / run detail so operators see the side effect.
    post_run_sync: list[str] = []
    #: Resources the action's manifest declares it installs. After a
    #: successful run labdog inserts these as host-scope overrides so
    #: the resources are managed going forward. Keys are canonical
    #: module names; values are per-item dicts validated against the
    #: module's Create schema. See ``ActionManifest.post_run_register``.
    post_run_register: dict[str, list[dict[str, Any]]] = {}


class RunCreateBody(BaseModel):
    action_key: str
    host_id: int | None = None
    group_id: int | None = None
    parameters: dict[str, Any] = {}
    parallelism: int = 1
    dry_run: bool = False


class ActionHostRunOut(BaseModel):
    id: int
    action_run_id: int
    host_id: int
    #: Hostname of the target host, resolved at serialization time for
    #: display. NULL only if the host row was deleted out from under the
    #: run (host_id FK is ON DELETE CASCADE, so in practice always set).
    hostname: str | None = None
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    exit_code: int | None
    error_message: str | None
    snapshot_name: str | None = None
    #: Populated when ``status='pending'`` — human-readable string naming
    #: the in-flight op holding the host. NULL otherwise.
    pending_reason: str | None = None

    model_config = {"from_attributes": True}


class ActionRunOut(BaseModel):
    id: int
    action_key: str
    action_version: str
    host_id: int | None
    group_id: int | None
    #: NULL for ad-hoc runs; populated when the run was dispatched by
    #: the unified scheduler or POST /api/scheduled-actions/{id}/run-now.
    scheduled_action_id: int | None = None
    parameters: dict
    parallelism: int
    #: Universal destructive-flow toggles, mirrored from the schedule
    #: at dispatch time. Ignored when the action is non-destructive.
    snapshot_enabled: bool = True
    verify_enabled: bool = True
    auto_rollback: bool = True
    status: str
    triggered_by_user_id: int | None
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    #: Populated when ``status='pending'`` — human-readable string naming
    #: the in-flight op holding the target host. NULL otherwise.
    pending_reason: str | None = None
    created_at: datetime
    host_runs: list[ActionHostRunOut] = []

    model_config = {"from_attributes": True}
