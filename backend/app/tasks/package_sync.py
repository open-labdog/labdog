"""Package per-tab sync task — thin delegator to the coalesced orchestrator.

See ``app/tasks/sync.py`` for the rationale; this module is the
``packages`` slice of the same conversion.
"""

from app.tasks import celery_app


@celery_app.task(bind=True, name="app.tasks.package_sync.run_package_sync", queue="long_running")
def run_package_sync(self, job_id: int, host_id: int) -> dict:
    """Delegate package sync to the coalesced per-host orchestrator.

    Equivalent to a per-tab orchestration with ``module_filter=["packages"]``.
    """
    from app.tasks.host_sync_orchestrator import run_host_sync

    return run_host_sync.run(job_id, host_id, ["packages"])
