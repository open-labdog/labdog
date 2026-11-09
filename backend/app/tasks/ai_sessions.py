"""Archiving old assistant sessions on a schedule.

``ai.session_archive_after_days`` (0, the default, = never) archives every
finished session that ended longer ago than that. Archiving rather than
deleting, because an archived session keeps everything a delete would
lose — its rollback, its alert's link, and its place in the full-auto
safeguards' counts (:mod:`app.ai.session_housekeeping`) — and an
operator can still bulk-delete from the archive by hand.

Tasks:
    archive_old_sessions -- daily; archives finished sessions past the window
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.db import with_task_engine
from app.tasks import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.ai_sessions.archive_old_sessions", queue="default")
def archive_old_sessions() -> dict:
    """Archive finished sessions older than ``ai.session_archive_after_days``."""
    return asyncio.run(with_task_engine(_archive_old_sessions()))


async def _archive_old_sessions(now: datetime | None = None) -> dict:
    from sqlalchemy import func, update  # noqa: PLC0415

    from app.ai.models import TERMINAL_SESSION_STATUSES, AISession  # noqa: PLC0415
    from app.audit.logger import log_action  # noqa: PLC0415
    from app.db import task_session  # noqa: PLC0415
    from app.settings_service import get_setting_typed  # noqa: PLC0415

    now = now or datetime.now(UTC)
    async with task_session() as db:
        days = int(await get_setting_typed("ai.session_archive_after_days", db))
        if days <= 0:
            return {"archived": 0, "days": days}
        ids = sorted(
            (
                await db.execute(
                    update(AISession)
                    .where(
                        AISession.archived_at.is_(None),
                        AISession.status.in_(TERMINAL_SESSION_STATUSES),
                        func.coalesce(AISession.finished_at, AISession.created_at)
                        < now - timedelta(days=days),
                    )
                    .values(archived_at=now)
                    .returning(AISession.id)
                )
            )
            .scalars()
            .all()
        )
        if ids:
            await log_action(
                db,
                action="ai_sessions_archived",
                entity_type="ai_session",
                after_state={"session_ids": ids, "older_than_days": days},
            )
        await db.commit()
    if ids:
        logger.info("ai_sessions: archived %d session(s) older than %d days", len(ids), days)
    return {"archived": len(ids), "days": days}


# ---------------------------------------------------------------------------
# RedBeat registration
# ---------------------------------------------------------------------------


def _register_beat_schedules() -> None:
    from app.tasks.beat_registry import ensure_entry  # noqa: PLC0415

    ensure_entry(
        name="archive-old-ai-sessions",
        task="app.tasks.ai_sessions.archive_old_sessions",
        run_every_seconds=86400,
        app=celery_app,
    )


# Registration happens from ``beat_init`` (app.tasks.beat_registry), not at
# import — see the note at the foot of ``audit_retention`` (BUG-70).
