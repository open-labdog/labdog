"""Tests for ansible-runner failure interpretation."""

from app.ansible_runtime.diagnose import interpret_runner_failure


class _FakeRunner:
    """Minimal stand-in for ansible_runner.Runner used in tests."""

    def __init__(
        self,
        events: list[dict] | None = None,
        status: str = "failed",
        rc: int = 4,
    ) -> None:
        self.events = events or []
        self.status = status
        self.rc = rc


def _unreachable_event(msg: str) -> dict:
    return {
        "event": "runner_on_unreachable",
        "event_data": {"res": {"msg": msg}},
    }


def _failed_event(msg: str) -> dict:
    return {
        "event": "runner_on_failed",
        "event_data": {"res": {"msg": msg}},
    }


def test_no_space_left_translated():
    runner = _FakeRunner(
        events=[
            _unreachable_event(
                "Task failed: mkdir: cannot create directory "
                "'/home/keymaster/.ansible/tmp/...': No space left on device"
            )
        ]
    )
    assert "out of disk space" in interpret_runner_failure(runner)


def test_connection_refused_translated():
    runner = _FakeRunner(
        events=[_unreachable_event("Failed to connect: Connection refused")]
    )
    assert "SSH connection refused" in interpret_runner_failure(runner)


def test_permission_denied_translated():
    runner = _FakeRunner(
        events=[_unreachable_event("Permission denied (publickey).")]
    )
    assert "SSH authentication failed" in interpret_runner_failure(runner)


def test_dns_resolution_translated():
    runner = _FakeRunner(
        events=[_unreachable_event("ssh: Could not resolve hostname foo: ...")]
    )
    assert "DNS resolution failed" in interpret_runner_failure(runner)


def test_unknown_msg_falls_through_truncated():
    long_msg = "Some weird error " * 30  # ~510 chars
    runner = _FakeRunner(events=[_failed_event(long_msg)])
    out = interpret_runner_failure(runner)
    assert out.startswith("Some weird error")
    assert len(out) <= 240


def test_no_events_uses_status_fallback():
    runner = _FakeRunner(events=[], status="timeout", rc=124)
    out = interpret_runner_failure(runner)
    assert "timeout" in out
    assert "124" in out


def test_module_stderr_used_when_msg_missing():
    runner = _FakeRunner(
        events=[
            {
                "event": "runner_on_failed",
                "event_data": {
                    "res": {"module_stderr": "Permission denied (publickey)."}
                },
            }
        ]
    )
    assert "SSH authentication failed" in interpret_runner_failure(runner)


def test_non_failure_events_ignored():
    runner = _FakeRunner(
        events=[
            {"event": "playbook_on_start", "event_data": {}},
            {"event": "runner_on_ok", "event_data": {"res": {"msg": "ok"}}},
        ],
        status="successful",
        rc=0,
    )
    # No failure event present — falls back to status/rc summary.
    out = interpret_runner_failure(runner)
    assert "successful" in out


def test_first_known_cause_wins_over_later_event():
    runner = _FakeRunner(
        events=[
            _unreachable_event("No space left on device"),
            _unreachable_event("Connection refused"),
        ]
    )
    assert "out of disk space" in interpret_runner_failure(runner)
