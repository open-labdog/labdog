"""Capabilities the model may invoke.

Each tool is a :class:`ToolHandler`: a JSON-Schema spec the provider
advertises, a static classification, and an async ``run``. The loop never
special-cases a tool by name — it looks up the handler, checks the
classification against the session's autonomy level, executes, redacts,
and records an ``AIToolCall`` row.

A session may narrow the set via ``AISession.allowed_tools``. That single
control bounds both spend and blast radius: a nightly log sweep limited
to ``query_loki`` cannot open an SSH session at all, and cannot spend
what an unbounded ``journalctl`` would.
"""

from app.ai.tools.base import ToolContext, ToolHandler, ToolResult
from app.ai.tools.hosts import GET_HOST_FACTS, LIST_HOSTS
from app.ai.tools.logs import QUERY_LOKI
from app.ai.tools.metrics import QUERY_MIMIR, QUERY_MIMIR_RANGE
from app.ai.tools.ssh import RUN_SSH_COMMAND

#: Every registered tool, keyed by the name the model calls.
TOOL_REGISTRY: dict[str, ToolHandler] = {
    tool.spec.name: tool
    for tool in (
        LIST_HOSTS,
        GET_HOST_FACTS,
        RUN_SSH_COMMAND,
        QUERY_MIMIR,
        QUERY_MIMIR_RANGE,
        QUERY_LOKI,
    )
}

#: Always available regardless of the allowlist. Without an inventory the
#: model cannot discover the host ids every other tool takes, so excluding
#: these would produce a session that can do nothing rather than a
#: narrower one.
ALWAYS_ALLOWED = frozenset({"list_hosts", "get_host_facts"})

#: Session modes that get no tools at all, allowlist or not.
#:
#: A verify session is handed its evidence in the prompt and answers one
#: question about it, so there is nothing for a tool to fetch. The reason
#: to enforce that rather than merely expect it is what a verify verdict
#: does: a FAIL can restore a pre-change snapshot, discarding the change
#: and everything since. A session deciding that must not also be opening
#: SSH connections to the host it is deciding about.
#:
#: This cannot be expressed as ``allowed_tools=[]`` — an empty allowlist
#: still yields ``ALWAYS_ALLOWED`` by design, which is right for a
#: narrowed investigation and wrong here.
TOOLLESS_MODES = frozenset({"verify"})


def tools_for_session(
    autonomy_level: str, allowed_tools: list[str] | None = None, *, mode: str = "chat"
) -> list[ToolHandler]:
    """The handlers a session may see.

    Autonomy level does not filter this list. ``run_ssh_command`` is
    offered even to a read-only session, because what the level gates is
    the classification of an individual *command* — withholding the tool
    would just leave the model unable to read anything.

    ``allowed_tools`` does filter it. ``None`` means no restriction.

    ``mode`` can remove everything: see :data:`TOOLLESS_MODES`.
    """
    if mode in TOOLLESS_MODES:
        return []
    if allowed_tools is None:
        return list(TOOL_REGISTRY.values())
    permitted = set(allowed_tools) | ALWAYS_ALLOWED
    return [handler for name, handler in TOOL_REGISTRY.items() if name in permitted]


def unknown_tool_names(allowed_tools: list[str] | None) -> list[str]:
    """Names in an allowlist that match no registered tool.

    Used to reject a typo at configuration time rather than silently
    handing the session a smaller toolset than the operator intended.
    """
    if not allowed_tools:
        return []
    return sorted(set(allowed_tools) - set(TOOL_REGISTRY))


__all__ = [
    "ALWAYS_ALLOWED",
    "TOOLLESS_MODES",
    "TOOL_REGISTRY",
    "ToolContext",
    "ToolHandler",
    "ToolResult",
    "tools_for_session",
    "unknown_tool_names",
]
