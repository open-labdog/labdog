"""BUG-167: a failure the playbook ignores or rescues is not a failure.

ansible-runner sends ``runner_on_failed`` for a failed task even when the
task has ``ignore_errors`` (with ``event_data.ignore_errors`` true) or sits
in a ``block`` whose ``rescue`` handles it (no flag at all). Ansible counts
the first as ``ok``/``ignored`` and the second as ``rescued``; both
aggregators here counted them as failures. The event shapes below are the
ones ansible-runner 2.4 emitted for a real local run (ansible-core 2.21).
"""

from __future__ import annotations

from app.ansible_runtime.outcomes import aggregate_module_outcomes
from app.sync.orchestrator import _runner_events_to_task_events
from app.tasks.action_group import _HostCtx, _route_runner_events_to_hosts

PLAY = "LabDog Package Management"


def _event(kind: str, task: str, *, host: str = "h", ignore_errors=None, msg: str = "") -> dict:
    data = {"play": PLAY, "task": task, "host": host}
    if kind == "runner_on_failed":
        data["ignore_errors"] = ignore_errors
        data["res"] = {"msg": msg or f"{task} failed"}
    return {"event": kind, "event_data": data}


def _stats(*, failures=None, rescued=None, ignored=None, dark=None) -> dict:
    return {
        "event": "playbook_on_stats",
        "event_data": {
            "failures": failures or {},
            "rescued": rescued or {},
            "ignored": ignored or {},
            "dark": dark or {},
        },
    }


class TestSyncOutcomes:
    def test_an_ignored_failure_leaves_the_module_in_sync(self):
        events = [
            _event("runner_on_failed", "Remove packages: a, b", ignore_errors=True),
            _event("runner_on_ok", "Remove one by one: a"),
        ]

        projected = _runner_events_to_task_events(events)

        assert [e["failed"] for e in projected] == [False, False]
        assert aggregate_module_outcomes(projected, ["packages"]) == {"packages": "in_sync"}

    def test_a_real_failure_still_errors_the_module(self):
        events = [
            _event("runner_on_failed", "Remove packages: a, b", ignore_errors=True),
            _event("runner_on_failed", "Remove one by one: b", ignore_errors=False),
        ]

        projected = _runner_events_to_task_events(events)

        assert aggregate_module_outcomes(projected, ["packages"]) == {"packages": "error"}


def _ctxs(*names: str) -> list[_HostCtx]:
    return [_HostCtx(host_id=i, host_run_id=100 + i, inv_name=n) for i, n in enumerate(names)]


class TestGroupActionOutcomes:
    def test_an_ignored_failure_does_not_fail_the_member(self):
        outcomes = _route_runner_events_to_hosts(
            _ctxs("a"),
            [
                _event("runner_on_failed", "probe", host="a", ignore_errors=True),
                _event("runner_on_ok", "upgrade", host="a"),
                _stats(ignored={"a": 1}),
            ],
            "successful",
            0,
        )
        assert outcomes.succeeded == {100: True}

    def test_a_rescued_failure_does_not_fail_the_member(self):
        outcomes = _route_runner_events_to_hosts(
            _ctxs("a", "b"),
            [
                _event("runner_on_failed", "drain", host="a", ignore_errors=False),
                _event("runner_on_ok", "drain with force", host="a"),
                _event("runner_on_ok", "drain", host="b"),
                _stats(rescued={"a": 1}),
            ],
            "successful",
            0,
        )
        assert outcomes.succeeded == {100: True, 101: True}

    def test_a_real_failure_fails_only_that_member_with_its_message(self):
        outcomes = _route_runner_events_to_hosts(
            _ctxs("a", "b"),
            [
                # Rescued first, then a real failure the host stops at.
                _event("runner_on_failed", "drain", host="a", msg="drain timed out"),
                _event("runner_on_failed", "upgrade", host="a", msg="kubeadm failed"),
                _event("runner_on_ok", "upgrade", host="b"),
                _stats(failures={"a": 1}, rescued={"a": 1}),
            ],
            "failed",
            2,
        )
        assert outcomes.succeeded == {100: False, 101: True}
        assert outcomes.error_message == {100: "kubeadm failed"}

    def test_without_stats_the_failure_events_decide(self):
        """A run cut short sends no stats: every failure event counts."""
        outcomes = _route_runner_events_to_hosts(
            _ctxs("a"),
            [_event("runner_on_failed", "upgrade", host="a", msg="boom")],
            "failed",
            1,
        )
        assert outcomes.succeeded == {100: False}
        assert outcomes.error_message == {100: "boom"}

    def test_an_unreachable_member_fails(self):
        outcomes = _route_runner_events_to_hosts(
            _ctxs("a"),
            [
                {"event": "runner_on_unreachable", "event_data": {"host": "a"}},
                _stats(dark={"a": 1}),
            ],
            "failed",
            4,
        )
        assert outcomes.error_message == {100: "host unreachable"}
