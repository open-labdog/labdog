"""The tool contract.

A handler declares what it is (``spec``), how dangerous it is
(``classification``), and how to run it. The loop owns everything else:
autonomy checks, redaction, audit rows, and caps.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.base import ToolSpec
from app.ai.safety import Classification, Verdict


@dataclass
class ToolContext:
    """What a handler is allowed to see about the session it runs in."""

    db: AsyncSession
    session_id: int
    autonomy_level: str
    #: Hosts this session may touch. Empty means the session was created
    #: without a target, and host-scoped tools refuse rather than roam.
    target_host_ids: list[int] = field(default_factory=list)
    #: Set when the session is driven by an ActionRun, for audit linkage.
    action_run_id: int | None = None
    user_id: int | None = None
    #: True only when an operator has approved *this exact call*. Lifts the
    #: autonomy gate for it and nothing else — the denylist still applies,
    #: because a command that is never allowed does not become allowable by
    #: someone clicking approve. Set exclusively by
    #: :func:`app.ai.approvals.execute_approved`.
    preapproved: bool = False


@dataclass
class ToolResult:
    """The outcome of one tool invocation.

    ``content`` goes back to the model verbatim (after redaction), so it
    should read as an answer, not as a status object.
    """

    content: str
    #: False marks this as an error the model should route around rather
    #: than a result it should trust.
    ok: bool = True
    #: The host this call touched, recorded on the AIToolCall row.
    target_host_id: int | None = None
    #: Overrides the handler's static classification when the *arguments*
    #: determine the real risk — as with run_ssh_command.
    classification: Classification | None = None
    #: Short line for the audit trail and the UI; falls back to content.
    summary: str | None = None


ToolRunner = Callable[[ToolContext, dict[str, Any]], Awaitable[ToolResult]]

#: Decides a call's real classification from its arguments alone, without
#: running it or touching the database.
Preclassifier = Callable[[dict[str, Any]], Verdict]


@dataclass(frozen=True)
class ToolHandler:
    spec: ToolSpec
    #: The worst this tool can do regardless of arguments. Handlers whose
    #: risk depends on arguments declare the ceiling here and refine it via
    #: ``ToolResult.classification``.
    classification: Classification
    run: ToolRunner
    #: Set when the ceiling alone would misjudge a call. ``run_ssh_command``
    #: is nominally ``mutating``, but most calls are a ``journalctl`` read —
    #: gating them all as writes would make a read-only session useless.
    #:
    #: Needed because the Agent SDK decides permission *before* dispatching
    #: the call, so ``ToolResult.classification`` comes too late there. Kept
    #: as data on the handler rather than a name check in the gate, so the
    #: rule travels with the tool that owns it.
    preclassify: Preclassifier | None = None

    def verdict_for(self, arguments: dict[str, Any]) -> Verdict:
        """This call's classification, refined by arguments where possible."""
        if self.preclassify is not None:
            return self.preclassify(arguments)
        return Verdict(
            self.classification,
            f"{self.spec.name} is classified {self.classification}",
            "",
        )


def tool(
    name: str,
    description: str,
    parameters: dict[str, Any],
    classification: Classification,
    preclassify: Preclassifier | None = None,
) -> Callable[[ToolRunner], ToolHandler]:
    """Decorator turning an async function into a registered handler."""

    def wrap(func: ToolRunner) -> ToolHandler:
        return ToolHandler(
            spec=ToolSpec(name=name, description=description, parameters=parameters),
            classification=classification,
            run=func,
            preclassify=preclassify,
        )

    return wrap
