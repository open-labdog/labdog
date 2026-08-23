"""Every registered task must route to a queue the worker consumes.

Celery will happily publish a task to a queue nobody reads. The broker
accepts it, the caller's ``send_task`` returns an id, and the work simply
never happens — no exception, no dead-letter, no log line. There is no
runtime signal to alert on, so the only place to catch it is here.

The gap this closes: ``task_routes`` is a routing *override*, not a
manifest, and the deployment runs ``-Q default,long_running`` while
Celery's built-in default queue is named ``celery``. Any task no route
pattern matched therefore went somewhere nothing consumed. Eight of forty
tasks were in that state, six of them on RedBeat timers firing into the
void — the stale-run sweepers, all three retention pruners, approval
expiry, and alert investigations.

These tests import both halves of the contract rather than restating
either, so they keep holding when a queue is renamed or a task is added.
"""

import fnmatch

import pytest

from app.celery_manager import CeleryManager
from app.tasks import celery_app


@pytest.fixture(autouse=True, scope="module")
def _load_task_modules():
    """Import everything in ``conf.include``, as a worker does at startup.

    Importing ``app.tasks`` alone registers almost nothing: the task
    modules are listed in ``conf.include`` and pulled in when a worker
    boots, so a bare import sees only whatever happened to be imported as
    a side effect. Checking routing against that partial set would pass
    while saying nothing — the stranded tasks are exactly the ones a
    bare import misses.
    """
    celery_app.loader.import_default_modules()


def _queue_for(task_name: str) -> str:
    """Resolve a task to its queue exactly as Celery's router would."""
    for pattern, route in celery_app.conf.task_routes.items():
        if fnmatch.fnmatch(task_name, pattern):
            return route["queue"]
    return celery_app.conf.task_default_queue


def _own_tasks() -> list[str]:
    """Registered tasks, minus Celery's own built-ins (``celery.*``)."""
    return sorted(n for n in celery_app.tasks if not n.startswith("celery."))


def test_every_task_routes_to_a_consumed_queue():
    """The whole contract, in one assertion.

    Deliberately not parametrised: when several tasks are stranded they
    are almost always stranded for the same reason, and one failure
    listing all of them reads better than eight identical ones.
    """
    consumed = set(CeleryManager.QUEUES)
    stranded = {name: queue for name in _own_tasks() if (queue := _queue_for(name)) not in consumed}

    assert not stranded, (
        "These tasks publish to a queue no worker consumes, so they are "
        f"accepted and never run: {stranded}. Worker consumes {sorted(consumed)}. "
        "Either add a task_routes entry or fix task_default_queue."
    )


def test_default_queue_is_consumed():
    """The default is what catches everything nobody thought about.

    Guarded separately from the sweep above because it is the failure that
    reintroduces the whole class of bug at once: unset it, and every task
    without an explicit route silently stops running.
    """
    assert celery_app.conf.task_default_queue in CeleryManager.QUEUES


@pytest.mark.parametrize(
    "task_name",
    [
        "app.tasks.ai_alerts.investigate_alert",
        "app.tasks.ai_approvals.expire_stale_approvals",
        "app.tasks.ai_snapshots.prune_ai_snapshots",
        "app.tasks.audit_retention.prune_old_audit_logs",
        "app.tasks.audit_retention.prune_old_ssh_transcripts",
        "app.tasks.action_sweeper.sweep_stale_action_runs",
        "app.tasks.sync_sweeper.sweep_stale_syncs",
    ],
)
def test_previously_stranded_tasks_are_registered_and_routed(task_name):
    """Name the casualties, so a regression says which one broke.

    The sweep above would catch these anyway. Listing them by name buys
    something the sweep cannot: if one is later renamed or dropped from
    ``conf.include``, this fails on the missing registration rather than
    quietly having nothing to check.
    """
    assert task_name in celery_app.tasks, f"{task_name} is not registered on the worker"
    assert _queue_for(task_name) in CeleryManager.QUEUES
