"""Reaping approval requests nobody answered.

A parked session is waiting on a person, and people go on holiday. Left
alone the session sits in ``waiting_approval`` indefinitely: no report,
no failure, no conclusion — the same shape as the stranded ``queued``
session that once sat for 28 hours while the UI showed "Working…".

The sweep therefore does two things, and the second is the one that
matters. Expiring the request is bookkeeping. *Resuming the session* is
what turns a dead run into a finished one: the model is told the change
was not approved, writes up what it did establish, and the session ends
with a report an operator can read. A run that stops without saying what
it found has wasted everything it already spent.

Runs every fifteen minutes rather than daily. The expiry window is
operator-set and can be as short as an hour, and a request that lapses at
09:05 should not stay actionable-looking until midnight.
"""

from __future__ import annotations

import asyncio
import logging

from app.tasks import celery_app

logger = logging.getLogger(__name__)

#: How often the sweep runs. Not the expiry window itself — that is
#: ``ai.approval_expiry_hours``, set per instance.
SWEEP_INTERVAL_SECONDS = 900

EXPIRY_NOTE = "Nobody decided within the time allowed, so the request expired."


@celery_app.task(
    name="app.tasks.ai_approvals.expire_stale_approvals",
    queue="default",
)
def expire_stale_approvals() -> dict:
    """Expire lapsed approval requests and let their sessions finish."""
    return asyncio.run(_expire_stale_approvals())


async def _expire_stale_approvals() -> dict:
    from datetime import UTC, datetime  # noqa: PLC0415

    from sqlalchemy import select  # noqa: PLC0415

    from app.ai.models import AIApprovalRequest, AISession  # noqa: PLC0415
    from app.db import task_session  # noqa: PLC0415

    now = datetime.now(UTC)
    resumed: list[int] = []

    async with task_session() as db:
        stale = (
            (
                await db.execute(
                    select(AIApprovalRequest).where(
                        AIApprovalRequest.status == "pending",
                        AIApprovalRequest.expires_at.is_not(None),
                        AIApprovalRequest.expires_at <= now,
                    )
                )
            )
            .scalars()
            .all()
        )

        for approval in stale:
            approval.status = "expired"
            approval.decided_at = now
            approval.decision_note = EXPIRY_NOTE
            # decided_by_user_id stays NULL: nobody decided. That is the
            # difference between "rejected" and "expired", and the audit
            # trail should not imply a person was involved.

            session = await db.get(AISession, approval.session_id)
            if session is not None and session.status == "waiting_approval":
                resumed.append(session.id)

        await db.commit()

    for session_id in resumed:
        celery_app.send_task("app.tasks.ai_task.resume_session", kwargs={"session_id": session_id})

    if stale:
        logger.info(
            "ai_approvals: expired %d request(s); resumed %d session(s)",
            len(stale),
            len(resumed),
        )
    return {"expired": len(stale), "resumed": len(resumed)}


# ---------------------------------------------------------------------------
# RedBeat registration
# ---------------------------------------------------------------------------


def _register_beat_schedules() -> None:
    from app.tasks.beat_registry import ensure_entry

    ensure_entry(
        name="expire-stale-ai-approvals",
        task="app.tasks.ai_approvals.expire_stale_approvals",
        run_every_seconds=SWEEP_INTERVAL_SECONDS,
        app=celery_app,
    )


# Registration happens from ``beat_init`` (app.tasks.beat_registry), not at
# import. Calling it here rewrote the entry's ``due_at`` in every process
# that imported this module — API included — so on a deployment that
# restarts more than once a day, a daily job never fired at all (BUG-70).
