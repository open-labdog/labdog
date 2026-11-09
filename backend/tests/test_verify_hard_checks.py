"""Characterisation tests for the SSH hard checks in ``run_verification``.

``tests/test_ai_verify.py`` reaches ``run_verification`` only to show a
dry run buys no AI verdict. These pin the checks themselves: which rules
are checked, how each reading is taken and reported, what an unreachable
host returns, and the fallback when the AI verifier raises.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

from app.workflows.steps import verify as verify_step


class _Result:
    def __init__(self, stdout: str = "", exit_status: int = 0) -> None:
        self.stdout = stdout
        self.exit_status = exit_status
        self.stderr = ""


def _host(**replies):
    """A fake connection answering by command prefix; ``replies`` override."""
    answers = {
        "systemctl is-active": "active",
        "dpkg-query": "install ok installed",
        "rpm -q": _Result("", 1),
        "cat /proc/loadavg": "0.42 0.30 0.25 1/200 1234",
        "df": "37%",
        "id -u": "0",
        "journalctl": "",
        "systemctl list-units": "",
    }
    answers.update(replies)
    commands: list[str] = []

    class _Conn:
        async def run(self, command: str, check: bool = False):  # noqa: ARG002, FBT001, FBT002
            commands.append(command)
            for prefix, answer in answers.items():
                if command.startswith(prefix) or (prefix == "journalctl" and prefix in command):
                    if callable(answer):
                        answer = answer(command)
                    if isinstance(answer, Exception):
                        raise answer
                    return answer if isinstance(answer, _Result) else _Result(answer)
            raise AssertionError(command)

    @asynccontextmanager
    async def _connect(*_a, **_kw):
        yield _Conn()

    return _connect, commands


async def _verify(connect, services=(), packages=(), prompt=None, **kw):
    host = SimpleNamespace(id=1, hostname="h", ip_address="10.0.0.9")
    with patch.object(verify_step, "ssh_connect_host", connect):
        return await verify_step.run_verification(
            host, "/tmp/key", list(services), list(packages), prompt, None, **kw
        )


def _svc(name, **state):
    return SimpleNamespace(service_name=name, **state)


def _pkg(name, **state):
    return SimpleNamespace(package_name=name, **state)


async def test_an_unreachable_host_fails_with_nothing_collected():
    @asynccontextmanager
    async def _refused(*_a, **_kw):
        raise OSError("no route to host")
        yield  # pragma: no cover

    result = await _verify(_refused)

    assert result == {
        "passed": False,
        "hard_checks": {
            "services": [],
            "packages": [],
            "load": None,
            "disk_pct": None,
            "journal_errors": None,
            "journal_error_reason": "the SSH connection failed: no route to host",
        },
        "ai_result": None,
    }


async def test_only_running_services_are_checked_and_state_is_the_fallback():
    connect, commands = _host(
        **{"systemctl is-active": lambda c: "active" if c.endswith("nginx") else "inactive"}
    )
    by_desired = await _verify(
        connect,
        services=[
            _svc("nginx", desired_state="running"),
            _svc("cron", desired_state="stopped"),
            _svc("sshd", state="running"),
        ],
    )
    assert [r["name"] for r in by_desired["hard_checks"]["services"]] == ["nginx"]

    by_state = await _verify(
        connect, services=[_svc("nginx", state="running"), _svc("redis", state="running")]
    )
    assert by_state["hard_checks"]["services"] == [
        {"name": "nginx", "expected": "active", "actual": "active", "ok": True},
        {"name": "redis", "expected": "active", "actual": "inactive", "ok": False},
    ]
    assert by_state["passed"] is False
    assert by_state["services_ok"] is False
    assert by_state["services_checked"] == 2
    assert "systemctl is-active redis" in commands


async def test_a_service_check_error_is_reported_as_its_state():
    connect, _ = _host(**{"systemctl is-active": RuntimeError("channel closed")})
    result = await _verify(connect, services=[_svc("nginx", desired_state="running")])
    assert result["hard_checks"]["services"] == [
        {"name": "nginx", "expected": "active", "actual": "error: channel closed", "ok": False}
    ]


async def test_packages_try_dpkg_then_rpm():
    connect, commands = _host(
        **{
            "dpkg-query": lambda c: "install ok installed" if " deb " in c else "",
            "rpm -q": lambda c: _Result("", 0 if c.endswith("rhel") else 1),
        }
    )
    result = await _verify(
        connect,
        packages=[
            _pkg("deb", desired_state="present"),
            _pkg("rhel", desired_state="latest"),
            _pkg("gone", desired_state="present"),
            _pkg("skip", desired_state="absent"),
        ],
    )
    assert result["hard_checks"]["packages"] == [
        {"name": "deb", "expected": "present", "installed": True, "ok": True},
        {"name": "rhel", "expected": "latest", "installed": True, "ok": True},
        {"name": "gone", "expected": "present", "installed": False, "ok": False},
    ]
    assert "rpm -q deb" not in commands
    assert result["packages_checked"] == 3
    assert result["packages_ok"] is False


async def test_a_package_rule_with_only_state_is_checked_and_reported_with_it():
    connect, _ = _host(**{"dpkg-query": RuntimeError("dpkg locked")})
    result = await _verify(connect, packages=[_pkg("vim", state="latest")])
    assert result["hard_checks"]["packages"] == [
        {"name": "vim", "expected": "latest", "installed": False, "ok": False}
    ]


async def test_unreadable_load_and_disk_are_none_not_zero():
    connect, _ = _host(**{"cat /proc/loadavg": "", "df": RuntimeError("df hung")})
    checks = (await _verify(connect))["hard_checks"]
    assert checks["load"] is None
    assert checks["disk_pct"] is None


async def test_readings_are_parsed():
    connect, _ = _host(**{"cat /proc/loadavg": "12.50 1 1 1/2 3", "df": " 96%\n"})
    checks = (await _verify(connect))["hard_checks"]
    assert checks["load"] == 12.5
    assert checks["disk_pct"] == 96


async def test_active_services_no_rule_mentions_are_listed():
    connect, _ = _host(
        **{
            "systemctl list-units": (
                "nginx.service loaded active running nginx\n"
                "  docker.service loaded active running Docker\n"
                "\n"
                "ssh loaded active running OpenSSH\n"
            )
        }
    )
    result = await _verify(connect, services=[_svc("nginx", desired_state="stopped")])
    assert result["hard_checks"]["unmanaged_services"] == ["docker", "ssh"]


async def test_an_unmanaged_scan_failure_leaves_the_list_empty():
    connect, _ = _host(**{"systemctl list-units": RuntimeError("timeout")})
    result = await _verify(connect)
    assert result["hard_checks"]["unmanaged_services"] == []
    assert result["passed"] is True


async def test_an_ai_verifier_that_raises_resolves_by_the_fail_closed_policy():
    connect, _ = _host()

    async def _broken(*_a, **_kw):
        raise ValueError("provider exploded")

    with patch("app.workflows.steps.ai_verify.run_ai_verification", _broken):
        open_ = await _verify(connect, prompt="check nginx")
        closed = await _verify(connect, prompt="check nginx", ai_fail_closed=True)

    expected_output = "AI verification errored before reaching a verdict: provider exploded"
    assert open_["ai_result"] == {
        "passed": True,
        "verdict": "inconclusive",
        "output": expected_output,
        "session_id": None,
    }
    assert open_["passed"] is True
    assert closed["ai_result"]["passed"] is False
    assert closed["passed"] is False


async def test_journal_errors_alone_bring_in_the_ai_verifier_but_a_failed_check_does_not():
    calls: list[tuple] = []

    async def _spy(system_state, prompt, **kw):
        calls.append((system_state, prompt, kw))
        return {"passed": True, "verdict": "pass", "output": "ok", "session_id": 4}

    with patch("app.workflows.steps.ai_verify.run_ai_verification", _spy):
        noisy, _ = _host(journalctl="kernel: oops")
        await _verify(noisy, action_run_id=12)
        failing, _ = _host(**{"systemctl is-active": "failed"})
        await _verify(
            failing, services=[_svc("nginx", desired_state="running")], prompt="look at it"
        )

    assert len(calls) == 1
    system_state, prompt, kw = calls[0]
    assert prompt == ""
    assert system_state["host_hostname"] == "h"
    assert system_state["host_ip"] == "10.0.0.9"
    assert system_state["hard_checks"]["journal_errors"] == "kernel: oops"
    assert kw == {"fail_closed": False, "host_id": 1, "action_run_id": 12}
