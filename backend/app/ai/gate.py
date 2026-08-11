"""Decide whether one tool call may run, before it runs.

Both runners consult this, and that is the point of it existing as its
own module. They arrive from opposite directions:

* :class:`~app.ai.agent_sdk.runner.AgentSDKRunner` is *asked* — the SDK
  calls ``can_use_tool`` for every call and honours a denial by never
  dispatching it (verified live: the denied tool's Python function was
  not entered, and the model was handed the refusal text and routed
  around it).
* :class:`~app.ai.loop.AgentLoop` owns its loop and could enforce
  autonomy anywhere, and used to do it *inside* ``tools/ssh.py`` — which
  is too late to pause a session, because by then the call is running.

One function for both means the answer to "may this run" cannot drift
between backends, which it silently would have: the SDK path gained a
gate first, and the two were already implementing the same policy twice.

Two things make this fragile in ways that do not announce themselves, so
both are pinned by tests:

**A populated ``allowed_tools`` silently disables the SDK's gate.** An
entry there auto-approves the tool *before* the callback is consulted;
the SDK warns about it (``CanUseToolShadowedWarning``) and the warning is
easy to lose in logs. LabDog leaves ``allowed_tools`` empty so every call
falls through to here. Adding to it is a change to the safety posture,
not a convenience.

**A tool's ceiling is not its verdict.** ``run_ssh_command`` is declared
``mutating`` because it *can* be, but most calls are a ``journalctl``
read. Gating on the ceiling would refuse every read in a read-only
session — the sessions LabDog runs by default — so the decision uses
:meth:`ToolHandler.verdict_for`, which re-reads the arguments.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.ai.safety import Classification, Verdict, is_allowed
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
    #: it yet, as opposed to being refused outright. The caller turns these
    #: into approval requests; everything else is a plain refusal.
    needs_approval: bool = False
    #: The full verdict, carried through so a caller that parks the
    #: session can record *why* the classifier called this a write without
    #: re-running the classifier and risking a different answer.
    verdict: Verdict | None = None


def decide(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    permitted: dict[str, ToolHandler],
    autonomy_level: str,
) -> GateDecision:
    """Whether ``tool_name`` may run with ``arguments`` in this session.

    ``tool_name`` is LabDog's own name for the tool; the SDK runner
    unqualifies the MCP name before calling.

    ``permitted`` is the session's own handler set. A tool absent from it
    is refused even if it exists in the registry: the model can ask for a
    name it was never offered, and a narrowed session is a spend and
    blast-radius bound that has to hold here too.
    """
    handler = permitted.get(tool_name)

    if handler is None:
        available = ", ".join(sorted(permitted)) or "none"
        return GateDecision(
            allowed=False,
            reason=(
                f"The {tool_name} tool is not available to this session. You may use: {available}"
            ),
            classification="denied",
        )

    verdict = handler.verdict_for(arguments)
    allowed, reason = is_allowed(verdict, autonomy_level)

    # `is_allowed` reports "not now" and "never" the same way. The
    # difference decides whether the session can park and come back, so it
    # is recovered here rather than re-derived by each caller.
    needs_approval = (
        not allowed and autonomy_level == "approval" and verdict.classification != "denied"
    )

    return GateDecision(
        allowed=allowed,
        reason=reason,
        classification=verdict.classification,
        needs_approval=needs_approval,
        verdict=verdict,
    )
