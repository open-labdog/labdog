"""Package drift check: per-host body plus the periodic sweep delegator.

The sweep itself — host locking, per-host transaction boundary — lives
in `app.tasks.drift_sweep.sweep_module` (BUG-67). This module owns the
collect-and-diff and nothing else, and never commits: the driver
decides whether the verdict becomes durable.
"""

import logging

from app.db import with_task_engine
from app.tasks import celery_app

logger = logging.getLogger(__name__)


async def check_package_drift_for_one_host(host, hms, db) -> bool:
    """Collect and diff this host's packages, writing the verdict to *hms*.

    See `app.tasks.drift_sweep.CheckOne` for the contract. Returns
    ``False`` when the host has no usable SSH key, in which case
    nothing is written.
    """
    import time

    import asyncssh

    from app.metrics.recorder import record_drift_sample
    from app.packages.collector import collect_package_states
    from app.packages.diff import compute_diff
    from app.packages.merge import get_effective_packages
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

        effective = await get_effective_packages(host.id, db)
        desired_dicts = [p.model_dump() for p in effective]
        package_names = [p.package_name for p in effective]

        _t0 = time.monotonic()
        actual = await collect_package_states(host, db, private_key_pem, package_names)

        pkg_diff = compute_diff(desired_dicts, actual)
        _duration_ms = int((time.monotonic() - _t0) * 1000)

        write_drift_verdict(hms, drifted=pkg_diff.has_drift, collected_state=actual)
        await record_drift_sample(
            db,
            host_id=host.id,
            module_type="package",
            status=hms.sync_status,
            add_count=len(pkg_diff.to_install),
            remove_count=len(pkg_diff.to_remove),
            policy_change_count=len(pkg_diff.to_upgrade) + len(pkg_diff.to_hold_change),
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
        logger.exception("package drift check failed for host %s", host.id)
        record_check_error(hms, e)
        return True


@celery_app.task(name="app.tasks.package_drift.check_all_package_drift", queue="long_running")
def check_all_package_drift():
    """Periodic task: package drift for every host with package drift enabled."""
    import asyncio

    from app.tasks.drift_sweep import sweep_module

    return asyncio.run(with_task_engine(sweep_module("package", check_package_drift_for_one_host)))


package_drift_task = check_all_package_drift

DRIFT_SCHEDULE_KEY = "labdog:package:drift"


def _register_package_drift_schedule():
    from app.config import settings
    from app.tasks.beat_registry import ensure_entry

    ensure_entry(
        name="check-package-drift-periodic",
        task="app.tasks.package_drift.check_all_package_drift",
        run_every_seconds=settings.drift.check_interval_minutes * 60,
        app=celery_app,
    )


# Registration happens from ``beat_init`` (app.tasks.beat_registry), not at
# import. Calling it here rewrote the entry's ``due_at`` in every process
# that imported this module — API included — so on a deployment that
# restarts more than once a day, a daily job never fired at all (BUG-70).
