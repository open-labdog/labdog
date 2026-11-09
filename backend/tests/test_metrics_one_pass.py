"""BUG-160: one scan per table gives the same numbers as one per family.

Each scrape ran three queries over ``drift_samples`` and two over
``sync_jobs``. They are now one ``GROUP BY`` each. These tests seed a mix
that exercises every rule the separate queries had (NULL durations, a
module with no timed row, a rollup row with stale bounds, in-flight and
skewed sync jobs) and compare with values worked out by hand.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.metrics import aggregates as agg
from app.metrics.aggregates import _BUCKETS_DRIFT, _BUCKETS_SYNC
from app.models.drift_sample import DriftSample
from app.models.drift_sample_rollup import DriftSampleRollup
from app.models.sync_job import JobStatus, SyncJob
from tests.conftest import create_host

pytestmark = pytest.mark.integration


def _drift(host_id, module, status, *, adds=0, removes=0, policy=0, ms=None):
    return DriftSample(
        host_id=host_id,
        module_type=module,
        status=status,
        add_count=adds,
        remove_count=removes,
        policy_change_count=policy,
        duration_ms=ms,
        checked_at=datetime.now(UTC),
    )


def _buckets(seconds: list[float], bounds) -> list[int]:
    return [sum(1 for s in seconds if s <= b) for b in bounds]


async def test_drift_families_from_one_pass(db):
    host = await create_host(db)
    db.add_all(
        [
            _drift(host.id, "firewall", "in_sync", ms=400),
            _drift(host.id, "firewall", "out_of_sync", adds=2, removes=1, ms=3000),
            _drift(host.id, "firewall", "out_of_sync", adds=1, policy=1, ms=None),
            _drift(host.id, "service", "error", ms=None),
            _drift(host.id, "service", "in_sync", ms=70000),
            # Only untimed rows: counted, but no histogram.
            _drift(host.id, "cron", "in_sync", ms=None),
        ]
    )
    db.add_all(
        [
            DriftSampleRollup(
                module_type="firewall",
                status="out_of_sync",
                checks=10,
                add_count=5,
                remove_count=4,
                policy_change_count=3,
                duration_count=2,
                duration_sum_seconds=1.5,
                duration_buckets=[1, 2, 2, 2, 2, 2, 2],
                duration_bounds=list(_BUCKETS_DRIFT),
            ),
            # Bounds from an older release: its count and sum still add up,
            # its buckets do not.
            DriftSampleRollup(
                module_type="service",
                status="in_sync",
                checks=4,
                add_count=0,
                remove_count=0,
                policy_change_count=0,
                duration_count=4,
                duration_sum_seconds=8.0,
                duration_buckets=[4, 4],
                duration_bounds=[1.0, 2.0],
            ),
        ]
    )
    await db.flush()

    result = await agg.get_drift_aggregates(db)

    assert result.counts == [
        ("cron", "in_sync", 1),
        ("firewall", "in_sync", 1),
        ("firewall", "out_of_sync", 12),
        ("service", "error", 1),
        ("service", "in_sync", 5),
    ]
    assert result.change_sums == [
        ("cron", 0, 0, 0),
        ("firewall", 8, 5, 4),
        ("service", 0, 0, 0),
    ]
    hist = dict(result.duration)
    assert set(hist) == {"firewall", "service"}
    fw = hist["firewall"]
    assert fw.bucket_counts == [
        a + b for a, b in zip(_buckets([0.4, 3.0], _BUCKETS_DRIFT), [1, 2, 2, 2, 2, 2, 2])
    ]
    assert (fw.total_count, fw.total_sum) == (4, pytest.approx(0.4 + 3.0 + 1.5))
    svc = hist["service"]
    assert svc.bucket_counts == _buckets([70.0], _BUCKETS_DRIFT)
    assert (svc.total_count, svc.total_sum) == (5, pytest.approx(70.0 + 8.0))

    # The per-family entry points still answer the same.
    assert await agg.get_drift_counts(db) == result.counts
    assert await agg.get_drift_change_sums(db) == result.change_sums
    assert await agg.get_drift_duration_histogram(db) == result.duration


async def test_sync_job_counts_and_histogram_from_one_pass(db):
    host = await create_host(db)
    t0 = datetime.now(UTC) - timedelta(hours=1)

    def job(status, module, *, took=None, skew=False):
        started = t0 if took is not None or skew else None
        completed = None
        if took is not None:
            completed = t0 + timedelta(seconds=took)
        if skew:
            completed = t0 - timedelta(seconds=5)
        return SyncJob(
            host_id=host.id,
            status=status,
            module_type=module,
            started_at=started,
            completed_at=completed,
        )

    db.add_all(
        [
            job(JobStatus.success, "firewall", took=3),
            job(JobStatus.success, "packages", took=45),
            job(JobStatus.failed, "packages", took=700),
            job(JobStatus.running, "firewall"),
            job(JobStatus.pending, "bulk"),
            # completed before it started: counted, not timed.
            job(JobStatus.failed, "firewall", skew=True),
        ]
    )
    await db.flush()

    result = await agg.get_sync_job_aggregates(db)

    assert sorted(result.counts) == [
        ("failed", "firewall", 1),
        ("failed", "packages", 1),
        ("pending", "bulk", 1),
        ("running", "firewall", 1),
        ("success", "firewall", 1),
        ("success", "packages", 1),
    ]
    assert result.duration.bucket_counts == _buckets([3, 45, 700], _BUCKETS_SYNC)
    assert result.duration.total_count == 3
    assert result.duration.total_sum == pytest.approx(748.0)
    assert sorted(await agg.get_sync_job_counts(db)) == sorted(result.counts)
    assert await agg.get_sync_duration_histogram(db) == result.duration
