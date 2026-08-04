"""Read-side aggregations powering the dashboard charts.

Both functions are pure read/aggregation over existing tables (``SyncJob``
and ``DriftSample``), bucketed by ``date_trunc`` into a time series — this
is dashboard-only. The Prometheus ``/metrics`` exporter does **not** reuse
these: Prometheus needs current point-in-time values (its TSDB does its own
bucketing over scrape history, and a back-dated sample is rejected as
out-of-order on the next scrape), so it has its own point-in-time
aggregations in ``app.metrics.aggregates`` instead. See
``app.metrics.__init__`` for the full three-way split (recorder / service /
aggregates).
"""

from datetime import datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.metrics.schemas import (
    DriftTrendPoint,
    DriftTrendSeries,
    SyncRatePoint,
    SyncRateSeries,
)
from app.models.drift_sample import DriftSample
from app.models.sync_job import JobStatus, SyncJob

_TERMINAL_STATUSES = (JobStatus.success, JobStatus.failed, JobStatus.cancelled)


async def get_sync_success_rate(
    db: AsyncSession,
    *,
    since: datetime,
    granularity: Literal["day", "hour"],
    module: str | None = None,
) -> SyncRateSeries:
    """Bucket terminal ``SyncJob`` rows by ``created_at`` and compute success rate.

    Only terminal statuses (success / failed / cancelled) count toward the
    totals — in-flight (pending / running) jobs are excluded since they
    haven't resolved yet.
    """
    bucket = func.date_trunc(granularity, SyncJob.created_at).label("bucket")
    query = (
        select(
            bucket,
            func.count().label("total"),
            func.count().filter(SyncJob.status == JobStatus.success).label("success"),
            func.count().filter(SyncJob.status == JobStatus.failed).label("failed"),
        )
        .where(SyncJob.status.in_(_TERMINAL_STATUSES))
        .where(SyncJob.created_at >= since)
    )
    if module is not None:
        query = query.where(SyncJob.module_type == module)
    query = query.group_by(bucket).order_by(bucket)

    result = await db.execute(query)
    points = [
        SyncRatePoint(
            bucket=row.bucket,
            total=row.total,
            success=row.success,
            failed=row.failed,
            success_rate=(row.success / row.total) if row.total else None,
        )
        for row in result.all()
    ]
    return SyncRateSeries(granularity=granularity, since=since, points=points)


async def get_drift_trend(
    db: AsyncSession,
    *,
    since: datetime,
    granularity: Literal["day", "hour"],
    module: str | None = None,
) -> DriftTrendSeries:
    """Bucket ``DriftSample`` rows by ``checked_at`` into a drift trend series."""
    bucket = func.date_trunc(granularity, DriftSample.checked_at).label("bucket")
    total_drift = func.sum(
        DriftSample.add_count + DriftSample.remove_count + DriftSample.policy_change_count
    ).label("total_drift")
    query = select(
        bucket,
        func.count().label("checks"),
        func.count().filter(DriftSample.status == "out_of_sync").label("drifted_checks"),
        total_drift,
    ).where(DriftSample.checked_at >= since)
    if module is not None:
        query = query.where(DriftSample.module_type == module)
    query = query.group_by(bucket).order_by(bucket)

    result = await db.execute(query)
    points = [
        DriftTrendPoint(
            bucket=row.bucket,
            checks=row.checks,
            drifted_checks=row.drifted_checks,
            total_drift=int(row.total_drift or 0),
        )
        for row in result.all()
    ]
    return DriftTrendSeries(granularity=granularity, since=since, points=points)
