"""Retention for ``drift_samples``, without resetting the drift counters.

``drift_samples`` is written once per drift check per module and never
updated. Nothing pruned it, so it grew for the life of the install.

Deleting rows is not as simple as it looks. ``labdog_drift_checks_total``,
``labdog_drift_changes_total`` and ``labdog_drift_check_duration_seconds``
are all derived from ``COUNT(*)``/``SUM()`` over the whole table with no
time window, and they are **counters**. A counter that decreases is read by
Prometheus as a process restart: ``rate()`` copes, ``increase()`` across the
deletion silently under-reports, and nothing warns anybody.

So each row is folded into ``drift_sample_rollup`` before it goes, and the
exporter reports live rows plus rollup. The fold and the delete share one
transaction — that is the load-bearing property of this module, and the one
a future edit is most likely to break. Split them and a crash in between
either double-counts (rollup committed, rows still present) or loses the
history outright (rows gone, rollup not written).

Window: ``logging.drift_retention_days`` (default 90, ``0`` = keep
forever). Separate from the audit and run windows because drift samples are
small and feed a trend chart that offers up to 90 days.
"""

from __future__ import annotations

import logging

from app.tasks import celery_app

logger = logging.getLogger(__name__)

#: Rows folded per transaction. Retention runs daily, so the first run on an
#: install that has been up for a year has a lot to do; doing it in one
#: statement would hold a long transaction over a table the drift sweeps are
#: still writing to.
BATCH_SIZE = 5000


@celery_app.task(
    name="app.tasks.drift_retention.prune_old_drift_samples",
    queue="default",
)
def prune_old_drift_samples() -> dict:
    """Fold ``drift_samples`` older than the window into the rollup, then delete."""
    import asyncio  # noqa: PLC0415

    return asyncio.run(_prune_drift_samples())


async def _get_retention_days(db) -> int:
    from app.settings_service import get_setting_typed  # noqa: PLC0415

    return int(await get_setting_typed("logging.drift_retention_days", db))


async def _prune_drift_samples() -> dict:
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from sqlalchemy import delete, select  # noqa: PLC0415

    from app.db import task_session  # noqa: PLC0415
    from app.models.drift_sample import DriftSample  # noqa: PLC0415

    async with task_session() as db:
        retention_days = await _get_retention_days(db)
        # ``0`` means "keep forever" per the setting's description. Without
        # this guard the cutoff becomes *now* and the whole table goes —
        # the value meaning "never delete" performing the largest possible
        # delete. Same guard, same reason, as audit_retention.
        if retention_days <= 0:
            logger.info(
                "drift_retention: retention disabled (%d) — keeping all drift_samples",
                retention_days,
            )
            return {"deleted": 0, "retention_days": retention_days, "skipped": "retention disabled"}

        cutoff = datetime.now(UTC) - timedelta(days=retention_days)

    total_deleted = 0
    while True:
        async with task_session() as db:
            ids = (
                (
                    await db.execute(
                        select(DriftSample.id)
                        .where(DriftSample.checked_at < cutoff)
                        .order_by(DriftSample.id)
                        .limit(BATCH_SIZE)
                    )
                )
                .scalars()
                .all()
            )
            if not ids:
                break

            # Fold, then delete, then commit — one transaction. See the
            # module docstring; this ordering is the invariant.
            await _fold_into_rollup(db, list(ids))
            await db.execute(delete(DriftSample).where(DriftSample.id.in_(list(ids))))
            await db.commit()
            total_deleted += len(ids)

        if len(ids) < BATCH_SIZE:
            break

    logger.info(
        "drift_retention: folded and deleted %d drift_samples older than %s",
        total_deleted,
        cutoff.isoformat(),
    )
    return {
        "deleted": total_deleted,
        "retention_days": retention_days,
        "cutoff": cutoff.isoformat(),
    }


