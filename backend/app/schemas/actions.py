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
    #: Pack names whose entries for the same key were shadowed by this
    #: one, in processing order. Non-empty only on collisions.
    overridden_from: list[str] = []


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
