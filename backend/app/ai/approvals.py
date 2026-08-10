"""Pausing a session on a change, and picking it up again.

An approval is a pause of unbounded length in the middle of an agent run.
Nothing here holds a worker open across it: :func:`park` writes the rows
and the runner returns, and :func:`resume_prompt` is what a *later* task
uses to carry on. The session's conversational state is not duplicated
into the approval row — it already lives in the transcript (for
``AgentLoop``) or in the CLI's own session file referenced by
``AISession.resume_state`` (for ``AgentSDKRunner``).

Two rules decide the shape of everything below.

**What the operator approved is what runs.** The arguments are frozen on
the request row and executed from there. Re-offering the tool and hoping
the model reissues an identical call would make the approval advisory —
the model could substitute a different command after the fact and it
would carry a human's authorisation.

**A grant is one shot.** :func:`execute_approved` runs the stored call
once and the request leaves ``pending``. If the model asks for the same
thing again it is gated again, because a second execution is a second
change to the host.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import AIApprovalRequest, AISession, AIToolCall
from app.ai.safety import Verdict
from app.ai.snapshots import SnapshotFailed, snapshot_before_change
from app.ai.tools import TOOL_REGISTRY, ToolContext, ToolResult
from app.settings_service import get_setting_typed

logger = logging.getLogger(__name__)

#: Returned to the model in place of the tool result when a call parks.
#: The transcript needs *something* here — both wire formats reject a
#: tool call with no matching result — and this says plainly that the
#: command has not run.
PARKED_RESULT = (
    "This command needs the operator's approval and has not run. "
    "The session is paused until they decide. Do not retry it and do "
    "not look for another way to make the same change."
)

#: For the other calls in the same turn, which are dropped when one of
#: them parks the session.
SKIPPED_RESULT = (
    "Not run: the session paused on an earlier command in this turn "
    "that needs the operator's approval."
)


def command_preview(tool_name: str, arguments: dict) -> str:
    """The one line an operator decides on.

    Stored on the request rather than derived at render time. What is
    approved has to be exactly what runs, and a preview computed twice is
    a preview that can differ twice.
    """
    if tool_name == "run_ssh_command":
        return str(arguments.get("command") or "").strip()
    parts = ", ".join(f"{k}={v!r}" for k, v in sorted(arguments.items()) if k != "purpose")
    return f"{tool_name}({parts})"


async def park(
    db: AsyncSession,
    session: AISession,
    *,
    tool_name: str,
    arguments: dict,
    verdict: Verdict,
    record: AIToolCall | None = None,
) -> AIApprovalRequest:
    """Record a call awaiting a decision and mark the session parked.

    ``record`` is the caller's own ``AIToolCall`` row when it already has
    one. Both runners write that row before they know the verdict, and
    creating a second one here would show the operator the same proposed
    command twice.
    """
    hours = int(await get_setting_typed("ai.approval_expiry_hours", db))
    host_id = arguments.get("host_id")

    approval = AIApprovalRequest(
        session_id=session.id,
        tool_name=tool_name,
        arguments=dict(arguments),
        target_host_id=host_id if isinstance(host_id, int) else None,
        summary=str(arguments.get("purpose") or "").strip(),
        command_preview=command_preview(tool_name, arguments),
        classification=verdict.classification,
        reason=verdict.reason,
        status="pending",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=hours),
    )
    db.add(approval)
    await db.flush()

    if record is None:
        record = AIToolCall(
            session_id=session.id,
            tool_name=tool_name,
            arguments=dict(arguments),
            classification=verdict.classification,
            started_at=datetime.now(UTC),
        )
        db.add(record)
    # Left "proposed" rather than "blocked": the call has not been
    # refused, only deferred, and the two look identical in the UI if the
    # status does not distinguish them.
    record.status = "proposed"
    record.approval_id = approval.id
    record.target_host_id = approval.target_host_id
    record.classification = verdict.classification
    record.result_summary = f"Awaiting operator approval: {verdict.reason}"[:1000]

    session.status = "waiting_approval"
    await db.flush()
    return approval


async def pending_for_session(db: AsyncSession, session_id: int) -> AIApprovalRequest | None:
    result = await db.execute(
        select(AIApprovalRequest)
        .where(
            AIApprovalRequest.session_id == session_id,
            AIApprovalRequest.status == "pending",
        )
        .order_by(AIApprovalRequest.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def latest_for_session(db: AsyncSession, session_id: int) -> AIApprovalRequest | None:
    """The most recent request, decided or not.

    A resuming task needs the one that parked the session, which by then
    is no longer pending.
    """
    result = await db.execute(
        select(AIApprovalRequest)
        .where(AIApprovalRequest.session_id == session_id)
        .order_by(AIApprovalRequest.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def execute_approved(
    db: AsyncSession,
    session: AISession,
    approval: AIApprovalRequest,
) -> ToolResult:
    """Run the call an operator approved, and record it as a real call.

    ``preapproved`` on the context is what lets the tool's own autonomy
    gate through. It is deliberately narrower than passing ``full_auto``:
    the denylist still applies, because a command that is never allowed
    is not made allowable by someone clicking approve.
    """
    handler = TOOL_REGISTRY.get(approval.tool_name)
    if handler is None:
        return ToolResult(f"There is no tool called {approval.tool_name!r}.", ok=False)

    arguments = dict(approval.arguments or {})
    record = (
        await db.execute(
            select(AIToolCall)
            .where(AIToolCall.approval_id == approval.id)
            .order_by(AIToolCall.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    snapshot_name: str | None = None
    if approval.target_host_id is not None:
        try:
            snapshot_name = await snapshot_before_change(
                db,
                host_id=approval.target_host_id,
                session_id=session.id,
                label=approval.command_preview,
            )
        except SnapshotFailed as exc:
            # Refusing here rather than proceeding without a net. The
            # operator approved a change they could expect to be able to
            # undo; running it anyway would silently make it a different,
            # riskier change than the one they agreed to.
            if record is not None:
                record.status = "error"
                record.result_summary = str(exc)[:1000]
                record.finished_at = datetime.now(UTC)
            await db.flush()
            return ToolResult(str(exc), ok=False, target_host_id=approval.target_host_id)

    ctx = ToolContext(
        db=db,
        session_id=session.id,
        autonomy_level=session.autonomy_level,
        target_host_ids=list(session.target_host_ids or []),
        action_run_id=session.action_run_id,
        user_id=session.created_by_user_id,
        preapproved=True,
    )

    try:
        result = await handler.run(ctx, arguments)
    except Exception as exc:  # noqa: BLE001 - reported to the model, not raised
        logger.exception("ai session %s: approved call %s failed", session.id, approval.tool_name)
        result = ToolResult(f"The {approval.tool_name} tool failed: {exc}", ok=False)

    if record is not None:
        record.status = "executed" if result.ok else "error"
        record.classification = result.classification or record.classification
        record.result_summary = (result.summary or result.content)[:1000]
        record.result_chars = len(result.content)
        record.snapshot_name = snapshot_name
        record.finished_at = datetime.now(UTC)
    if approval.tool_name == "run_ssh_command":
        session.command_count += 1
    await db.flush()
    return result


def resume_prompt(approval: AIApprovalRequest, result: ToolResult | None) -> str:
    """What the model is told when the session picks up again.

    Phrased as narration because that is what it is: LabDog ran the
    command, not the model. Pretending otherwise — feeding it back as a
    tool result the model appears to have obtained — would misrepresent
    who acted, in the one place where who acted is the whole point.
    """
    where = f" on host {approval.target_host_id}" if approval.target_host_id else ""
    asked = f"You asked to run:\n\n    {approval.command_preview}\n{where}".rstrip()

    if approval.status == "approved" and result is not None:
        outcome = (
            "LabDog ran it on their behalf, so you do not need to run it "
            "again. Its output was:\n\n"
            f"{result.content}\n\n"
            "Continue from here: verify the change had the effect you "
            "intended, and say so plainly if it did not."
        )
        return f"{asked}\n\nThe operator approved this. {outcome}"

    if approval.status == "expired":
        return (
            f"{asked}\n\nNobody decided within the time allowed, so this was not run "
            f"and the request has expired. Do not retry it and do not look for "
            f"another way to make the same change. Report what you found, say "
            f"plainly what still needs doing, and stop."
        )

    note = (approval.decision_note or "").strip()
    because = f" They said: {note}" if note else ""
    return (
        f"{asked}\n\nThe operator rejected this, so it was not run.{because} Do not "
        f"retry it and do not look for another way to make the same change. "
        f"Continue with what you can verify without it, and report what still "
        f"needs doing."
    )
