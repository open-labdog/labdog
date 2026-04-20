from celery import Celery

from app.config import settings

celery_app = Celery(
    "barricade",
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
        "app.tasks.workflow_orchestrator.*": {"queue": "long_running"},
        "app.tasks.workflow_host.*": {"queue": "long_running"},
        "app.tasks.workflow_schedule.*": {"queue": "long_running"},
        "discovery.*": {"queue": "long_running"},
        "gitops.*": {"queue": "long_running"},
        "scans.check_scheduled": {"queue": "default"},
        "scans.run_config": {"queue": "long_running"},
    },
    worker_max_tasks_per_child=100,
    task_time_limit=1800,
    task_soft_time_limit=1500,
)

# Auto-discover tasks
celery_app.conf.include = [
    "app.tasks.discovery",
    "app.tasks.gitops",
    "app.tasks.sync",
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
    "app.tasks.workflow_orchestrator",
    "app.tasks.workflow_host",
    "app.tasks.workflow_schedule",
    "app.tasks.scan_schedule",
    "app.tasks.scan_run",
]
