"""Decide whether one tool call may run, before it runs.

The SDK consults ``can_use_tool`` for every call and honours a denial by
never dispatching it — verified live: the denied tool's Python function
was not entered, and the model was handed the refusal text and routed
around it. That makes this the enforcement point for autonomy level under
the SDK runner, and it sits *earlier* than the equivalent check in
:class:`~app.ai.loop.AgentLoop`, which can only classify a call as it
executes it.

Two things make it fragile in ways that do not announce themselves, so
both are pinned by tests:

**A populated ``allowed_tools`` silently disables this gate.** An entry
there auto-approves the tool *before* the callback is consulted; the SDK
warns about it (``CanUseToolShadowedWarning``) and the warning is easy to
lose in logs. LabDog leaves ``allowed_tools`` empty so every call falls
through to here. Adding to it is a change to the safety posture, not a
convenience.

**A tool's ceiling is not its verdict.** ``run_ssh_command`` is declared
``mutating`` because it *can* be, but most calls are a ``journalctl``
read. Gating on the ceiling would refuse every read in a read-only
session — the sessions LabDog runs by default — so the decision uses
:meth:`ToolHandler.verdict_for`, which re-reads the arguments.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.ai.agent_sdk.bridge import local_tool_name
from app.ai.safety import Classification, is_allowed
from app.ai.tools import ToolHandler


@dataclass(frozen=True)
class GateDecision:
    """The verdict on one proposed call, and why."""

    allowed: bool
    #: Shown to the model on refusal, so it reads as a policy answer
    #: rather than a malfunction it should retry around.
    reason: str
    classification: Classification
    #: Set when the call was refused only because a human has not approved
    #: it yet, as opposed to being refused outright. Phase 3 turns these
    #: into approval requests; until then they are plain refusals.
    needs_approval: bool = False


def decide(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    permitted: dict[str, ToolHandler],
    autonomy_level: str,
) -> GateDecision:
    """Whether ``tool_name`` may run with ``arguments`` in this session.

    ``permitted`` is the session's own handler set, keyed by LabDog name.
    A tool absent from it is refused even if it exists in the registry:
    the model can ask for a name it was never offered, and a narrowed
    session is a spend and blast-radius bound that has to hold here too.
    """
    name = local_tool_name(tool_name)
    handler = permitted.get(name)

    if handler is None:
        available = ", ".join(sorted(permitted)) or "none"
        return GateDecision(
            allowed=False,
            reason=(f"The {name} tool is not available to this session. You may use: {available}"),
            classification="denied",
        )

    verdict = handler.verdict_for(arguments)
    allowed, reason = is_allowed(verdict, autonomy_level)

    # `is_allowed` reports "not now" and "never" the same way. The
    # difference decides whether Phase 3 can park the session and come
    # back, so it is recovered here rather than re-derived by the caller.
    needs_approval = (
        not allowed and autonomy_level == "approval" and verdict.classification != "denied"
    )

    return GateDecision(
        allowed=allowed,
        reason=reason,
        classification=verdict.classification,
        needs_approval=needs_approval,
    )
