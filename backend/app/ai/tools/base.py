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
from app.ai.safety import Classification


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


@dataclass(frozen=True)
class ToolHandler:
    spec: ToolSpec
    #: The worst this tool can do regardless of arguments. Handlers whose
    #: risk depends on arguments declare the ceiling here and refine it via
    #: ``ToolResult.classification``.
    classification: Classification
    run: ToolRunner


def tool(
    name: str,
    description: str,
    parameters: dict[str, Any],
    classification: Classification,
) -> Callable[[ToolRunner], ToolHandler]:
    """Decorator turning an async function into a registered handler."""

    def wrap(func: ToolRunner) -> ToolHandler:
        return ToolHandler(
            spec=ToolSpec(name=name, description=description, parameters=parameters),
            classification=classification,
            run=func,
        )

    return wrap
