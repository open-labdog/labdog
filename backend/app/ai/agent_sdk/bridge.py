"""Expose LabDog's tools to the Agent SDK, and nothing else.

The SDK takes custom tools as an in-process MCP server: async Python
functions it calls directly, no socket and no second process. Every
handler in :data:`app.ai.tools.TOOL_REGISTRY` maps onto one, so the model
sees the same capabilities under either runner.

**The tools LabDog does not offer matter more than the ones it does.**
Left to its defaults the SDK advertises Claude Code's own toolset —
``Bash``, ``Read``, ``Write``, ``Edit``, ``WebFetch`` — to the model,
inside LabDog's container, alongside ours. That was confirmed live: the
model ran ``Bash`` before it found the tool it had been asked to use.
``strict_mcp_config`` does not prevent it; that setting governs MCP
servers, and the built-ins are not one.

:data:`NO_BUILTIN_TOOLS` is the setting that does, and every session must
pass it. LabDog's threat model is that the model reaches a host only
through a classified, redacted, audited tool call against an allowlisted
host. A shell in the container is not that.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from app.ai.tools import ToolHandler, ToolResult

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to type checkers
    from claude_agent_sdk import McpSdkServerConfig

#: MCP server name. The SDK derives the tool names the model sees from
#: it, so changing this renames every tool.
MCP_SERVER_NAME = "labdog"

_PREFIX = f"mcp__{MCP_SERVER_NAME}__"

#: Pass as ``ClaudeAgentOptions.tools`` to withhold every built-in tool.
#: The SDK maps the empty list to ``--tools ""``; omitting the option
#: entirely means *all* built-ins, so this is not a redundant default.
NO_BUILTIN_TOOLS: list[str] = []

#: Runs one tool call and returns its result. Supplied by the runner,
#: which owns the database session, the ``AIToolCall`` row, and the SSE
#: emission — none of which belong in the adapter.
ToolExecutor = Callable[[ToolHandler, dict[str, Any]], Awaitable[ToolResult]]


def qualified_tool_name(name: str) -> str:
    """LabDog's name for a tool → the name the model calls it by."""
    return f"{_PREFIX}{name}"


def local_tool_name(name: str) -> str:
    """The model's name for a tool → LabDog's.

    Tolerates an already-local name: the permission callback receives
    qualified names, but tests and log lines carry both.
    """
    return name[len(_PREFIX) :] if name.startswith(_PREFIX) else name


def _adapt(handler: ToolHandler, execute: ToolExecutor) -> Any:
    """Wrap one handler as an SDK tool.

    A function rather than an inline loop body so each closure binds its
    own ``handler``; sharing one would leave every tool running whichever
    handler the loop happened to end on.
    """
    from claude_agent_sdk import tool as sdk_tool

    @sdk_tool(handler.spec.name, handler.spec.description, handler.spec.parameters)
    async def _run(args: dict[str, Any]) -> dict[str, Any]:
        result = await execute(handler, args or {})
        # Content is already redacted by the handlers that produce
        # sensitive output; redacting again here would double-escape
        # placeholders in transcripts.
        return {
            "content": [{"type": "text", "text": result.content}],
            "is_error": not result.ok,
        }

    return _run


def build_tool_server(
    handlers: list[ToolHandler],
    execute: ToolExecutor,
) -> McpSdkServerConfig:
    """Build the in-process MCP server carrying exactly ``handlers``.

    The list is the session's own — narrowed by ``AISession.allowed_tools``
    — so a session restricted to log queries cannot see an SSH tool to
    call. That is a stronger guarantee than refusing the call later,
    because an unseen tool costs no tokens and invites no attempt.
    """
    from claude_agent_sdk import create_sdk_mcp_server

    return create_sdk_mcp_server(
        MCP_SERVER_NAME,
        version="1.0.0",
        tools=[_adapt(handler, execute) for handler in handlers],
    )
