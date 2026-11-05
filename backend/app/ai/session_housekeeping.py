"""Archiving and deleting assistant sessions.

Sessions pile up: an alert investigation is one per alert, and nothing
prunes them. Deleting one loses more than its transcript, though:

* **The full-auto safeguards count sessions.** The cooldown, the daily
  cap and the check a few minutes after a fix are queries over
  ``alert_investigation`` sessions and their tool calls
  (:mod:`app.ai.alert_autonomy`, :mod:`app.ai.remediation`). Deleting
  today's would reset a host's cap and drop a fix's pending check.
* **The rollback goes.** It works from the session's tool calls; after a
  delete the snapshot is still on the hypervisor, named only in the audit
  row, and outside the retention sweep.
* **The alert forgets it was investigated** (``SET NULL``).

So the everyday tool is archiving: the session leaves the list and keeps
everything, safeguards included. Deleting is still there, with refusals
for the cases above. One rule set serves the session's own delete button
and the bulk delete, except that the single delete may leave snapshots
behind (it names them in the audit row, as it always has) where a bulk
delete skips such sessions instead of doing that to many at once.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import TERMINAL_SESSION_STATUSES, AISession, AIToolCall
from app.audit.logger import log_action
from app.settings_service import get_setting_typed

TERMINAL = frozenset(TERMINAL_SESSION_STATUSES)

#: The longest a safeguard looks back over a fix without reading a
#: setting: the daily cap and the check afterwards (``LOOKBACK``) both
#: cover a day. The cooldown is a setting and can be longer.
_SAFEGUARD_FLOOR = timedelta(hours=24)


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


async def safeguards_hold_until(
    db: AsyncSession, session: AISession, now: datetime
) -> datetime | None:
    """Until when the full-auto safeguards still count ``session``, if they do.

    Only a full-auto alert session that changed a host is counted. Its
    last change was no later than its end, so its end plus the longest
    window is a safe bound for every one of them.
    """
    from app.ai.alert_autonomy import REACHED_HOST

    if session.mode != "alert_investigation" or session.autonomy_level != "full_auto":
        return None
    changed = await db.scalar(
        select(AIToolCall.id)
        .where(
            AIToolCall.session_id == session.id,
            AIToolCall.classification == "mutating",
            AIToolCall.status.in_(REACHED_HOST),
        )
        .limit(1)
    )
    if changed is None:
        return None
    cooldown = int(await get_setting_typed("ai.alert_remediation_cooldown_minutes", db))
    window = max(_SAFEGUARD_FLOOR, timedelta(minutes=cooldown))
    until = _utc(session.finished_at or session.created_at) + window
    return until if until > now else None


async def snapshots_kept(db: AsyncSession, session_id: int) -> list[str]:
    """Snapshots this session took that retention has not removed yet."""
    rows = await db.execute(
        select(AIToolCall.snapshot_name).where(
            AIToolCall.session_id == session_id,
            AIToolCall.snapshot_name.is_not(None),
            AIToolCall.snapshot_pruned_at.is_(None),
        )
    )
    return list(rows.scalars().all())


async def deletion_refusal(
    db: AsyncSession, session: AISession, now: datetime, *, leave_snapshots: bool
) -> str | None:
    """Why ``session`` may not be deleted, or None when it may."""
    if session.status not in TERMINAL:
        return (
            f"This session is {session.status}. Cancel it before deleting, so the "
            "run that owns it can stop cleanly."
        )
    held = await safeguards_hold_until(db, session, now)
    if held is not None:
        return (
            "This full-auto alert fix still counts towards the remediation "
            "safeguards (the cooldown, the daily cap and the check afterwards) "
            f"until {held:%Y-%m-%d %H:%M} UTC. Archive it instead."
        )
    if not leave_snapshots:
        kept = await snapshots_kept(db, session.id)
        if kept:
            return (
                f"This session's snapshot ({', '.join(kept)}) is still on the hypervisor, "
                "and deleting the session would leave it there untracked. Delete it on "
                "its own to do that anyway, or let snapshot retention remove it first."
            )
    return None


async def delete_session(db: AsyncSession, session: AISession, *, user_id: int | None) -> None:
    """Delete ``session`` and write the audit row that outlives it.

    The caller has checked :func:`deletion_refusal` and commits.

    Spend is unaffected: the ledger in ``ai_usage_days`` is keyed by date
    and provider, not by session. Messages and tool calls go with the
    session via ON DELETE CASCADE.
    """
    # Any snapshot this session took is about to lose the row that records
    # it, which is the only thing the retention sweep works from. Naming
    # them here is what stops them becoming invisible as well as orphaned —
    # they are still on the hypervisor, and someone has to be able to find
    # out that they exist.
    orphaned = await snapshots_kept(db, session.id)

    # The transcript is about to be unrecoverable, so what it was goes in
    # the audit trail — a deleted investigation should not be invisible.
    await log_action(
        db,
        action="ai_session_deleted",
        entity_type="ai_session",
        entity_id=session.id,
        user_id=user_id,
        before_state={
            "snapshots_left_behind": orphaned,
            "mission": session.mission[:500],
            "status": session.status,
            "mode": session.mode,
            "iterations": session.iterations,
            "command_count": session.command_count,
            "target_host_ids": list(session.target_host_ids or []),
            "created_at": session.created_at.isoformat() if session.created_at else None,
        },
    )
    await db.delete(session)
