from app.tasks import celery_app


@celery_app.task(name="app.tasks.drift.check_all_drift", queue="long_running")
def check_all_drift():
    """Periodic task: check drift for all hosts with drift_check_enabled=True."""
    import asyncio
    from sqlalchemy import select
    from app.db import AsyncSessionLocal
    from app.models.host import Host
    from app.drift.detector import check_drift
    from datetime import datetime, timezone

    async def _run():
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Host).where(Host.drift_check_enabled == True))
            hosts = result.scalars().all()
            for host in hosts:
                try:
                    from app.api.drift import _get_desired_rules_for_host

                    desired = await _get_desired_rules_for_host(host.id, db)
                    drift_result = await check_drift(host.id, desired, db)
                    host.sync_status = drift_result.status
                    host.last_drift_check_at = datetime.now(timezone.utc)
                except Exception:
                    from app.models.host import SyncStatus
                    host.sync_status = SyncStatus.error
                    host.last_drift_check_at = datetime.now(timezone.utc)
            await db.commit()
            return len(hosts)

    count = asyncio.run(_run())
    return {"checked": count}


# Register periodic drift check via RedBeat (prevents duplicate schedules on restart)
def _register_beat_schedule():
    from redbeat import RedBeatSchedulerEntry
    from celery.schedules import schedule
    from app.config import settings

    interval = schedule(run_every=settings.DRIFT_CHECK_INTERVAL_MINUTES * 60)
    entry = RedBeatSchedulerEntry(
        name="check-drift-periodic",
        task="app.tasks.drift.check_all_drift",
        schedule=interval,
        app=celery_app,
    )
    entry.save()


try:
    _register_beat_schedule()
except Exception:
    # Fallback: Redis may not be available at import time (e.g., during tests)
    pass
