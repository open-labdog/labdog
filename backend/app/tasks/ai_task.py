"""Celery entry points for AI sessions.

Runs on the ``long_running`` queue: a session with a 900s wall-clock cap
plus provider latency comfortably exceeds the default task time limit.

Phase 1 runs a chat session start to finish. Phase 2 adds the
``_builtin.ai_task`` per-host wrapper, and phase 3 adds ``resume_session``
for continuing after an approval.
"""

from __future__ import annotations

import asyncio
import logging

from app.ai.loop import AgentLoop, LoopCaps, redis_publisher
from app.ai.models import AISession
from app.ai.service import (
    AIDisabledError,
    BudgetExceededError,
    assert_within_budget,
    finish_session,
    resolve_provider,
)
from app.db import task_session
from app.tasks import celery_app

logger = logging.getLogger(__name__)

SESSION_CHANNEL = "ai.session.{id}"


def _redis_client():
    import redis

    from app.config import settings

    return redis.from_url(settings.redis.url)


async def _run_session_async(session_id: int) -> dict:
    async with task_session() as db:
        session = await db.get(AISession, session_id)
        if session is None:
            logger.warning("ai_task: session %s no longer exists", session_id)
            return {"session_id": session_id, "status": "missing"}

        # An operator may have cancelled between dispatch and pickup.
        if session.status == "cancelled":
            return {"session_id": session_id, "status": "cancelled"}

        redis_client = _redis_client()
        channel = SESSION_CHANNEL.format(id=session_id)
        publish = redis_publisher(redis_client, channel)

        try:
            provider_row = await resolve_provider(db, session.provider_id)
            await assert_within_budget(db, provider_row)
        except (AIDisabledError, BudgetExceededError) as exc:
            await finish_session(db, session, status="failed", error=str(exc))
            await db.commit()
            await publish("error", {"message": str(exc)})
            await publish("status", {"status": "failed"})
            redis_client.close()
            return {"session_id": session_id, "status": "failed", "error": str(exc)}

        caps = await LoopCaps.from_settings(db)
        loop = AgentLoop(db, session, provider_row, caps, publish=publish)

        try:
            outcome = await loop.run()
            await db.commit()
        except Exception as exc:
            logger.exception("ai_task: session %s failed", session_id)
            await db.rollback()
            # Re-fetch: the failed transaction rolled back the in-memory row.
            session = await db.get(AISession, session_id)
            if session is not None:
                await finish_session(db, session, status="failed", error=str(exc)[:2000])
                await db.commit()
            await publish("error", {"message": str(exc)})
            await publish("status", {"status": "failed"})
            redis_client.close()
            return {"session_id": session_id, "status": "failed", "error": str(exc)}

        redis_client.close()
        return {
            "session_id": session_id,
            "status": outcome.status,
            "iterations": outcome.iterations,
            "stopped_by": outcome.stopped_by,
        }


@celery_app.task(
    name="app.tasks.ai_task.run_chat_session",
    queue="long_running",
    # Comfortably above the ai.wall_clock_seconds ceiling so the loop's own
    # cap is what stops a run, not a hard kill that would orphan the session
    # in "running".
    soft_time_limit=7200,
    time_limit=7500,
)
def run_chat_session(session_id: int) -> dict:
    """Drive one chat session to completion."""
    return asyncio.run(_run_session_async(session_id))
