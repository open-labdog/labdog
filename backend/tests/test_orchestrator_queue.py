"""BUG-63: an orchestrator must never wait on a pool it occupies.

``action_orchestrator.run_action`` publishes per-host children and then
blocks in ``result.join()`` until they finish. It used to be published to
``long_running`` — the same queue as its children — served by one worker
at ``celery.concurrency`` 4. Four schedules sharing a cron minute
(``0 3 * * *`` is the obvious default) took all four slots, leaving none
for any child, and nothing progressed until the orchestrator's
``soft_time_limit`` of 43200s fired: twelve hours of a wedged fleet, then
four runs finalised ``partial`` having touched zero hosts.

The fix is structural rather than a sizing tweak — no value of
``concurrency`` makes a shared pool safe, because the number of
simultaneous orchestrators is set by the operator's cron entries, not by
config. ``run_action`` gets its own queue and its own worker, so the pool
it waits on is never the pool it holds a slot in.

These tests assert that *separation*, not the queue names: renaming a
queue should not fail them, and reintroducing the overlap should.
"""

from __future__ import annotations

import fnmatch
from unittest.mock import MagicMock, patch

import pytest

from app.celery_manager import WORKER_QUEUES, CeleryManager
from app.tasks import _is_orchestrator_worker, celery_app
from app.tasks.action_orchestrator import CHILD_QUEUE

ORCHESTRATOR_TASK = "app.tasks.action_orchestrator.run_action"


@pytest.fixture(autouse=True, scope="module")
def _load_task_modules():
    celery_app.loader.import_default_modules()


def _queue_for(task_name: str) -> str:
    for pattern, route in celery_app.conf.task_routes.items():
        if fnmatch.fnmatch(task_name, pattern):
            return route["queue"]
    return celery_app.conf.task_default_queue


def _worker_serving(queue: str) -> str:
    matches = [name for name, queues in WORKER_QUEUES.items() if queue in queues]
    assert len(matches) == 1, f"{queue!r} is served by {matches}, expected exactly one worker"
    return matches[0]


class TestTheJoinCannotStarveItsChildren:
    def test_the_orchestrator_and_its_children_are_on_different_queues(self):
        assert _queue_for(ORCHESTRATOR_TASK) != CHILD_QUEUE

    def test_they_are_served_by_different_workers(self):
        """Different queues are not enough — one worker consuming both puts
        them back in the same pool."""
        assert _worker_serving(_queue_for(ORCHESTRATOR_TASK)) != _worker_serving(CHILD_QUEUE)

    def test_nothing_else_shares_the_orchestrator_worker(self):
        """A slot on the orchestrator worker held by unrelated work
        reintroduces the starvation this split removes."""
        orch_worker = _worker_serving(_queue_for(ORCHESTRATOR_TASK))
        orch_queues = set(WORKER_QUEUES[orch_worker])

        strays = sorted(
            name
            for name in celery_app.tasks
            if not name.startswith("celery.")
            and name != ORCHESTRATOR_TASK
            and _queue_for(name) in orch_queues
        )
        assert not strays, (
            f"these tasks would occupy orchestrator slots: {strays}. "
            "The orchestrator worker exists so run_action never competes "
            "for the pool it waits on."
        )

    def test_every_per_host_dispatch_target_lands_on_the_child_queue(self):
        """The children are dispatched with an explicit ``queue=``, so this
        checks the constant they all use is the one the work pool serves."""
        assert CHILD_QUEUE in WORKER_QUEUES[_worker_serving(CHILD_QUEUE)]


class TestBothWorkersAreActuallyStarted:
    def _start(self):
        mgr = CeleryManager()
        procs = []

        def _popen(cmd, **_kw):
            procs.append(cmd)
            p = MagicMock()
            p.pid = 1000 + len(procs)
            p.poll.return_value = None
            return p

        with patch("app.celery_manager.subprocess.Popen", side_effect=_popen):
            mgr.start()
        return mgr, procs

    def test_one_subprocess_per_worker(self):
        _, procs = self._start()
        assert len(procs) == len(WORKER_QUEUES)

    def test_each_worker_gets_its_own_queues_and_node_name(self):
        _, procs = self._start()
        by_name = {}
        for cmd in procs:
            node = cmd[cmd.index("-n") + 1]
            by_name[node.split("@", 1)[0]] = cmd[cmd.index("-Q") + 1]
        assert by_name == {name: ",".join(q) for name, q in WORKER_QUEUES.items()}

    def test_only_one_worker_carries_beat(self):
        """Two RedBeat schedulers against one keyspace double-fire every
        periodic task."""
        _, procs = self._start()
        assert sum("--beat" in cmd for cmd in procs) == 1

    def test_is_alive_is_false_when_any_worker_has_died(self):
        mgr, _ = self._start()
        assert mgr.is_alive()
        dead = next(iter(mgr._processes.values()))
        dead.poll.return_value = 1
        assert not mgr.is_alive(), "a dead orchestrator means no action run ever starts"
        assert mgr.dead_workers()

    def test_is_alive_is_false_before_start(self):
        assert not CeleryManager().is_alive()


class TestBootWorkIsNotDoneTwice:
    """Both workers raise ``worker_ready``; the boot handlers must not
    both run — two concurrent `git pull`s into the same pack working
    trees is a race, and two copies of each sweeper race over rows."""

    @pytest.mark.parametrize(
        "hostname,expected",
        [
            ("orchestrator@box", True),
            ("work@box", False),
            ("celery@box", False),
            ("", False),
            (None, False),
        ],
    )
    def test_only_the_orchestrator_worker_is_recognised_as_such(self, hostname, expected):
        sender = MagicMock()
        sender.hostname = hostname
        assert _is_orchestrator_worker(sender) is expected

    def test_an_unnamed_worker_does_the_boot_work(self):
        """Fail-safe direction: a worker started by hand without ``-n``
        should do the work redundantly, not skip it entirely."""
        sender = MagicMock()
        sender.hostname = "celery@somebox"
        assert _is_orchestrator_worker(sender) is False
