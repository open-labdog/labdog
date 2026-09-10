"""The per-host action lifecycle, phase by phase.

``app.tasks.action_host`` used to be one 1112-line function carrying
``# noqa: C901, PLR0912, PLR0915``. Its phases are now named functions
matching ``action_group``'s Phase A–G, which makes the decisions that were
buried in the middle of that function directly testable for the first time.

These are the decisions worth pinning: which of snapshot / rollback /
cleanup runs for a given outcome. Getting one wrong does not fail loudly —
it reverts a host that succeeded, or leaves a snapshot behind on one that
failed — so the routing table is asserted here rather than inferred.
"""

import json

import pytest

from app.tasks.action_host import (
    _Envelope,
    _finish_early,
    _phase_a_snapshot,
    _phase_ef_rollback_or_cleanup,
    _publish,
    _RunCtx,
    _RunSpec,
)


class _FakeRedis:
    """Records publishes; optionally fails like a broker having a moment."""

    def __init__(self, *, explode: bool = False):
        self.published: list[tuple[str, dict]] = []
        self._explode = explode

    def publish(self, channel: str, payload: str) -> None:
        if self._explode:
            raise ConnectionError("broker unreachable")
        self.published.append((channel, json.loads(payload)))

    def exists(self, _key: str) -> int:
        return 0


def _ctx(*, explode: bool = False) -> _RunCtx:
    return _RunCtx(
        action_run_id=1,
        host_run_id=2,
        channel="actions.run.1",
        r=_FakeRedis(explode=explode),
        private_data_dir="/tmp/does-not-exist",
    )


def _spec(**overrides) -> _RunSpec:
    base = dict(
        host_id=10,
        host_ip="10.0.0.1",
        host_hostname="web-01",
        host_port=22,
        ssh_user="root",
        private_key_text="",
        parameters={},
        playbook_path="/pack/playbook.yml",
        action_key="linux-upgrade",
        destructive=True,
        roles_paths=(),
        verify_playbook_path=None,
        verify_timeout=300,
        ai_verify_prompt=None,
        ai_verify_fail_closed=False,
        playbook_timeout=None,
        metrics_backend=None,
        snapshot_enabled=True,
        verify_enabled=True,
        auto_rollback=True,
        post_run_sync=(),
        post_run_register={},
        triggered_by_user_id=None,
        master_key=b"0" * 32,
    )
    base.update(overrides)
    return _RunSpec(**base)


def _mapped_envelope() -> _Envelope:
    """An envelope for a host with a Proxmox mapping and a taken snapshot."""
    return _Envelope(
        client=object(),
        pve_node="pve1",
        vmid=500,
        vm_type="qemu",
        snapshot_name="labdog-1-123",
    )


class TestTheEnvelopePredicates:
    """``can_snapshot`` and ``has_snapshot`` replace four-term conditions."""

    def test_an_empty_envelope_can_do_neither(self):
        env = _Envelope()
        assert env.can_snapshot is False
        assert env.has_snapshot is False

    def test_a_mapping_without_a_snapshot_can_snapshot_but_has_none(self):
        env = _Envelope(client=object(), pve_node="pve1", vmid=500)
        assert env.can_snapshot is True
        assert env.has_snapshot is False

    def test_a_vmid_of_zero_still_counts_as_mapped(self):
        """Guards against a truthiness check creeping in: vmid 0 is a vmid."""
        env = _Envelope(client=object(), pve_node="pve1", vmid=0)
        assert env.can_snapshot is True


class TestPhaseAAnnouncesWhyItSkipped:
    """A destructive run with no snapshot has no rollback. Say so."""

    async def test_an_operator_opt_out_is_recorded(self):
        ctx = _ctx()
        ok = await _phase_a_snapshot(ctx, _spec(snapshot_enabled=False), _Envelope())

        assert ok is True
        assert any("snapshot_enabled=false" in line for line in ctx.step_log)
        assert any("no rollback available" in line for line in ctx.step_log)

    async def test_a_missing_vm_mapping_is_recorded(self):
        ctx = _ctx()
        ok = await _phase_a_snapshot(ctx, _spec(snapshot_enabled=True), _Envelope())

        assert ok is True
        assert any("no Proxmox VM mapping" in line for line in ctx.step_log)

    async def test_a_non_destructive_action_says_nothing(self):
        """No envelope was ever going to run — there is nothing to warn about."""
        ctx = _ctx()
        ok = await _phase_a_snapshot(ctx, _spec(destructive=False), _Envelope())

        assert ok is True
        assert ctx.step_log == []


