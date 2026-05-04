"""Linux-user per-tab sync task — thin delegator to the coalesced orchestrator.

See ``app/tasks/sync.py`` for the rationale; this module is the
``linux-users`` slice of the same conversion.
"""

from app.tasks import celery_app


@celery_app.task(bind=True, name="app.tasks.user_sync.user_sync_task", queue="long_running")
def user_sync_task(self, job_id: int, host_id: int) -> dict:
    """Delegate linux-user sync to the coalesced per-host orchestrator.

    Equivalent to a per-tab orchestration with ``module_filter=["linux-users"]``.
    """
    from app.tasks.host_sync_orchestrator import run_host_sync

    return run_host_sync.run(job_id, host_id, ["linux-users"])
