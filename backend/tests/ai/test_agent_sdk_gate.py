"""The permission gate is the SDK runner's autonomy enforcement.

The SDK asks before dispatching a call and does not run what this
refuses, so these tests are the autonomy model for that runner. Two
failure modes are specifically pinned, because neither announces itself:
gating a tool on its ceiling instead of its arguments (which would make
every read-only session useless), and letting a session call a tool it
was never offered.
"""

from __future__ import annotations

import pytest

from app.ai.agent_sdk import decide
from app.ai.agent_sdk.bridge import local_tool_name, qualified_tool_name
from app.ai.tools import TOOL_REGISTRY

READ = "journalctl -u nginx -n 50"
WRITE = "systemctl restart nginx"
DENIED = "rm -rf /"


@pytest.fixture
def permitted() -> dict:
    return dict(TOOL_REGISTRY)


def ssh(command: str, **extra):
    return {"host_id": 1, "command": command, **extra}


class TestNameMapping:
    def test_round_trip(self) -> None:
        assert local_tool_name(qualified_tool_name("list_hosts")) == "list_hosts"

    def test_qualified_form_is_what_the_model_sees(self) -> None:
        assert qualified_tool_name("list_hosts") == "mcp__labdog__list_hosts"

    def test_an_unqualified_name_passes_through(self) -> None:
        """The callback gets qualified names; logs and tests carry both."""
        assert local_tool_name("list_hosts") == "list_hosts"


class TestArgumentsDecideNotTheCeiling:
    """``run_ssh_command`` is declared ``mutating`` because it can be.

    Gating on that alone would refuse ``journalctl`` in a read-only
    session — which is every session LabDog runs by default — making the
    SDK runner strictly less capable than the loop for no safety gain.
    """

    def test_a_read_is_allowed_in_a_read_only_session(self, permitted) -> None:
        d = decide(
            qualified_tool_name("run_ssh_command"),
            ssh(READ),
            permitted=permitted,
            autonomy_level="read_only",
        )
        assert d.allowed is True
        assert d.classification == "read_only"

    def test_the_declared_ceiling_really_is_mutating(self) -> None:
        """Guards the premise: if this stops being a ceiling, the test
        above stops proving anything."""
        assert TOOL_REGISTRY["run_ssh_command"].classification == "mutating"

    def test_a_write_is_refused_in_a_read_only_session(self, permitted) -> None:
        d = decide(
            qualified_tool_name("run_ssh_command"),
            ssh(WRITE),
            permitted=permitted,
            autonomy_level="read_only",
        )
        assert d.allowed is False
        assert d.classification == "mutating"
        assert d.needs_approval is False, "read_only is a refusal, not a pending approval"

    def test_a_missing_command_does_not_crash_the_gate(self, permitted) -> None:
        """A malformed call must produce a verdict, not an exception —
        an exception here would fail open or kill the session."""
        d = decide(
            qualified_tool_name("run_ssh_command"),
            {"host_id": 1},
            permitted=permitted,
            autonomy_level="read_only",
        )
        assert d.allowed is False


class TestAutonomyLevels:
    def test_full_auto_permits_a_write(self, permitted) -> None:
        d = decide(
            qualified_tool_name("run_ssh_command"),
            ssh(WRITE),
            permitted=permitted,
            autonomy_level="full_auto",
        )
        assert d.allowed is True

    def test_approval_refuses_but_marks_it_pending(self, permitted) -> None:
        """The distinction Phase 3 needs: this call can proceed once a
        human says so, unlike a denylisted one that never can."""
        d = decide(
            qualified_tool_name("run_ssh_command"),
            ssh(WRITE),
            permitted=permitted,
            autonomy_level="approval",
        )
        assert d.allowed is False
        assert d.needs_approval is True

    @pytest.mark.parametrize("level", ["read_only", "approval", "full_auto"])
    def test_the_denylist_holds_at_every_level(self, permitted, level: str) -> None:
        d = decide(
            qualified_tool_name("run_ssh_command"),
            ssh(DENIED),
            permitted=permitted,
            autonomy_level=level,
        )
        assert d.allowed is False
        assert d.classification == "denied"
        assert d.needs_approval is False, "no approval can unblock a denylisted command"

    def test_a_read_only_tool_needs_no_autonomy(self, permitted) -> None:
        d = decide(
            qualified_tool_name("list_hosts"), {}, permitted=permitted, autonomy_level="read_only"
        )
        assert d.allowed is True


class TestTheSessionToolsetIsEnforced:
    """``AISession.allowed_tools`` bounds spend and blast radius. The
    model can still *ask* for a name it was never offered, so the bound
    has to hold here and not only when the toolset is built."""

    def test_a_tool_outside_the_session_is_refused(self) -> None:
        narrowed = {"query_loki": TOOL_REGISTRY["query_loki"]}
        d = decide(
            qualified_tool_name("run_ssh_command"),
            ssh(READ),
            permitted=narrowed,
            autonomy_level="full_auto",
        )
        assert d.allowed is False
        assert "not available to this session" in d.reason
        assert "query_loki" in d.reason, "should name what it may use instead"

    def test_an_unknown_tool_is_refused(self, permitted) -> None:
        d = decide(
            qualified_tool_name("exfiltrate"),
            {},
            permitted=permitted,
            autonomy_level="full_auto",
        )
        assert d.allowed is False
        assert d.classification == "denied"

    def test_an_empty_toolset_refuses_everything(self) -> None:
        d = decide(qualified_tool_name("list_hosts"), {}, permitted={}, autonomy_level="full_auto")
        assert d.allowed is False
