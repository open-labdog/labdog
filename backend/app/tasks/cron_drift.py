"""Cron drift check: per-host body plus the periodic sweep delegator.

The sweep itself — host locking, per-host transaction boundary — lives
in `app.tasks.drift_sweep.sweep_module` (BUG-67). This module owns the
collect-and-diff and nothing else, and never commits: the driver
decides whether the verdict becomes durable.
"""

import logging

from app.tasks import celery_app

logger = logging.getLogger(__name__)


async def check_cron_drift_for_one_host(host, hms, db) -> bool:
    """Collect and diff this host's cron jobs, writing the verdict to *hms*.

    See `app.tasks.drift_sweep.CheckOne` for the contract. Returns
    ``False`` when the host has no usable SSH key, in which case
    nothing is written.
    """
    import time

    import asyncssh

    from app.cron.collector import collect_cron_jobs
    from app.cron.diff import diff_cron_jobs
    from app.cron.merge import get_effective_cron_jobs
    from app.metrics.recorder import record_drift_sample
    from app.tasks.drift_sweep import (
        load_private_key_pem,
        probe_source_ip_once,
        record_check_error,
        record_unreachable,
        write_drift_verdict,
    )

    try:
        private_key_pem = await load_private_key_pem(host, db)
        if private_key_pem is None:
            return False

        effective = await get_effective_cron_jobs(host.id, db)
        desired_dicts = [
            {
                "name": j.name,
                "user": j.user,
                "schedule": j.schedule,
                "command": j.command,
            }
            for j in effective
        ]

        users = list({j.user for j in effective})

        _t0 = time.monotonic()
        actual = await collect_cron_jobs(host, db, private_key_pem, users)

        cron_diff = diff_cron_jobs(desired_dicts, actual)
        _duration_ms = int((time.monotonic() - _t0) * 1000)

        drifted = bool(
            cron_diff.jobs_to_add or cron_diff.jobs_to_remove or cron_diff.jobs_to_update
        )

        write_drift_verdict(hms, drifted=drifted, collected_state=actual)
        await record_drift_sample(
            db,
            host_id=host.id,
            module_type="cron",
            status=hms.sync_status,
            add_count=len(cron_diff.jobs_to_add),
            remove_count=len(cron_diff.jobs_to_remove),
            policy_change_count=len(cron_diff.jobs_to_update),
            duration_ms=_duration_ms,
        )

        from app.api.host_state import refresh_host_sync_status

        await refresh_host_sync_status(host, db)

        await probe_source_ip_once(host, db, private_key_pem, logger)
        return True
    except (OSError, asyncssh.Error, TimeoutError) as e:
        record_unreachable(hms, e)
        return True
    except Exception as e:
        logger.exception("cron drift check failed for host %s", host.id)
        record_check_error(hms, e)
        return True


@celery_app.task(name="app.tasks.cron_drift.check_all_cron_drift", queue="drift")
def check_all_cron_drift():
    """Periodic task: cron drift for every host with cron drift enabled."""
    from app.tasks.drift_sweep import run_sweep

    # Fans out one check per host on the drift queue; see app.tasks.drift_sweep.
    return run_sweep("cron")


cron_drift_task = check_all_cron_drift


def _register_cron_drift_schedule():
    from app.config import settings
    from app.tasks.beat_registry import ensure_entry

    ensure_entry(
        name="check-cron-drift-periodic",
        task="app.tasks.cron_drift.check_all_cron_drift",
        run_every_seconds=settings.drift.check_interval_minutes * 60,
        app=celery_app,
    )


# Registration happens from ``beat_init`` (app.tasks.beat_registry), not at
# import. Calling it here rewrote the entry's ``due_at`` in every process
# that imported this module — API included — so on a deployment that
# restarts more than once a day, a daily job never fired at all (BUG-70).
