"""Drift-sample retention must not make a Prometheus counter go backwards.

``labdog_drift_checks_total``, ``labdog_drift_changes_total`` and
``labdog_drift_check_duration_seconds`` are all derived from the whole of
``drift_samples`` with no time window, and all three are counters. Deleting
old rows makes them decrease, which Prometheus reads as a process restart:
``rate()`` copes, ``increase()`` across the deletion silently under-reports,
and nobody is told.

So the retention job folds what it deletes into ``drift_sample_rollup`` and
the exporter reports live + rollup. These tests are about that sum being
exact, and about the fold and the delete sharing one transaction.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.metrics import aggregates as agg
from app.metrics.aggregates import _BUCKETS_DRIFT
from app.models.drift_sample import DriftSample
from app.models.drift_sample_rollup import DriftSampleRollup
from app.tasks import drift_retention
from tests.conftest import create_host


@pytest.fixture
async def host_id(db) -> int:
    host = await create_host(db, hostname="drift-retention")
    return host.id


def _sample(
    host_id: int,
    days_ago: int,
    *,
    module="firewall",
    status="out_of_sync",
    adds=1,
    duration_ms=1000,
):
    return DriftSample(
        host_id=host_id,
        module_type=module,
        status=status,
        add_count=adds,
        remove_count=0,
        policy_change_count=0,
        duration_ms=duration_ms,
        checked_at=datetime.now(UTC) - timedelta(days=days_ago),
    )


async def _totals(db) -> tuple[int, int]:
    """(all-time checks, all-time add changes) as the exporter sees them."""
    checks = sum(v for _m, _s, v in await agg.get_drift_counts(db))
    adds = sum(a for _m, a, _r, _p in await agg.get_drift_change_sums(db))
    return checks, adds


class TestTheCountersSurviveAPrune:
    async def test_totals_are_unchanged_by_retention(self, db, monkeypatch, host_id):
        """The property the whole design exists for."""
        for age in (200, 150, 120, 5, 1):
            db.add(_sample(host_id, age))
        await db.commit()

        before = await _totals(db)
        assert before == (5, 5)

        await _prune(db, monkeypatch, retention_days=90)

        assert await _totals(db) == before, (
            "all-time counters must not move when retention deletes rows — "
            "a decrease reads to Prometheus as a counter reset"
        )

    async def test_the_rows_really_were_deleted(self, db, monkeypatch, host_id):
        for age in (200, 150, 1):
            db.add(_sample(host_id, age))
        await db.commit()

        await _prune(db, monkeypatch, retention_days=90)

        remaining = (await db.execute(_all(DriftSample))).scalars().all()
        assert len(remaining) == 1, "retention that keeps everything is not retention"

    async def test_a_second_prune_does_not_double_count(self, db, monkeypatch, host_id):
        for age in (200, 150, 1):
            db.add(_sample(host_id, age))
        await db.commit()

        await _prune(db, monkeypatch, retention_days=90)
        after_first = await _totals(db)
        await _prune(db, monkeypatch, retention_days=90)

        assert await _totals(db) == after_first


class TestTheHistogramToo:
    async def test_count_and_sum_survive(self, db, monkeypatch, host_id):
        """`_count` and `_sum` are counters as much as the two totals are.

        The original TODO listed only the two counters; rolling those up and
        leaving the histogram to reset would have fixed the visible half.
        """
        for age in (200, 150, 1):
            db.add(_sample(host_id, age, duration_ms=2000))
        await db.commit()

        before = await agg.get_drift_duration_histogram(db)
        assert dict(before)["firewall"].total_count == 3

        await _prune(db, monkeypatch, retention_days=90)

        after = dict(await agg.get_drift_duration_histogram(db))["firewall"]
        assert after.total_count == 3
        assert after.total_sum == 6.0

    async def test_samples_without_a_duration_are_excluded_from_the_histogram(
        self, db, monkeypatch, host_id
    ):
        """`duration_ms` is nullable and the live query filters those out.

        Counting them in the rollup would make `_count` disagree with the
        live query it is added to.
        """
        db.add(_sample(host_id, 200, duration_ms=None))
        db.add(_sample(host_id, 200, duration_ms=1000))
        await db.commit()

        await _prune(db, monkeypatch, retention_days=90)

        rollup = (await db.execute(_all(DriftSampleRollup))).scalars().all()
        assert len(rollup) == 1
        assert rollup[0].checks == 2, "both samples are checks"
        assert rollup[0].duration_count == 1, "only one had a duration"

    async def test_changed_bucket_bounds_keep_count_but_drop_buckets(
        self, db, monkeypatch, host_id
    ):
        """Mismatched bounds must not be added element-wise.

        Two arrays whose positions mean different durations sum to a
        histogram that is wrong in a way nothing would surface.
        """
        db.add(_sample(host_id, 200, duration_ms=1000))
        await db.commit()
        await _prune(db, monkeypatch, retention_days=90)

        row = (await db.execute(_all(DriftSampleRollup))).scalars().one()
        row.duration_bounds = [0.1, 0.2]
        row.duration_buckets = [7, 9]
        await db.commit()

        hist = dict(await agg.get_drift_duration_histogram(db))["firewall"]
        assert hist.total_count == 1, "count is bucket-independent and still true"
        assert hist.bucket_counts == [0] * len(_BUCKETS_DRIFT), "stale buckets are not added"


class TestTheGuards:
    async def test_zero_means_keep_forever(self, db, monkeypatch, host_id):
        """The value meaning 'never delete' must not perform the largest
        possible delete — the cutoff would otherwise be *now*."""
        db.add(_sample(host_id, 900))
        await db.commit()

        result = await _prune(db, monkeypatch, retention_days=0)

        assert result["deleted"] == 0
        assert result["skipped"] == "retention disabled"
        assert len((await db.execute(_all(DriftSample))).scalars().all()) == 1

    async def test_nothing_old_enough_is_a_no_op(self, db, monkeypatch, host_id):
        db.add(_sample(host_id, 1))
        await db.commit()

        result = await _prune(db, monkeypatch, retention_days=90)

        assert result["deleted"] == 0
        assert (await db.execute(_all(DriftSampleRollup))).scalars().all() == []


class TestTheFoldAndTheDeleteShareATransaction:
    async def test_fold_does_not_commit_on_its_own(self, db, host_id):
        """``_fold_into_rollup`` must leave its work uncommitted."""
        db.add(_sample(host_id, 200))
        await db.commit()
        ids = [r.id for r in (await db.execute(_all(DriftSample))).scalars().all()]

        await drift_retention._fold_into_rollup(db, ids)

        assert db.in_transaction(), "the fold must still be inside the caller's transaction"

    async def test_one_commit_per_batch_and_it_follows_the_delete(self, db, monkeypatch, host_id):
        """The invariant the module exists to hold, asserted at the caller.

        Fold and delete have to land together. Split them and a crash in
        between either double-counts (rollup committed, rows still there) or
        loses the history outright (rows gone, rollup never written).

        Asserting that ``_fold_into_rollup`` itself does not commit is not
        enough — it passes just as happily when the *caller* commits between
        the two. So this counts commits per batch and checks the ordering.
        Verified against the mutation: moving ``await db.commit()`` to sit
        between the fold and the delete makes this fail with 2 != 1, and
        every other test in this file still passes.
        """
        for age in (200, 150):
            db.add(_sample(host_id, age))
        await db.commit()

        events: list[str] = []
        real_execute, real_commit = db.execute, db.commit

        async def _spy_execute(stmt, *a, **k):
            events.append("delete" if "DELETE" in str(stmt).upper() else "read")
            return await real_execute(stmt, *a, **k)

        async def _spy_commit(*a, **k):
            events.append("commit")
            return await real_commit(*a, **k)

        monkeypatch.setattr(db, "execute", _spy_execute)
        monkeypatch.setattr(db, "commit", _spy_commit)
        await _prune(db, monkeypatch, retention_days=90)

        assert events.count("commit") == 1, (
            f"exactly one commit per batch — the fold and the delete share it. Got {events}"
        )
        assert events.index("delete") < events.index("commit"), (
            "the delete must be inside the transaction the fold is in"
        )

    async def test_it_groups_by_module_and_status(self, db, monkeypatch, host_id):
        """The rollup grain has to be the finest any consumer needs.

        ``labdog_drift_checks_total`` is per module_type+status; the change
        sums and the histogram are per module_type and recover their totals
        by summing across statuses.
        """
        db.add(_sample(host_id, 200, module="firewall", status="in_sync"))
        db.add(_sample(host_id, 200, module="firewall", status="out_of_sync"))
        db.add(_sample(host_id, 200, module="service", status="in_sync"))
        await db.commit()

        await _prune(db, monkeypatch, retention_days=90)

        rows = (await db.execute(_all(DriftSampleRollup))).scalars().all()
        assert {(r.module_type, r.status) for r in rows} == {
            ("firewall", "in_sync"),
            ("firewall", "out_of_sync"),
            ("service", "in_sync"),
        }


class TestItIsScheduled:
    def test_the_beat_registrar_exists_and_is_discoverable(self):
        """``register_all`` walks ``conf.include`` looking for this name.

        A retention job nothing schedules is the state ``drift_samples`` was
        already in.
        """
        from app.tasks import celery_app

        assert "app.tasks.drift_retention" in celery_app.conf.include
        assert callable(drift_retention._register_beat_schedules)


# --- helpers ---------------------------------------------------------------


def _all(model):
    from sqlalchemy import select

    return select(model)


async def _prune(db, monkeypatch, *, retention_days: int) -> dict:
    """Run the prune body against the test session.

    ``task_session()`` is patched to yield the test's own session so the
    savepoint-per-test rollback still applies; ``_get_retention_days`` is
    patched because the setting is read through the settings service.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _session():
        yield db

    async def _days(_db):
        return retention_days

    monkeypatch.setattr("app.db.task_session", _session)
    monkeypatch.setattr(drift_retention, "_get_retention_days", _days)
    return await drift_retention._prune_drift_samples()
