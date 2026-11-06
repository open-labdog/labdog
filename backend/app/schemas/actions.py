from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

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
    #: exists yet). Also ``None`` for built-in pseudo-actions; the
    #: frontend tells the two apart via ``unresolved``.
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
    #: Optional mapping of this action's playbook vars onto a Grafana
    #: metrics backend (see ``ActionManifest.metrics_backend``). When
    #: present, the run dialog renders the mapped URL params as
    #: registered-instance pickers instead of free-text. ``None`` when the
    #: action doesn't integrate with Grafana.
    metrics_backend: dict[str, str] | None = None


class RunCreateBody(BaseModel):
    action_key: str
    host_id: int | None = None
    group_id: int | None = None
    parameters: dict[str, Any] = {}
    parallelism: int = 1
    dry_run: bool = False
    #: Grafana instances the operator picked in the run dialog for an
    #: action that declares ``metrics_backend``, by kind (``mimir`` /
    #: ``loki``). Overrides every target host's assigned destination for
    #: this run. A kind left out uses each host's own destination.
    metrics_destinations: dict[Literal["mimir", "loki"], int] | None = None


class ActionHostRunOut(BaseModel):
    id: int
    action_run_id: int
    #: NULL once the target host has been deleted. The run and its
    #: transcript survive that (BUG-77), so anything keyed on this — a
    #: link to the host page, a re-run button — has to handle None.
    host_id: int | None = None
    #: The host's name. Read from the live host row when it still exists,
    #: otherwise from the snapshot taken at dispatch, so it is always a
    #: usable label even after the host is gone.
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


class RunInvestigateBody(BaseModel):
    #: Investigate this host's part of the run only. Omitted: the whole run.
    host_run_id: int | None = None


class RunInvestigationOut(BaseModel):
    session_id: int
    #: False when an earlier investigation of the same hosts was reopened
    #: rather than a new one started.
    created: bool


class RunInvestigationRef(BaseModel):
    """An assistant session started from this run's page."""

    session_id: int
    status: str
    #: The hosts it may touch: every host of the run, or the one asked about.
    target_host_ids: list[int]
    created_at: datetime


class RunHostFailure(BaseModel):
    """The first host a run failed on, for a one-line reason in a list."""

    host_run_id: int
    host_id: int | None = None
    hostname: str
    exit_code: int | None = None
    error_message: str | None = None
    #: The first failed task's message, read from the host's log —
    #: ``error_message`` only says ansible-runner exited non-zero.
    reason: str | None = None


class HostRunEntry(BaseModel):
    """One host's part in a run, for that host's own history.

    A group or fleet run lists here too: the row is this host's result,
    so a run that was ``partial`` overall reads ``succeeded`` on a host it
    did not fail on.
    """

    host_run_id: int
    action_run_id: int
    action_key: str
    #: The run's overall status; ``status`` is this host's.
    run_status: str
    status: str
    target_kind: str
    target_label: str
    scheduled_action_id: int | None = None
    alert_event_id: int | None = None
    triggered_by_user_id: int | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None
    error_message: str | None = None
    #: The first failed task's message from this host's log, when it failed.
    reason: str | None = None
    #: The run failed here and LabDog restored the pre-run snapshot.
    rolled_back: bool = False


class ActionRunOut(BaseModel):
    id: int
    action_key: str
    action_version: str
    host_id: int | None
    group_id: int | None
    #: What the run targeted, recorded at dispatch time. Survives the
    #: target being deleted, which sets ``host_id``/``group_id`` to NULL:
    #: ``target_label`` is then the only description of what ran.
    target_kind: str
    target_label: str
    #: NULL for ad-hoc runs; populated when the run was dispatched by
    #: the unified scheduler or POST /api/scheduled-actions/{id}/run-now.
    scheduled_action_id: int | None = None
    #: Set when an alert's labdog_action label started the run. The name
    #: is filled in by the run detail endpoint.
    alert_event_id: int | None = None
    alertname: str | None = None
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
    #: How many of the run's hosts are in each status, e.g.
    #: ``{"succeeded": 17, "failed": 1}``. Filled in by the list endpoint,
    #: which leaves ``host_runs`` empty, so a group run can read
    #: "17 ok, 1 failed" without fetching every host row.
    host_counts: dict[str, int] = {}
    #: The first host that failed (lowest host-run id), filled in by the
    #: list endpoint alongside ``host_counts``.
    first_failure: RunHostFailure | None = None
    #: Filled in by the run detail endpoint, newest first.
    investigations: list[RunInvestigationRef] = []

    model_config = {"from_attributes": True}
