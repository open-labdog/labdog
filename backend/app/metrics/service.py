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
    DriftCoverage,
    DriftTrendPoint,
    DriftTrendSeries,
    SyncRatePoint,
    SyncRateSeries,
)
from app.models.drift_sample import DriftSample
from app.models.host import Host
from app.models.host_module_status import HostModuleStatus
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


async def get_drift_coverage(db: AsyncSession) -> DriftCoverage:
    """How much of the fleet has any drift checking switched on at all.

    Answers the question every empty drift surface raises and none of them
    could: are there no results because nothing has drifted, or because
    nothing is being checked? On a fresh install it is always the second —
    every flag defaults to off and the sweep runs on schedule finding no
    candidates, indefinitely and silently.

    Both flags are counted because they are independent (see
    ``docs/ui/drift-detection.md``). ``Host.drift_check_enabled`` gates the
    firewall sweep and nothing else; the other six modules each read their
    own ``HostModuleStatus`` row. A fleet with every service-drift row on
    and every host flag off is fully covered for six modules and not at all
    for firewall — one number could not say that, so this returns three.
    """
    hosts_total = await db.scalar(select(func.count()).select_from(Host)) or 0
    firewall_hosts = (
        await db.scalar(
            select(func.count()).select_from(Host).where(Host.drift_check_enabled.is_(True))
        )
        or 0
    )
    module_hosts = (
        await db.scalar(
            select(func.count(func.distinct(HostModuleStatus.host_id))).where(
                HostModuleStatus.drift_check_enabled.is_(True),
                # The firewall module's own column has no writer and is
                # always false; counting it would be counting nothing.
                HostModuleStatus.module_type != "firewall",
            )
        )
        or 0
    )
    # The union, not the sum: a host with both kinds on is one covered host.
    any_hosts = (
        await db.scalar(
            select(func.count(func.distinct(Host.id)))
            .select_from(Host)
            .outerjoin(
                HostModuleStatus,
                (HostModuleStatus.host_id == Host.id)
                & HostModuleStatus.drift_check_enabled.is_(True)
                & (HostModuleStatus.module_type != "firewall"),
            )
            .where(Host.drift_check_enabled.is_(True) | HostModuleStatus.id.is_not(None))
        )
        or 0
    )
    return DriftCoverage(
        hosts_total=hosts_total,
        firewall_enabled_hosts=firewall_hosts,
        module_enabled_hosts=module_hosts,
        any_enabled_hosts=any_hosts,
    )
