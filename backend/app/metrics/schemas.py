"""Shared Pydantic response shapes for the dashboard charts.

Consumed by ``app.api.dashboard``, backed by ``app.metrics.service``'s
time-bucketed aggregations. The Prometheus ``/metrics`` exporter does
**not** use these — its aggregations (``app.metrics.aggregates``) return
plain tuples/dataclasses and are rendered as exposition-format text by
``app.metrics.exposition``, not JSON, so there's no shared Pydantic shape
between the two. See ``app.metrics.__init__`` for the full split.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Granularity = Literal["day", "hour"]


class SyncRatePoint(BaseModel):
    bucket: datetime
    total: int
    success: int
    failed: int
    success_rate: float | None


class SyncRateSeries(BaseModel):
    granularity: Granularity
    since: datetime
    points: list[SyncRatePoint]


class DriftTrendPoint(BaseModel):
    bucket: datetime
    checks: int
    drifted_checks: int
    total_drift: int


class DriftTrendSeries(BaseModel):
    granularity: Granularity
    since: datetime
    points: list[DriftTrendPoint]


class DriftCoverage(BaseModel):
    """Fleet-wide drift-check coverage, for telling "nothing drifted" apart
    from "nothing is being checked"."""

    hosts_total: int
    #: Hosts with ``Host.drift_check_enabled`` — the firewall sweep's gate.
    firewall_enabled_hosts: int
    #: Distinct hosts with at least one non-firewall module enabled.
    module_enabled_hosts: int
    #: Distinct hosts covered by either flag. Not the sum of the two above —
    #: a host with both kinds on is one covered host.
    any_enabled_hosts: int


# ---------------------------------------------------------------------------
# Point-in-time reads for the Overview tabs (``app.metrics.overview``).
# ---------------------------------------------------------------------------


class StuckOperation(BaseModel):
    """An operation that has held its host(s) for longer than the sweeper's
    stale threshold, and what is waiting for it to finish."""

    kind: Literal["sync", "action"]
    #: The SyncJob id for a sync, the ActionRun id for an action.
    id: int
    #: The action key, or the modules a sync covers.
    label: str
    #: Every host the operation still holds; one for a sync.
    host_ids: list[int]
    hostnames: list[str]
    #: When its earliest still-running host started.
    started_at: datetime
    #: Distinct operations deferred (``pending``) on those hosts. They
    #: cannot start until this one lets go.
    queued_behind: int


class StuckOperations(BaseModel):
    threshold_minutes: int
    items: list[StuckOperation]


class ModuleStatusCount(BaseModel):
    """Hosts in one sync status for one module, from ``host_module_status``."""

    module_type: str
    sync_status: str
    count: int


class CACertExpiry(BaseModel):
    """One CA certificate LabDog deploys, with when it stops being valid."""

    name: str
    fingerprint_sha256: str
    not_after: datetime
    #: Distinct hosts it is deployed to, through any group or host rule.
    host_count: int
    #: Where it is declared: ``group <name>`` / ``host <name>``.
    scopes: list[str]
