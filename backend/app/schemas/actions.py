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

    model_config = {"from_attributes": True}


class ActionRunOut(BaseModel):
    id: int
    action_key: str
    action_version: str
    host_id: int | None
    group_id: int | None
    parameters: dict
    parallelism: int
    status: str
    triggered_by_user_id: int | None
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    created_at: datetime
    host_runs: list[ActionHostRunOut] = []

    model_config = {"from_attributes": True}
