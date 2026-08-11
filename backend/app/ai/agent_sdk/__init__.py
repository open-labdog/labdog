"""Agentic sessions driven by the Claude Agent SDK.

A Claude subscription authenticates one thing: Claude Code's own harness.
There is no way to point the Messages API at it. So running agentic work
on a subscription rather than metered credit means driving Claude Code as
a subprocess — and the supported way to do that is Anthropic's Claude
Agent SDK, which is exactly the bidirectional stream-json transport this
project was otherwise going to hand-roll.

The consequence for LabDog's architecture is that **the SDK owns the
loop**. This backend therefore cannot be an
:class:`~app.ai.providers.base.LLMProvider`: it is never asked for one
turn at a time, so it has no ``stream_turn`` to implement. It enters one
level up instead, as a second session runner behind the same
``AISession`` / ``AIMessage`` / ``AIToolCall`` rows and the same SSE
events, sharing the tool registry, the command classifier, and the
redactor with :class:`~app.ai.loop.AgentLoop`.

Safety does not depend on which runner drives the session, because it
lives inside the tools: classification, redaction, the target-host
allowlist, and the audit rows all execute in LabDog's own code either
way. The model reaches a host only through a tool we wrote.

The SDK is an optional dependency — its wheel carries a large
platform-specific Claude Code binary that has no business in the
``.deb``/``.rpm`` artefacts — so nothing here may be imported at module
scope from always-loaded code. Call :func:`sdk_available` first.
"""

from __future__ import annotations

from importlib.util import find_spec

from app.ai.agent_sdk.bridge import (
    MCP_SERVER_NAME,
    build_tool_server,
    local_tool_name,
    qualified_tool_name,
)
from app.ai.gate import GateDecision, decide

#: Why an SDK-backed provider cannot run, phrased for an operator who is
#: looking at a provider that tests red.
UNAVAILABLE_MESSAGE = (
    "The Claude Agent SDK is not installed. The official container image "
    "ships it; package installs do not, so add it with "
    "`pip install 'labdog-backend[agent]'` on this host."
)


def sdk_available() -> bool:
    """Whether ``claude_agent_sdk`` can be imported.

    Uses :func:`find_spec` rather than a ``try: import`` so that merely
    asking the question does not pull a large module into memory in the
    API process, which never runs a session itself.
    """
    return find_spec("claude_agent_sdk") is not None


__all__ = [
    "MCP_SERVER_NAME",
    "UNAVAILABLE_MESSAGE",
    "GateDecision",
    "build_tool_server",
    "decide",
    "local_tool_name",
    "qualified_tool_name",
    "sdk_available",
]
