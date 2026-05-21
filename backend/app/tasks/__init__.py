import logging

from celery import Celery
from celery.signals import worker_ready

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
        "discovery.*": {"queue": "long_running"},
        "gitops.*": {"queue": "long_running"},
        "scans.check_scheduled": {"queue": "default"},
        "scans.run_config": {"queue": "long_running"},
    },
    worker_max_tasks_per_child=100,
    task_time_limit=1800,
    task_soft_time_limit=1500,
)


@worker_ready.connect
def _sync_packs_on_worker_start(sender=None, **_kwargs):
    """On Celery worker boot, sync every enabled action pack and rebuild
    the in-process action registry from disk.

    Runs synchronously — reload_registry() uses a blocking engine so it
    doesn't conflict with the worker's async task runtime. Failures are
    logged and swallowed so a failing git remote doesn't prevent the
    worker from starting.
    """
    try:
        import asyncio  # noqa: PLC0415

        from app.actions.registry import reload_registry  # noqa: PLC0415
        from app.db import AsyncSessionLocal  # noqa: PLC0415
        from app.packs.service import sync_enabled_packs  # noqa: PLC0415

        async def _do_sync():
            async with AsyncSessionLocal() as session:
                await sync_enabled_packs(session)

        asyncio.run(_do_sync())
        reload_registry()
    except Exception:
        logger.exception("action-pack sync on worker_ready failed; bundled pack only")


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
    "app.tasks.audit_retention",
]
