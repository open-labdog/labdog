"""Shared Pydantic response shapes for the dashboard charts.

These are consumed today by ``app.api.dashboard`` and are designed to be
reused unchanged by a future OpenMetrics ``/metrics`` exporter built on top
of the same ``app.metrics.service`` aggregations.
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
