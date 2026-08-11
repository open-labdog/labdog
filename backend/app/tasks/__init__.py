import logging

from celery import Celery
from celery.signals import worker_process_init, worker_ready

from app.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "labdog",
    broker=settings.redis.url,
    backend=settings.redis.url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_routes={
        "app.tasks.sync.*": {"queue": "long_running"},
        "app.tasks.host_sync_orchestrator.*": {"queue": "long_running"},
        "app.tasks.drift.*": {"queue": "long_running"},
        "app.tasks.service_sync.*": {"queue": "long_running"},
        "app.tasks.service_drift.*": {"queue": "long_running"},
        "app.tasks.hosts_sync.*": {"queue": "long_running"},
        "app.tasks.hosts_drift.*": {"queue": "long_running"},
        "app.tasks.user_drift.*": {"queue": "long_running"},
        "app.tasks.user_sync.*": {"queue": "long_running"},
        "app.tasks.cron_drift.*": {"queue": "long_running"},
        "app.tasks.cron_sync.*": {"queue": "long_running"},
        "app.tasks.package_sync.*": {"queue": "long_running"},
        "app.tasks.package_drift.*": {"queue": "long_running"},
        "app.tasks.ca_cert_action.*": {"queue": "long_running"},
        "app.tasks.resolver_sync.*": {"queue": "long_running"},
        "app.tasks.resolver_drift.*": {"queue": "long_running"},
        "app.tasks.action_orchestrator.*": {"queue": "long_running"},
        "app.tasks.action_host.*": {"queue": "long_running"},
        "app.tasks.builtin_dispatchers.*": {"queue": "long_running"},
        "app.tasks.scheduled_action_schedule.*": {"queue": "long_running"},
        "app.tasks.facts.*": {"queue": "long_running"},
        "app.tasks.ai_task.*": {"queue": "long_running"},
        "discovery.*": {"queue": "long_running"},
        "gitops.*": {"queue": "long_running"},
        "scans.check_scheduled": {"queue": "default"},
        "scans.run_config": {"queue": "long_running"},
    },
    worker_max_tasks_per_child=100,
    task_time_limit=1800,
    task_soft_time_limit=1500,
)


@worker_process_init.connect
def _register_all_models(**_kwargs):
    """Put every model on ``Base.metadata`` before any task runs.

    Without this, which tables a worker knows about depends on which task
    modules Celery happened to import — and therefore on which task runs
    first. That produced a bug with a genuinely confusing shape: the first
    AI session after a restart failed with

        NoReferencedTableError: Foreign key associated with column
        'ai_sessions.action_run_id' could not find table 'action_runs'

    while later ones succeeded, because by then some action task had run in
    the same process and imported ``action_runs`` as a side effect. A
    restart brought it back. Registration is not something to leave to
    import order.

    Runs per pool child rather than once in the parent: prefork children
    inherit the parent's imports, but the solo and threads pools have no
    parent to inherit from, and a re-import in an already-populated
    process is a no-op.
    """
    try:
        from app.models import import_all_models

        import_all_models()
    except Exception:
        # A worker that starts with incomplete metadata is still more
        # useful than one that refuses to start; the failure is loud in
        # the log and the affected flush will say which table is missing.
        logger.exception("model registration on worker start failed")


@worker_ready.connect
def _sync_packs_on_worker_start(sender=None, **_kwargs):
    """On Celery worker boot, sync every enabled action pack and rebuild
    the in-process action registry from DB-backed packs.

    Both sync and registry reload run inside the same asyncio.run() block
    so asyncpg is used throughout. The former reload_registry() call used
    a sync SQLAlchemy engine which required psycopg2 (not installed),
    silently fell back to bundled-only, and caused DB-backed actions to
    be missing from the Celery registry while FastAPI found them fine.
    Failures are logged and swallowed so a failing git remote doesn't
    prevent the worker from starting.
    """
    try:
        import asyncio  # noqa: PLC0415

        from app.actions.registry import reload_registry_async  # noqa: PLC0415
        from app.db import AsyncSessionLocal  # noqa: PLC0415
        from app.packs.service import sync_enabled_packs  # noqa: PLC0415

        async def _do_sync():
            async with AsyncSessionLocal() as session:
                await sync_enabled_packs(session)
                await reload_registry_async(session)

        asyncio.run(_do_sync())
    except Exception:
        logger.exception("action-pack sync on worker_ready failed; bundled pack only")


@worker_ready.connect
def _sweep_orphans_on_worker_start(sender=None, **_kwargs):
    """Startup reconciliation: enqueue one sweep of each stale-row sweeper.

    A worker restart is exactly the moment rows get orphaned in
    ``running`` (the old worker was killed mid-task and its DB
    finalisation never ran). Both sweepers are deadline-based, so
    fresh legitimate rows are untouched; genuinely orphaned ones are
    reaped now instead of waiting for the next 5-minute beat.
    """
    try:
        celery_app.send_task("app.tasks.sync_sweeper.sweep_stale_syncs")
        celery_app.send_task("app.tasks.action_sweeper.sweep_stale_action_runs")
    except Exception:
        logger.exception("startup sweep enqueue failed; periodic sweeps will cover it")


# Auto-discover tasks
celery_app.conf.include = [
    "app.tasks.discovery",
    "app.tasks.gitops",
    "app.tasks.sync",
    "app.tasks.host_sync_orchestrator",
    "app.tasks.drift",
    "app.tasks.service_sync",
    "app.tasks.service_drift",
    "app.tasks.hosts_sync",
    "app.tasks.hosts_drift",
    "app.tasks.user_sync",
    "app.tasks.user_drift",
    "app.tasks.cron_sync",
    "app.tasks.cron_drift",
    "app.tasks.package_sync",
    "app.tasks.package_drift",
    "app.tasks.ca_cert_action",
    "app.tasks.resolver_sync",
    "app.tasks.resolver_drift",
    "app.tasks.action_orchestrator",
    "app.tasks.action_host",
    "app.tasks.builtin_dispatchers",
    "app.tasks.scheduled_action_schedule",
    "app.tasks.scan_schedule",
    "app.tasks.scan_run",
    "app.tasks.facts",
    "app.tasks.sync_sweeper",
    "app.tasks.action_sweeper",
    "app.tasks.audit_retention",
    "app.tasks.ai_task",
    "app.tasks.ai_approvals",
    "app.tasks.ai_snapshots",
]
