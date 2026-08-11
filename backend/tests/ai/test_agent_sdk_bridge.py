"""The tool bridge, and the lockdown posture it depends on.

The bridge is small, but one of its constants is load-bearing in a way
that reads as a no-op: :data:`NO_BUILTIN_TOOLS` is an empty list, and an
empty list is exactly what a careless cleanup deletes. Omitting it does
not disable a feature — it hands the model ``Bash``, ``Read``, ``Write``
and ``Edit`` inside LabDog's container. That was observed live, not
theorised: with the option absent the model ran ``Bash`` before it found
the tool it had been asked to use.
"""

from __future__ import annotations

import pytest

from app.ai.agent_sdk.bridge import (
    MCP_SERVER_NAME,
    NO_BUILTIN_TOOLS,
    build_tool_server,
    local_tool_name,
    qualified_tool_name,
)
from app.ai.tools import TOOL_REGISTRY, ToolHandler, ToolResult
from app.ai.tools.base import tool as labdog_tool

sdk = pytest.importorskip("claude_agent_sdk", reason="optional [agent] extra not installed")


class TestLockdownPosture:
    def test_no_builtin_tools_is_the_empty_list(self) -> None:
        """The SDK maps ``[]`` to ``--tools ""`` (no built-ins) and maps a
        *missing* option to every built-in. The two are opposites, so this
        constant must stay an empty list and must stay passed."""
        assert NO_BUILTIN_TOOLS == []

    def test_it_is_not_none(self) -> None:
        """``None`` is the SDK's "give the model everything" value."""
        assert NO_BUILTIN_TOOLS is not None


class TestNaming:
    def test_the_server_name_forms_the_tool_prefix(self) -> None:
        assert qualified_tool_name("x") == f"mcp__{MCP_SERVER_NAME}__x"

    def test_round_trip(self) -> None:
        assert local_tool_name(qualified_tool_name("list_hosts")) == "list_hosts"

    def test_an_unqualified_name_passes_through(self) -> None:
        """The permission callback gets qualified names; logs and tests
        carry both, and the gate only accepts the local form."""
        assert local_tool_name("list_hosts") == "list_hosts"


async def _never_called(handler: ToolHandler, args: dict) -> ToolResult:  # pragma: no cover
    raise AssertionError("executor should not run during construction")


class TestServerConstruction:
    async def test_it_advertises_every_real_tool(self) -> None:
        """Guards the assumption that LabDog's JSON Schemas are valid SDK
        input schemas — a rejected one should fail here, not on a live
        session."""
        server = build_tool_server(list(TOOL_REGISTRY.values()), _never_called)
        assert await _tool_names(server) == set(TOOL_REGISTRY)

    async def test_it_carries_only_the_handlers_given(self) -> None:
        """A session narrowed by ``allowed_tools`` must not be able to see
        the rest of the registry."""
        server = build_tool_server([TOOL_REGISTRY["list_hosts"]], _never_called)
        assert await _tool_names(server) == {"list_hosts"}

    async def test_an_empty_toolset_advertises_nothing(self) -> None:
        assert await _tool_names(build_tool_server([], _never_called)) == set()


async def _tool_names(server) -> set[str]:
    """Ask the MCP server what it advertises, the way the SDK does.

    A server built with no tools registers no ``tools/list`` handler at
    all, which is the same statement as an empty list.
    """
    from mcp.types import ListToolsRequest

    handler = server["instance"].request_handlers.get(ListToolsRequest)
    if handler is None:
        return set()
    result = await handler(ListToolsRequest(method="tools/list"))
    return {t.name for t in result.root.tools}


class TestAdapter:
    """Each wrapper must call *its own* handler. A shared closure over a
    loop variable would leave every tool running the last handler built —
    silent, and catastrophic once one of them is an SSH tool."""

    async def test_each_wrapper_calls_its_own_handler(self) -> None:
        calls: list[str] = []

        def make(name: str) -> ToolHandler:
            @labdog_tool(name, f"the {name} tool", {"type": "object"}, "read_only")
            async def _run(ctx, args):  # pragma: no cover - not run here
                return ToolResult("unused")

            return _run

        handlers = [make("alpha"), make("beta"), make("gamma")]

        async def execute(handler: ToolHandler, args: dict) -> ToolResult:
            calls.append(handler.spec.name)
            return ToolResult(f"ran {handler.spec.name}")

        wrappers = {h.spec.name: _wrapper_for(h, execute) for h in handlers}
        for name, wrapper in wrappers.items():
            result = await wrapper.handler({})
            assert result["content"][0]["text"] == f"ran {name}"
        assert calls == ["alpha", "beta", "gamma"]

    async def test_a_failed_result_is_marked_as_an_error(self) -> None:
        """``is_error`` is how the model learns to route around a failure
        rather than trusting the text as an answer."""

        @labdog_tool("boom", "fails", {"type": "object"}, "read_only")
        async def _run(ctx, args):  # pragma: no cover - not run here
            return ToolResult("unused")

        async def execute(handler: ToolHandler, args: dict) -> ToolResult:
            return ToolResult("host unreachable", ok=False)

        wrapper = _wrapper_for(_run, execute)
        result = await wrapper.handler({})
        assert result["is_error"] is True
        assert result["content"][0]["text"] == "host unreachable"

    async def test_a_successful_result_is_not(self) -> None:
        @labdog_tool("fine", "works", {"type": "object"}, "read_only")
        async def _run(ctx, args):  # pragma: no cover - not run here
            return ToolResult("unused")

        async def execute(handler: ToolHandler, args: dict) -> ToolResult:
            return ToolResult("all good")

        result = await _wrapper_for(_run, execute).handler({})
        assert result["is_error"] is False


def _wrapper_for(handler: ToolHandler, execute):
    """The single SDK tool the bridge builds for one handler."""
    from app.ai.agent_sdk.bridge import _adapt

    return _adapt(handler, execute)
