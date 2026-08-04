"""Capabilities the model may invoke.

Each tool is a :class:`ToolHandler`: a JSON-Schema spec the provider
advertises, a static classification, and an async ``run``. The loop never
special-cases a tool by name — it looks up the handler, checks the
classification against the session's autonomy level, executes, redacts,
and records an ``AIToolCall`` row.

Phase 1 registers the read-only set. Later phases add mutating tools
(``propose_action``) and the approval gate.
"""

from app.ai.tools.base import ToolContext, ToolHandler, ToolResult
from app.ai.tools.hosts import GET_HOST_FACTS, LIST_HOSTS
from app.ai.tools.metrics import QUERY_MIMIR
from app.ai.tools.ssh import RUN_SSH_COMMAND

#: Every tool available in phase 1, keyed by the name the model calls.
TOOL_REGISTRY: dict[str, ToolHandler] = {
    tool.spec.name: tool
    for tool in (LIST_HOSTS, GET_HOST_FACTS, RUN_SSH_COMMAND, QUERY_MIMIR)
}


def tools_for_session(autonomy_level: str) -> list[ToolHandler]:
    """The handlers a session may see.

    Every tool is offered at every level — ``run_ssh_command`` is offered
    even to a read-only session, because the classification of an
    individual *command* is what the level gates, not the tool itself.
    Withholding the tool would just make the model unable to read.
    """
    return list(TOOL_REGISTRY.values())


__all__ = [
    "TOOL_REGISTRY",
    "ToolContext",
    "ToolHandler",
    "ToolResult",
    "tools_for_session",
]