async def _fold_into_rollup(db, ids: list[int]) -> None:
    """Add the totals of *ids* to ``drift_sample_rollup``, in *db*'s transaction.

    Does not commit. The caller deletes the same rows and commits both
    together, which is what keeps the exporter's live+rollup sum exact
    across a crash.
    """
    from sqlalchemy import func, select  # noqa: PLC0415

    from app.metrics.aggregates import _BUCKETS_DRIFT  # noqa: PLC0415
    from app.models.drift_sample import DriftSample  # noqa: PLC0415
    from app.models.drift_sample_rollup import DriftSampleRollup  # noqa: PLC0415

    duration_seconds = DriftSample.duration_ms / 1000.0
    bucket_cols = [
        func.count()
        .filter((DriftSample.duration_ms.isnot(None)) & (duration_seconds <= bound))
        .label(f"le_{i}")
        for i, bound in enumerate(_BUCKETS_DRIFT)
    ]
    rows = (
        await db.execute(
            select(
                DriftSample.module_type,
                DriftSample.status,
                func.count().label("checks"),
                func.coalesce(func.sum(DriftSample.add_count), 0).label("add_sum"),
                func.coalesce(func.sum(DriftSample.remove_count), 0).label("remove_sum"),
                func.coalesce(func.sum(DriftSample.policy_change_count), 0).label("policy_sum"),
                func.count().filter(DriftSample.duration_ms.isnot(None)).label("dur_count"),
                func.coalesce(func.sum(func.coalesce(duration_seconds, 0.0)), 0.0).label("dur_sum"),
                *bucket_cols,
            )
            .where(DriftSample.id.in_(ids))
            .group_by(DriftSample.module_type, DriftSample.status)
        )
    ).all()

    bounds = list(_BUCKETS_DRIFT)
    for row in rows:
        buckets = [getattr(row, f"le_{i}") for i in range(len(bounds))]
        existing = await db.get(
            DriftSampleRollup, (row.module_type, row.status), with_for_update=True
        )
        if existing is None:
            db.add(
                DriftSampleRollup(
                    module_type=row.module_type,
                    status=row.status,
                    checks=row.checks,
                    add_count=row.add_sum,
                    remove_count=row.remove_sum,
                    policy_change_count=row.policy_sum,
                    duration_count=row.dur_count,
                    duration_sum_seconds=float(row.dur_sum),
                    duration_buckets=buckets,
                    duration_bounds=bounds,
                )
            )
            continue

        existing.checks += row.checks
        existing.add_count += row.add_sum
        existing.remove_count += row.remove_sum
        existing.policy_change_count += row.policy_sum
        existing.duration_count += row.dur_count
        existing.duration_sum_seconds += float(row.dur_sum)
        if list(existing.duration_bounds) == bounds and len(existing.duration_buckets) == len(
            bounds
        ):
            existing.duration_buckets = [
                a + b for a, b in zip(existing.duration_buckets, buckets, strict=True)
            ]
        else:
            # ``_BUCKETS_DRIFT`` changed since this row was written. Adding
            # the arrays element-wise would silently produce a histogram
            # whose buckets mean two different things. Keep the counts and
            # the sum — those are bucket-independent and still true — and
            # restart the bucket series against the current bounds.
            logger.warning(
                "drift_retention: histogram bounds changed for %s/%s "
                "(stored %s, current %s) — restarting bucket counts",
                row.module_type,
                row.status,
                existing.duration_bounds,
                bounds,
            )
            existing.duration_buckets = buckets
            existing.duration_bounds = bounds


# ---------------------------------------------------------------------------
# RedBeat registration
# ---------------------------------------------------------------------------


def _register_beat_schedules() -> None:
    from app.tasks.beat_registry import ensure_entry  # noqa: PLC0415

    _SECONDS_PER_DAY = 86400

    ensure_entry(
        name="prune-old-drift-samples",
        task="app.tasks.drift_retention.prune_old_drift_samples",
        run_every_seconds=_SECONDS_PER_DAY,
        app=celery_app,
    )


# Registration happens from ``beat_init`` (app.tasks.beat_registry), not at
# import — see the note at the foot of ``audit_retention`` for what calling
# it here would break (BUG-70).
