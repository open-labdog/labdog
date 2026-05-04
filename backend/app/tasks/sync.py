"""Firewall per-tab sync task — thin delegator to the coalesced orchestrator.

Pre-v0.2.0 this module owned the full firewall sync lifecycle (pre-run
DB writes, ansible-runner invocation, post-run state collection,
HostModuleStatus update). v0.2.0 moves all of that into
``app.tasks.host_sync_orchestrator.run_host_sync``; this task is kept
purely as a stable Celery task name so existing dispatchers — the
``POST /api/sync/hosts/{id}/sync`` endpoint, the per-group sync, and any
external callers that pin to this task name — continue to work without
change.

The per-tab task delegates in-process to the orchestrator's bound
``.run`` callable rather than dispatching a fresh Celery task. This
avoids an extra hop through the broker and keeps lifecycle ownership
inside a single worker process. The orchestrator's queue mechanism
(commit C-2) still uses ``run_host_sync.delay(...)`` for fresh
dispatches — only the per-tab entry points run inline.
"""

from app.tasks import celery_app


@celery_app.task(bind=True, name="app.tasks.sync.run_sync_playbook", queue="long_running")
def run_sync_playbook(self, job_id: int, host_id: int) -> dict:
    """Delegate firewall sync to the coalesced per-host orchestrator.

    Equivalent to a per-tab orchestration with ``module_filter=["firewall"]``.
    """
    from app.tasks.host_sync_orchestrator import run_host_sync

    return run_host_sync.run(job_id, host_id, ["firewall"])
