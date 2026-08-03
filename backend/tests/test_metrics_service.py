"""Unit tests for app.metrics.service aggregations.

Uses the real Postgres testcontainer (via the shared ``db`` fixture) because
``date_trunc`` and ``COUNT(*) FILTER (...)`` are Postgres-specific SQL that
can't be exercised against SQLite or a mock.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.metrics.service import get_drift_trend, get_sync_success_rate
from app.models.drift_sample import DriftSample
from app.models.sync_job import JobStatus, SyncJob

from .conftest import create_host

pytestmark = pytest.mark.integration


def _day_start(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


class TestSyncSuccessRate:
    @pytest.mark.asyncio
    async def test_buckets_by_day_and_computes_success_rate(self, db: AsyncSession):
        host = await create_host(db)
        now = datetime.now(UTC)
        today = _day_start(now)
        yesterday = today - timedelta(days=1)

        # Today: 3 success, 1 failed -> success_rate 0.75
        for _ in range(3):
            db.add(
                SyncJob(
                    host_id=host.id,
                    status=JobStatus.success,
                    module_type="firewall",
                    created_at=today + timedelta(hours=1),
                )
            )
        db.add(
            SyncJob(
                host_id=host.id,
                status=JobStatus.failed,
                module_type="firewall",
                created_at=today + timedelta(hours=2),
            )
        )
        # Yesterday: 1 failed only -> success_rate 0.0
        db.add(
            SyncJob(
                host_id=host.id,
                status=JobStatus.failed,
                module_type="firewall",
                created_at=yesterday + timedelta(hours=1),
            )
        )
        # Non-terminal status must be excluded from totals.
        db.add(
            SyncJob(
                host_id=host.id,
                status=JobStatus.pending,
                module_type="firewall",
                created_at=today + timedelta(hours=3),
            )
        )
        await db.flush()

        series = await get_sync_success_rate(
            db, since=yesterday - timedelta(hours=1), granularity="day"
        )

        assert series.granularity == "day"
        by_bucket = {p.bucket.replace(tzinfo=UTC): p for p in series.points}
        today_point = by_bucket[today]
        assert today_point.total == 4
        assert today_point.success == 3
        assert today_point.failed == 1
        assert today_point.success_rate == pytest.approx(0.75)

        yesterday_point = by_bucket[yesterday]
        assert yesterday_point.total == 1
        assert yesterday_point.success == 0
        assert yesterday_point.failed == 1
        assert yesterday_point.success_rate == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_module_filter(self, db: AsyncSession):
        host = await create_host(db)
        today = _day_start(datetime.now(UTC))

        db.add(
            SyncJob(
                host_id=host.id,
                status=JobStatus.success,
                module_type="firewall",
                created_at=today + timedelta(hours=1),
            )
        )
        db.add(
            SyncJob(
                host_id=host.id,
                status=JobStatus.success,
                module_type="package",
                created_at=today + timedelta(hours=1),
            )
        )
        await db.flush()

        series = await get_sync_success_rate(
            db, since=today - timedelta(days=1), granularity="day", module="firewall"
        )
        assert len(series.points) == 1
        assert series.points[0].total == 1

    @pytest.mark.asyncio
    async def test_empty_when_no_rows(self, db: AsyncSession):
        series = await get_sync_success_rate(
            db, since=datetime.now(UTC) - timedelta(days=1), granularity="day"
        )
        assert series.points == []


class TestDriftTrend:
    @pytest.mark.asyncio
    async def test_aggregates_counts_and_drift_volume(self, db: AsyncSession):
        host = await create_host(db)
        today = _day_start(datetime.now(UTC))

        db.add(
            DriftSample(
                host_id=host.id,
                module_type="firewall",
                status="out_of_sync",
                add_count=2,
                remove_count=1,
                policy_change_count=1,
                checked_at=today + timedelta(hours=1),
            )
        )
        db.add(
            DriftSample(
                host_id=host.id,
                module_type="firewall",
                status="in_sync",
                add_count=0,
                remove_count=0,
                policy_change_count=0,
                checked_at=today + timedelta(hours=2),
            )
        )
        await db.flush()

        series = await get_drift_trend(db, since=today - timedelta(days=1), granularity="day")
        assert len(series.points) == 1
        point = series.points[0]
        assert point.checks == 2
        assert point.drifted_checks == 1
        assert point.total_drift == 4

    @pytest.mark.asyncio
    async def test_module_filter(self, db: AsyncSession):
        host = await create_host(db)
        today = _day_start(datetime.now(UTC))

        db.add(
            DriftSample(
                host_id=host.id,
                module_type="firewall",
                status="out_of_sync",
                add_count=1,
                checked_at=today + timedelta(hours=1),
            )
        )
        db.add(
            DriftSample(
                host_id=host.id,
                module_type="package",
                status="out_of_sync",
                add_count=5,
                checked_at=today + timedelta(hours=1),
            )
        )
        await db.flush()

        series = await get_drift_trend(
            db, since=today - timedelta(days=1), granularity="day", module="package"
        )
        assert len(series.points) == 1
        assert series.points[0].total_drift == 5

    @pytest.mark.asyncio
    async def test_empty_when_no_rows(self, db: AsyncSession):
        series = await get_drift_trend(
            db, since=datetime.now(UTC) - timedelta(days=1), granularity="day"
        )
        assert series.points == []
