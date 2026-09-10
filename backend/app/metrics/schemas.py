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
