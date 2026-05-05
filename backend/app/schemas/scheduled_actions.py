"""Pydantic schemas for the unified scheduled-actions API.

The public shape mirrors the internal ``ScheduledAction`` model but
adds a few convenience fields (``target_name``, ``action_name``,
``destructive``, ``last_run``) that the listing endpoint resolves
server-side so the UI doesn't need to make N+1 lookup calls.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ScheduledActionTargetKind = Literal["host", "group", "fleet"]


class ScheduledActionRunSummary(BaseModel):
    """Newest action_runs row for a schedule, rolled up for the listing."""

    id: int
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class ScheduledActionIn(BaseModel):
    """Body for POST /api/scheduled-actions and PUT /api/scheduled-actions/{id}."""

    target_kind: ScheduledActionTargetKind
    target_id: int | None = None
    action_key: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    schedule_cron: str | None = None
    enabled: bool = False
    snapshot_enabled: bool = True
    verify_enabled: bool = True
    auto_rollback: bool = True
    batch_size: int = Field(default=1, ge=1)


class ScheduledActionOut(BaseModel):
    id: int
    target_kind: ScheduledActionTargetKind
    target_id: int | None = None
    action_key: str
    parameters: dict[str, Any]
    schedule_cron: str | None = None
    enabled: bool
    snapshot_enabled: bool
    verify_enabled: bool
    auto_rollback: bool
    batch_size: int
    last_dispatched_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    # Server-resolved presentation helpers — populated by the API
    # listing/detail endpoints so the UI can render rows without
    # refetching hosts/groups/actions in a loop.
    target_name: str | None = None
    action_name: str | None = None
    pack_name: str | None = None
    destructive: bool | None = None
    last_run: ScheduledActionRunSummary | None = None

    model_config = ConfigDict(from_attributes=True)


class ValidateCronRequest(BaseModel):
    cron: str


class ValidateCronResponse(BaseModel):
    valid: bool
    message: str | None = None
    next_run_at: list[datetime] = Field(default_factory=list)