class TestPhasesEAndFRouteOnOutcome:
    """The routing table: failure rolls back, success cleans up."""

    @pytest.fixture
    def calls(self, monkeypatch):
        seen: dict[str, list] = {"rollback": [], "delete": []}

        async def _fake_rollback(ctx, spec, env):
            seen["rollback"].append(env.snapshot_name)

        async def _fake_delete(ctx, spec, env, *, note):
            seen["delete"].append(note)

        monkeypatch.setattr("app.tasks.action_host._rollback", _fake_rollback)
        monkeypatch.setattr("app.tasks.action_host._delete_snapshot", _fake_delete)
        return seen

    async def test_failure_with_a_snapshot_rolls_back(self, calls):
        await _phase_ef_rollback_or_cleanup(_ctx(), _spec(), _mapped_envelope(), success=False)
        assert calls["rollback"] == ["labdog-1-123"]
        assert calls["delete"] == []

    async def test_success_with_a_snapshot_deletes_it(self, calls):
        await _phase_ef_rollback_or_cleanup(_ctx(), _spec(), _mapped_envelope(), success=True)
        assert calls["delete"] == ["deleted"]
        assert calls["rollback"] == []

    async def test_auto_rollback_off_keeps_the_snapshot_and_says_why(self, calls):
        ctx = _ctx()
        await _phase_ef_rollback_or_cleanup(
            ctx, _spec(auto_rollback=False), _mapped_envelope(), success=False
        )

        assert calls["rollback"] == []
        assert calls["delete"] == [], "the snapshot is the operator's recovery point"
        assert any("auto_rollback=false" in line for line in ctx.step_log)
        assert any("retained for manual recovery" in line for line in ctx.step_log)

    async def test_no_snapshot_means_nothing_to_do_either_way(self, calls):
        for success in (True, False):
            await _phase_ef_rollback_or_cleanup(_ctx(), _spec(), _Envelope(), success=success)
        assert calls == {"rollback": [], "delete": []}

    async def test_a_snapshot_with_no_client_is_not_acted_on(self, calls):
        """snapshot_name set but the Proxmox client gone — nothing can be called."""
        env = _Envelope(pve_node="pve1", vmid=500, snapshot_name="labdog-1-123")
        await _phase_ef_rollback_or_cleanup(_ctx(), _spec(), env, success=True)
        assert calls["delete"] == []


class TestThePublishHelperIsNotLoadBearing:
    """The DB row is authoritative; a lost event only lags the live view."""

    def test_a_broker_failure_does_not_propagate(self):
        ctx = _ctx(explode=True)
        _publish(ctx, {"event": "output", "text": "hello"})

    def test_a_step_log_line_survives_a_broker_failure(self):
        """The log is what gets persisted, so it must not depend on the publish."""
        from app.tasks.action_host import _log_step

        ctx = _ctx(explode=True)
        _log_step(ctx, "[snapshot] created")
        assert ctx.step_log == ["[snapshot] created"]


class TestFinishEarlyWritesATerminalRow:
    """Seven phases end the run early; they all go through one helper now.

    Driven against a stand-in session rather than the test transaction:
    ``_finish_early`` opens its own ``task_session``, as every phase does,
    so a row created in the rolled-back fixture transaction would not be
    visible to it. What matters here is the field set it writes and the
    event it emits, and both are visible without a database.
    """

    async def test_it_sets_status_error_finished_at_and_output(self, monkeypatch):
        from contextlib import asynccontextmanager
        from types import SimpleNamespace

        row = SimpleNamespace(status="running", error_message=None, finished_at=None, output=None)
        committed: list[bool] = []

        class _Result:
            def scalar_one_or_none(self):
                return row

        class _Session:
            async def execute(self, _stmt):
                return _Result()

            async def commit(self):
                committed.append(True)

        @asynccontextmanager
        async def _fake_session():
            yield _Session()

        monkeypatch.setattr("app.db.task_session", _fake_session)

        ctx = _ctx()
        ctx.step_log.append("[preflight] FAILED: unreachable")
        await _finish_early(ctx, "failed", error="host unreachable", include_output=True)

        assert row.status == "failed"
        assert row.error_message == "host unreachable"
        assert row.finished_at is not None
        assert row.output == "[preflight] FAILED: unreachable"
        assert committed == [True]
        assert ctx.r.published[-1][1] == {
            "event": "host_status",
            "host_run_id": ctx.host_run_id,
            "status": "failed",
        }

    async def test_it_leaves_output_alone_unless_asked(self, monkeypatch):
        """Before the run produces output, writing the empty log would only
        overwrite whatever the row already carried."""
        from contextlib import asynccontextmanager
        from types import SimpleNamespace

        row = SimpleNamespace(
            status="running", error_message=None, finished_at=None, output="earlier output"
        )

        class _Result:
            def scalar_one_or_none(self):
                return row

        class _Session:
            async def execute(self, _stmt):
                return _Result()

            async def commit(self):
                return None

        @asynccontextmanager
        async def _fake_session():
            yield _Session()

        monkeypatch.setattr("app.db.task_session", _fake_session)

        await _finish_early(_ctx(), "skipped", error="Host has no SSH key configured")

        assert row.status == "skipped"
        assert row.output == "earlier output"
