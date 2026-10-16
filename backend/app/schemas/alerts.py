"""Schemas for ``/api/alerts``: the actions alerts may run, intake, hosts.

The alert list itself and its rows (``AlertEventResponse``) live in
:mod:`app.ai.schemas`, where they were before alerts had a namespace of
their own.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AlertActionTargetKind = Literal["host", "group", "fleet"]


class AlertActionIn(BaseModel):
    """Body for POST /api/alerts/actions and PUT /api/alerts/actions/{id}.

    The target is where alerts may run the action, not what a run covers:
    a run is always the one host the alert names.
    """

    action_key: str = Field(min_length=1, max_length=64)
    target_kind: AlertActionTargetKind = "fleet"
    target_id: int | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    snapshot_enabled: bool = True
    verify_enabled: bool = True
    auto_rollback: bool = True
    #: No second alert run of the action on the same host within this
    #: many minutes. 0 means none.
    cooldown_minutes: int = Field(default=60, ge=0, le=10080)
    enabled: bool = True


class AlertActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action_key: str
    target_kind: AlertActionTargetKind
    target_id: int | None = None
    parameters: dict[str, Any]
    snapshot_enabled: bool
    verify_enabled: bool
    auto_rollback: bool
    cooldown_minutes: int
    enabled: bool
    created_at: datetime
    updated_at: datetime
    #: Resolved server-side. ``action_name`` is None when no loaded pack
    #: has the action any more; alerts naming it are then refused.
    action_name: str | None = None
    pack_name: str | None = None
    destructive: bool = False
    target_name: str | None = None
    #: The newest run an alert started with this entry.
    last_run_id: int | None = None
    last_run_status: str | None = None
    last_run_at: datetime | None = None


class AlertIntakeStatus(BaseModel):
    """Whether alerts can reach LabDog, for the Alerts › Handling tab.

    Says whether the webhook token is set, and whether it is long enough
    for an alert to run an action or full auto, never what it is: the
    token is a shared secret kept in the config file precisely so that no
    API returns it.
    """

    intake_enabled: bool
    webhook_path: str
    #: An unset token makes the webhook refuse every request.
    token_set: bool
    #: At least MIN_WEBHOOK_TOKEN_CHARS: below it, a webhook alert never
    #: runs an action or runs at full auto.
    token_long_enough_for_full_auto: bool
    #: Whether an alert labelled labdog_ai / labdog_ai_automate starts an
    #: investigation (ai.enabled and ai.auto_investigate_enabled).
    ai_enabled: bool
    auto_investigate_enabled: bool
    #: Off: the labdog_ai_automate=true label decides full auto. On: the rules.
    full_auto_rules_enabled: bool
    #: How many rules are in force: 1 (the label) while the rules are off.
    full_auto_rules: int
    #: Enabled entries on the list of actions alerts may run.
    allowed_actions: int
    poll_minutes: int
    #: When the newest alert first reached LabDog by each route.
    last_webhook_alert_at: datetime | None
    last_poll_alert_at: datetime | None


class HostAlertActionOut(BaseModel):
    """An action alerts may run on a host, from the list."""

    id: int
    action_key: str
    action_name: str | None = None
    #: ``all hosts``, ``host web-1`` or ``group web``.
    scope: str
    enabled: bool


class HostAlertsResponse(BaseModel):
    """What alerts may do to one host, for its page.

    Worked out from the list and the rules rather than stored. ``rules``
    are the full-auto rules whose host and group conditions allow this
    host (their alert-name and label conditions still have to match an
    alert). ``blockers`` are the safeguards that would hold every alert's
    full auto back on this host, as far as they can be known without an
    alert — including the ones that hold only for now, such as the 24 h
    pause after a fix made the host worse.
    """

    ai_enabled: bool
    auto_investigate_enabled: bool
    rules_enabled: bool
    rules: list[str]
    blockers: list[str]
    #: The actions alerts may run here. Shown whether or not the AI is on:
    #: they need no AI.
    alert_actions: list[HostAlertActionOut] = []
