"""Celery entry points for AI sessions.

Runs on the ``long_running`` queue: a session with a 900s wall-clock cap
plus provider latency comfortably exceeds the default task time limit.

Three entry points, all driving the same :class:`AgentLoop`:

* ``run_chat_session`` — an ad-hoc session from the assistant page.
* ``run_builtin_ai_task`` — one host, dispatched by the action
  orchestrator for ``_builtin.ai_task``. Participates in the per-host
  queue like any other host-writing operation.
* ``run_builtin_ai_task_group`` — one session covering a whole group,
  for ``_builtin.ai_task_group``.

The two builtin wrappers exist so that a scheduled AI check is an
ordinary action: it inherits cron dispatch, run history, cancellation,
and the host lock from machinery that already exists, rather than
growing a parallel scheduler.
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


# ---------------------------------------------------------------------------
# Built-in action wrappers
# ---------------------------------------------------------------------------


def _parse_allowed_tools(raw: str | None) -> list[str] | None:
    """Turn the comma-separated action parameter into an allowlist.

    Blank means "no restriction", which is ``None`` rather than an empty
    list — an empty list would be a session that may use nothing.
    """
    if not raw or not raw.strip():
        return None
    names = [part.strip() for part in raw.split(",") if part.strip()]
    return names or None


async def _session_for_action(
    db,
    *,
    action_run_id: int,
    parameters: dict,
    host_ids: list[int],
    title: str,
) -> AISession:
    """Create the AISession backing one builtin action run."""
    from app.ai import service
    from app.ai.loop import build_system_prompt

    autonomy = str(parameters.get("autonomy_level") or "read_only")
    provider_id = parameters.get("provider_id") or None
    # The action parameter is an int with 0 meaning "use the default",
    # because the parameter schema has no nullable int.
    if provider_id in (0, "0"):
        provider_id = None

    mission = str(parameters.get("mission") or "").strip()
    session = AISession(
        provider_id=int(provider_id) if provider_id else None,
        mode="scheduled",
        title=title,
        mission=mission,
        autonomy_level=autonomy,
        status="queued",
        target_host_ids=host_ids,
        allowed_tools=_parse_allowed_tools(parameters.get("allowed_tools")),
        action_run_id=action_run_id,
    )
    db.add(session)
    await db.flush()
    await service.append_message(
        db, session.id, role="system", content=build_system_prompt(autonomy)
    )
    await service.append_message(db, session.id, role="user", content=mission)
    return session


async def _run_action_session(session_id: int, action_run_id: int) -> tuple[bool, str]:
    """Drive a session created for an action run.

    Returns ``(succeeded, report_or_error)``. Publishes to the *action*
    run's channel as well as the session's own, so the existing run-detail
    view streams an AI check with no changes.
    """
    async with task_session() as db:
        session = await db.get(AISession, session_id)
        if session is None:
            return False, "AI session vanished before it ran"

        redis_client = _redis_client()

        async def publish(event: str, payload: dict) -> None:
            import json

            body = json.dumps({"event": event, **payload})
            redis_client.publish(SESSION_CHANNEL.format(id=session_id), body)
            redis_client.publish(f"actions.run.{action_run_id}", body)

        try:
            provider_row = await resolve_provider(db, session.provider_id)
            await assert_within_budget(db, provider_row)
        except (AIDisabledError, BudgetExceededError) as exc:
            await finish_session(db, session, status="failed", error=str(exc))
            await db.commit()
            redis_client.close()
            return False, str(exc)

        caps = await LoopCaps.from_settings(db)
        loop = AgentLoop(db, session, provider_row, caps, publish=publish)
        try:
            outcome = await loop.run()
            await db.commit()
        except Exception as exc:
            logger.exception("ai_task: action session %s failed", session_id)
            await db.rollback()
            session = await db.get(AISession, session_id)
            if session is not None:
                await finish_session(db, session, status="failed", error=str(exc)[:2000])
                await db.commit()
            redis_client.close()
            return False, str(exc)

        redis_client.close()
        return outcome.status == "succeeded", outcome.report


#: Matches the cap ``app.tasks.action_host`` puts on playbook output.
MAX_OUTPUT_BYTES = 1_000_000


async def _store_report(host_run_id: int, report: str, session_id: int | None = None) -> None:
    """Write an AI report into the ActionHostRun the UI renders.

    Failures here are logged and swallowed: losing the rendered copy of a
    report that is already safe in ``ai_sessions`` must not turn a
    successful check into a failed one.
    """
    if not report:
        return
    from sqlalchemy import select

    from app.models.action_run import ActionHostRun

    body = report[:MAX_OUTPUT_BYTES]
    if session_id is not None:
        body = f"{body}\n\n---\nAI session {session_id}"
    try:
        async with task_session() as db:
            host_run = (
                await db.execute(select(ActionHostRun).where(ActionHostRun.id == host_run_id))
            ).scalar_one_or_none()
            if host_run is not None:
                host_run.output = body
                await db.commit()
    except Exception:
        logger.exception("ai_task: could not store report on host_run %s", host_run_id)


async def _run_builtin_ai_task_async(action_run_id: int, host_run_id: int) -> None:
    from sqlalchemy import select

    from app.models.action_run import ActionRun
    from app.tasks.builtin_dispatchers import _begin_host_run, _finish_host_run

    host_id = await _begin_host_run(host_run_id)
    if host_id is None:
        return

    succeeded = False
    detail = ""
    # Bound before the try: the finally clause reads it, and session
    # creation can itself raise.
    session_id: int | None = None
    try:
        async with task_session() as db:
            run = (
                await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
            ).scalar_one_or_none()
            parameters = dict(run.parameters or {}) if run else {}
            session = await _session_for_action(
                db,
                action_run_id=action_run_id,
                parameters=parameters,
                host_ids=[host_id],
                title=f"Scheduled AI check (host {host_id})",
            )
            session_id = session.id
            await db.commit()

        succeeded, detail = await _run_action_session(session_id, action_run_id)
    except Exception as exc:
        logger.exception("ai_task: builtin ai_task failed for host_run %s", host_run_id)
        detail = str(exc)
    finally:
        # The findings are the whole point of the run, so they belong in
        # the row the run-detail view already reads. Without this the
        # report is only reachable through the AI session API, and a
        # scheduled check looks like it produced nothing.
        await _store_report(host_run_id, detail, session_id)
        await _finish_host_run(
            host_run_id,
            succeeded=succeeded,
            error=None if succeeded else (detail or "AI check failed")[:2000],
        )


@celery_app.task(
    name="app.tasks.ai_task.run_builtin_ai_task",
    queue="long_running",
    soft_time_limit=7200,
    time_limit=7500,
)
def run_builtin_ai_task(action_run_id: int, host_run_id: int) -> dict:
    """Run an AI investigation against one host."""
    asyncio.run(_run_builtin_ai_task_async(action_run_id, host_run_id))
    return {"action_run_id": action_run_id, "host_run_id": host_run_id}


async def _run_builtin_ai_task_group_async(action_run_id: int) -> None:
    """One session covering every member of the target group.

    Owns its own ``ActionHostRun`` rows and run-state transitions, the
    same contract ``app.tasks.action_group`` has with the orchestrator.
    Unlike the per-host path there is one session for the whole group, so
    every member's row takes the same outcome — the investigation either
    reached a conclusion or it did not, and attributing that per host
    would be inventing detail the run does not have.
    """
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.models.action_run import ActionHostRun, ActionRun
    from app.models.host import HostGroupMembership

    async with task_session() as db:
        run = (
            await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
        ).scalar_one_or_none()
        if run is None:
            logger.warning("ai_task: action_run %s not found", action_run_id)
            return

        member_ids = list(
            (
                await db.execute(
                    select(HostGroupMembership.c.host_id).where(
                        HostGroupMembership.c.group_id == run.group_id
                    )
                )
            )
            .scalars()
            .all()
        )
        if not member_ids:
            run.status = "failed"
            run.finished_at = datetime.now(UTC)
            await db.commit()
            return

        now = datetime.now(UTC)
        for host_id in member_ids:
            db.add(
                ActionHostRun(
                    action_run_id=action_run_id,
                    host_id=host_id,
                    status="running",
                    started_at=now,
                )
            )
        run.status = "running"
        run.started_at = run.started_at or now

        session = await _session_for_action(
            db,
            action_run_id=action_run_id,
            parameters=dict(run.parameters or {}),
            host_ids=member_ids,
            title=f"Scheduled AI check ({len(member_ids)} hosts)",
        )
        session_id = session.id
        await db.commit()

    succeeded, detail = await _run_action_session(session_id, action_run_id)

    async with task_session() as db:
        finished = datetime.now(UTC)
        host_runs = (
            (
                await db.execute(
                    select(ActionHostRun).where(ActionHostRun.action_run_id == action_run_id)
                )
            )
            .scalars()
            .all()
        )
        for host_run in host_runs:
            host_run.status = "succeeded" if succeeded else "failed"
            host_run.finished_at = finished
            # One session covered the whole group, so every member's row
            # carries the same report — the investigation reached one
            # conclusion, and splitting it per host would invent detail
            # the run does not have.
            if detail:
                host_run.output = f"{detail[:MAX_OUTPUT_BYTES]}\n\n---\nAI session {session_id}"
            if not succeeded:
                host_run.error_message = (detail or "AI check failed")[:2000]
        run = (
            await db.execute(select(ActionRun).where(ActionRun.id == action_run_id))
        ).scalar_one_or_none()
        if run is not None:
            run.status = "succeeded" if succeeded else "failed"
            run.finished_at = finished
        await db.commit()


@celery_app.task(
    name="app.tasks.ai_task.run_builtin_ai_task_group",
    queue="long_running",
    soft_time_limit=7200,
    time_limit=7500,
)
def run_builtin_ai_task_group(action_run_id: int) -> dict:
    """Run one AI investigation across a whole group."""
    asyncio.run(_run_builtin_ai_task_group_async(action_run_id))
    return {"action_run_id": action_run_id}
