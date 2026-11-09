"""Step 3 of the scaling plan: one image, several runtime roles.

``python -m app --role ...`` (or ``LABDOG_ROLE``) picks which processes a
container runs. ``all`` must stay exactly the single-container install;
the split roles must between them consume every queue a task can land on,
or work is accepted by the broker and never run.
"""

import fnmatch

import pytest

from app.celery_manager import (
    BEAT,
    ROLE_PROCESSES,
    WORKER_QUEUES,
    CeleryManager,
    parse_roles,
    processes_for,
)
from app.tasks import celery_app


@pytest.fixture(autouse=True, scope="module")
def _load_task_modules():
    celery_app.loader.import_default_modules()


def _queue_for(task_name: str) -> str:
    for pattern, route in celery_app.conf.task_routes.items():
        if fnmatch.fnmatch(task_name, pattern):
            return route["queue"]
    return celery_app.conf.task_default_queue


class TestParsingRoles:
    def test_nothing_means_all(self):
        assert parse_roles(None) == ("all",)
        assert parse_roles("") == ("all",)

    def test_a_comma_list_keeps_order_and_drops_repeats(self):
        assert parse_roles("worker, worker-drift,worker") == ("worker", "worker-drift")

    def test_an_unknown_role_is_refused(self):
        with pytest.raises(ValueError, match="unknown role"):
            parse_roles("worker,wrker")

    def test_all_cannot_be_combined(self):
        with pytest.raises(ValueError, match="cannot be combined"):
            parse_roles("all,beat")


class TestWhatEachRoleRuns:
    def test_all_is_the_single_container_install(self):
        assert processes_for(("all",)) == {**WORKER_QUEUES, BEAT: None}

    def test_api_runs_no_celery(self):
        assert processes_for(("api",)) == {}

    def test_worker_and_drift_worker_split_the_work_queues(self):
        assert processes_for(("worker",)) == {"work": ("default", "long_running")}
        assert processes_for(("worker-drift",)) == {"drift": ("drift",)}
        assert processes_for(("worker-actions",)) == {"actions": ("actions",)}

    def test_the_orchestrator_keeps_its_node_name_prefix(self):
        """``_is_orchestrator_worker`` keys off ``orchestrator@``."""
        assert list(processes_for(("orchestrator",))) == ["orchestrator"]

    def test_exactly_one_role_runs_beat_in_a_split_deployment(self):
        split = [r for r in ROLE_PROCESSES if r != "all"]
        assert [r for r in split if BEAT in ROLE_PROCESSES[r]] == ["beat"]


class TestQueuesAreCoveredEitherWay:
    def _consumed(self, roles):
        return {q for queues in processes_for(roles).values() if queues for q in queues}

    def test_the_split_roles_consume_what_all_consumes(self):
        split = tuple(r for r in ROLE_PROCESSES if r != "all")
        assert self._consumed(split) == self._consumed(("all",)) == set(CeleryManager.QUEUES)

    def test_every_task_lands_on_a_queue_the_split_roles_consume(self):
        split = tuple(r for r in ROLE_PROCESSES if r != "all")
        consumed = self._consumed(split)
        stranded = {
            n: q
            for n in celery_app.tasks
            if not n.startswith("celery.") and (q := _queue_for(n)) not in consumed
        }
        assert not stranded


class TestDriftHasItsOwnQueue:
    @pytest.mark.parametrize(
        "task_name",
        [
            "app.tasks.drift.check_all_drift",
            "app.tasks.cron_drift.check_all_cron_drift",
            "app.tasks.package_drift.check_all_package_drift",
            "app.tasks.resolver_drift.check_all_resolver_drift",
            "app.tasks.drift_sweep.check_host_module",
        ],
    )
    def test_drift_tasks_route_to_drift(self, task_name):
        assert task_name in celery_app.tasks
        assert _queue_for(task_name) == "drift"
        # The decorator's queue= wins over task_routes when a task is
        # called directly, so it has to agree.
        assert celery_app.tasks[task_name].queue in (None, "drift")

    @pytest.mark.parametrize(
        ("task_name", "queue"),
        [
            # On-demand pseudo-action: part of an action run, not a sweep.
            ("app.tasks.builtin_dispatchers.run_builtin_drift_check", "actions"),
            ("app.tasks.drift_retention.prune_old_drift_samples", "default"),
        ],
    )
    def test_lookalikes_stay_where_they_were(self, task_name, queue):
        if task_name not in celery_app.tasks:
            pytest.skip(f"{task_name} not registered under this name")
        assert _queue_for(task_name) == queue

    def test_prefetch_is_one(self):
        assert celery_app.conf.worker_prefetch_multiplier == 1


