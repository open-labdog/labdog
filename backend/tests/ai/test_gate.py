"""The permission gate — the one place autonomy is decided.

Both runners consult it before a call runs, so this file is the autonomy
model for the whole feature rather than for one backend. Three failure
modes are specifically pinned, because none announces itself: gating a
tool on its ceiling instead of its arguments (which would make every
read-only session useless), letting a session call a tool it was never
offered, and letting an approval promote a denylisted command.
"""

from __future__ import annotations

import pytest

from app.ai.gate import decide
from app.ai.tools import TOOL_REGISTRY

READ = "journalctl -u nginx -n 50"
WRITE = "systemctl restart nginx"
DENIED = "rm -rf /"


@pytest.fixture
def permitted() -> dict:
    return dict(TOOL_REGISTRY)


def ssh(command: str, **extra):
    return {"host_id": 1, "command": command, **extra}


class TestArgumentsDecideNotTheCeiling:
    """``run_ssh_command`` is declared ``mutating`` because it can be.

    Gating on that alone would refuse ``journalctl`` in a read-only
    session — which is every session LabDog runs by default — making the
    tool useless for the thing it mostly does.
    """

    def test_a_read_is_allowed_in_a_read_only_session(self, permitted) -> None:
        d = decide("run_ssh_command", ssh(READ), permitted=permitted, autonomy_level="read_only")
        assert d.allowed is True
        assert d.classification == "read_only"

    def test_the_declared_ceiling_really_is_mutating(self) -> None:
        """Guards the premise: if this stops being a ceiling, the test
        above stops proving anything."""
        assert TOOL_REGISTRY["run_ssh_command"].classification == "mutating"

    def test_a_write_is_refused_in_a_read_only_session(self, permitted) -> None:
        d = decide("run_ssh_command", ssh(WRITE), permitted=permitted, autonomy_level="read_only")
        assert d.allowed is False
        assert d.classification == "mutating"
        assert d.needs_approval is False, "read_only is a refusal, not a pending approval"

    def test_a_missing_command_does_not_crash_the_gate(self, permitted) -> None:
        """A malformed call must produce a verdict, not an exception —
        an exception here would fail open or kill the session."""
        d = decide(
            "run_ssh_command", {"host_id": 1}, permitted=permitted, autonomy_level="read_only"
        )
        assert d.allowed is False


class TestAutonomyLevels:
    def test_full_auto_permits_a_write(self, permitted) -> None:
        d = decide("run_ssh_command", ssh(WRITE), permitted=permitted, autonomy_level="full_auto")
        assert d.allowed is True

    def test_approval_refuses_but_marks_it_pending(self, permitted) -> None:
        """The distinction the whole approval flow rests on: this call can
        proceed once a human says so, unlike a denylisted one that never
        can."""
        d = decide("run_ssh_command", ssh(WRITE), permitted=permitted, autonomy_level="approval")
        assert d.allowed is False
        assert d.needs_approval is True

    def test_a_pending_decision_carries_its_verdict(self, permitted) -> None:
        """The caller records *why* the classifier called this a write.
        Without the verdict travelling with the decision it would have to
        re-run the classifier, which could answer differently."""
        d = decide("run_ssh_command", ssh(WRITE), permitted=permitted, autonomy_level="approval")
        assert d.verdict is not None
        assert d.verdict.classification == "mutating"
        assert d.verdict.segment == WRITE

    @pytest.mark.parametrize("level", ["read_only", "approval", "full_auto"])
    def test_the_denylist_holds_at_every_level(self, permitted, level: str) -> None:
        d = decide("run_ssh_command", ssh(DENIED), permitted=permitted, autonomy_level=level)
        assert d.allowed is False
        assert d.classification == "denied"
        assert d.needs_approval is False, "no approval can unblock a denylisted command"

    def test_a_read_only_tool_needs_no_autonomy(self, permitted) -> None:
        d = decide("list_hosts", {}, permitted=permitted, autonomy_level="read_only")
        assert d.allowed is True


class TestTheSessionToolsetIsEnforced:
    """``AISession.allowed_tools`` bounds spend and blast radius. The
    model can still *ask* for a name it was never offered, so the bound
    has to hold here and not only when the toolset is built."""

    def test_a_tool_outside_the_session_is_refused(self) -> None:
        narrowed = {"query_loki": TOOL_REGISTRY["query_loki"]}
        d = decide("run_ssh_command", ssh(READ), permitted=narrowed, autonomy_level="full_auto")
        assert d.allowed is False
        assert "not available to this session" in d.reason
        assert "query_loki" in d.reason, "should name what it may use instead"

    def test_an_unknown_tool_is_refused(self, permitted) -> None:
        d = decide("exfiltrate", {}, permitted=permitted, autonomy_level="full_auto")
        assert d.allowed is False
        assert d.classification == "denied"

    def test_an_empty_toolset_refuses_everything(self) -> None:
        d = decide("list_hosts", {}, permitted={}, autonomy_level="full_auto")
        assert d.allowed is False

    def test_an_mcp_qualified_name_is_not_silently_accepted(self, permitted) -> None:
        """The gate takes LabDog's own names; the SDK runner unqualifies
        before calling. Getting that wrong must fail closed — a gate that
        quietly accepted both forms would hide the mistake until someone
        renamed the MCP server.
        """
        d = decide(
            "mcp__labdog__run_ssh_command",
            ssh(READ),
            permitted=permitted,
            autonomy_level="full_auto",
        )
        assert d.allowed is False
