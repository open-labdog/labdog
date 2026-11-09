"""Service drift check: per-host body plus the periodic sweep delegator.

The sweep itself — host locking, per-host transaction boundary — lives
in `app.tasks.drift_sweep.sweep_module` (BUG-67). This module owns the
collect-and-diff and nothing else, and never commits: the driver
decides whether the verdict becomes durable.
"""

import logging

from app.tasks import celery_app

logger = logging.getLogger(__name__)


async def check_service_drift_for_one_host(host, hms, db) -> bool:
    """Collect and diff this host's services, writing the verdict to *hms*.

    See `app.tasks.drift_sweep.CheckOne` for the contract. Returns
    ``False`` when the host has no usable SSH key, in which case
    nothing is written.
    """
    import time

    import asyncssh

    from app.metrics.recorder import record_drift_sample
    from app.services.collector import collect_service_states
    from app.services.diff import compute_service_diff
    from app.services.merge import get_effective_services
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
        desired = await get_effective_services(host.id, db)
        service_names = [s.service_name for s in desired]

        _t0 = time.monotonic()
        current = await collect_service_states(host, db, private_key_pem, service_names)
        diff = compute_service_diff(current, desired)
        _duration_ms = int((time.monotonic() - _t0) * 1000)

        write_drift_verdict(
            hms,
            drifted=diff.has_changes,
            collected_state=[
                {
                    "service_name": s.service_name,
                    "active_state": s.active_state,
                    "enabled": s.enabled,
                }
                for s in current
            ],
        )
        await record_drift_sample(
            db,
            host_id=host.id,
            module_type="service",
            status=hms.sync_status,
            policy_change_count=len(diff.services_to_update),
            duration_ms=_duration_ms,
        )

        await probe_source_ip_once(host, db, private_key_pem, logger)
        return True
    except (OSError, asyncssh.Error, TimeoutError) as e:
        record_unreachable(hms, e)
        return True
    except Exception as e:
        logger.exception("service drift check failed for host %s", host.id)
        record_check_error(hms, e)
        return True


@celery_app.task(name="app.tasks.service_drift.check_all_service_drift", queue="drift")
def check_all_service_drift():
    """Periodic task: service drift for every host with service drift enabled."""
    from app.tasks.drift_sweep import run_sweep

    # Fans out one check per host on the drift queue; see app.tasks.drift_sweep.
    return run_sweep("service")


# Register periodic service drift check via RedBeat
def _register_service_drift_schedule():
    from app.config import settings
    from app.tasks.beat_registry import ensure_entry

    ensure_entry(
        name="check-service-drift-periodic",
        task="app.tasks.service_drift.check_all_service_drift",
        run_every_seconds=settings.drift.check_interval_minutes * 60,
        app=celery_app,
    )


# Registration happens from ``beat_init`` (app.tasks.beat_registry), not at
# import. Calling it here rewrote the entry's ``due_at`` in every process
# that imported this module — API included — so on a deployment that
# restarts more than once a day, a daily job never fired at all (BUG-70).