class TestActionsHaveTheirOwnQueue:
    """An action run's children must not wait behind bulk syncs on
    long_running (load test in PR #270: no parallelism-10 run finished
    within an hour while syncs filled that queue)."""

    @pytest.mark.parametrize(
        "task_name",
        [
            "app.tasks.action_host.run_action_host",
            "app.tasks.action_group.run_action_group",
            "app.tasks.builtin_dispatchers.run_builtin_sync",
            "app.tasks.builtin_dispatchers.run_builtin_collect_state",
        ],
    )
    def test_action_children_route_to_actions(self, task_name):
        if task_name not in celery_app.tasks:
            pytest.skip(f"{task_name} not registered under this name")
        assert _queue_for(task_name) == "actions"
        assert celery_app.tasks[task_name].queue in (None, "actions")

    def test_the_orchestrator_publishes_its_children_there(self):
        from app.tasks.action_orchestrator import CHILD_QUEUE

        assert CHILD_QUEUE == "actions"

    def test_syncs_stay_on_long_running(self):
        assert _queue_for("app.tasks.host_sync_orchestrator.run_host_sync") == "long_running"

    def test_only_the_actions_role_serves_it_in_a_split(self):
        split = [r for r in ROLE_PROCESSES if r != "all"]
        serving = [r for r in split for q in ROLE_PROCESSES[r].values() if q and "actions" in q]
        assert serving == ["worker-actions"]


class TestTheManagerSpawnsThePlan:
    @pytest.fixture
    def popen(self, monkeypatch):
        calls = []

        class _P:
            pid = 1

            def poll(self):
                return None

        def _fake(cmd, **kw):
            calls.append((cmd, kw))
            return _P()

        monkeypatch.setattr("app.celery_manager.subprocess.Popen", _fake)
        yield calls
        import app.celery_manager as cm

        cm._active_manager = None

    def test_a_drift_worker_uses_drift_concurrency_and_its_own_node_name(self, popen, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings.celery, "drift_concurrency", 9)
        CeleryManager(processes_for(("worker-drift",)), roles=("worker-drift",)).start()

        ((cmd, kw),) = popen
        assert "--concurrency=9" in cmd
        assert cmd[cmd.index("-Q") + 1] == "drift"
        assert cmd[cmd.index("-n") + 1] == "drift@%h"
        assert kw["env"]["LABDOG_ROLE"] == "worker-drift"

    def test_an_actions_worker_uses_actions_concurrency(self, popen, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings.celery, "actions_concurrency", 7)
        CeleryManager(processes_for(("worker-actions",)), roles=("worker-actions",)).start()

        ((cmd, kw),) = popen
        assert "--concurrency=7" in cmd
        assert cmd[cmd.index("-Q") + 1] == "actions"
        assert cmd[cmd.index("-n") + 1] == "actions@%h"
        assert kw["env"]["LABDOG_ROLE"] == "worker-actions"

    def test_the_default_manager_still_runs_everything(self, popen):
        CeleryManager().start()
        names = [kw["env"]["LABDOG_ROLE"] for _cmd, kw in popen]
        assert len(popen) == len(WORKER_QUEUES) + 1
        assert set(names) == {"all"}


class TestPerContainerBootWork:
    @pytest.mark.parametrize(
        ("role", "sibling"),
        [("all", True), ("orchestrator", False), ("orchestrator,worker", True), ("beat", False)],
    )
    def test_an_orchestrator_alone_syncs_its_own_packs(self, monkeypatch, role, sibling):
        from app.tasks import _sibling_syncs_packs

        monkeypatch.setenv("LABDOG_ROLE", role)
        assert _sibling_syncs_packs() is sibling

    def test_only_one_booting_worker_enqueues_the_orphan_sweeps(self, monkeypatch):
        from types import SimpleNamespace

        from app.tasks import _sweep_orphans_on_worker_start

        taken = set()

        class _R:
            def set(self, key, value, nx=False, ex=None):
                if key in taken:
                    return None
                taken.add(key)
                return True

        sent = []
        monkeypatch.setattr("redis.from_url", lambda _url: _R())
        monkeypatch.setattr(celery_app, "send_task", lambda name, **_kw: sent.append(name))

        for host in ("work@a", "work@b", "drift@c"):
            _sweep_orphans_on_worker_start(sender=SimpleNamespace(hostname=host))

        assert len(sent) == 2


class TestTheEntrypoint:
    def test_a_bad_role_is_a_usage_error(self, monkeypatch):
        import app.__main__ as entry

        monkeypatch.setattr("sys.argv", ["app", "--role", "nope"])
        with pytest.raises(SystemExit) as exc:
            entry.main()
        assert exc.value.code == 2

    def test_the_schema_wait_knows_the_heads(self, monkeypatch):
        """A worker role must find the migration heads wherever it starts."""
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        import app.__main__ as entry

        cfg = Config(str(entry.BACKEND_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(entry.BACKEND_DIR / "alembic"))
        assert ScriptDirectory.from_config(cfg).get_heads()
