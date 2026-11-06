"""Starting an assistant session from an action run.

The run page's "Investigate with AI" button. It works like the alerts
page's investigate button (:func:`app.tasks.ai_alerts.start_investigation`)
with two differences that follow from who asked: an operator pressed it
while looking at the run, so the session is an ordinary read-only chat
they can carry on, not an unattended alert session; and its mission is
built from the run's own rows (:mod:`app.ai.run_mission`).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import AIProvider, AISession
from app.models.action_run import ActionHostRun, ActionRun
from app.models.host import Host

#: Run statuses an investigation can start from. A live run's log is not
#: finished, and the host is still the run's.
TERMINAL_RUN_STATUSES = frozenset({"succeeded", "partial", "failed", "cancelled"})

#: An earlier investigation in one of these is not reopened: the operator
#: pressing the button again after one failed or was stopped wants another.
_ABANDONED = frozenset({"failed", "cancelled"})


class RunInvestigationRefused(Exception):
    """The run cannot be investigated; the message says why."""


async def investigation_scope(
    db: AsyncSession, run: ActionRun, host_run: ActionHostRun | None
) -> tuple[list[ActionHostRun], list[int]]:
    """The run's host rows, and the ids of the hosts the session may touch.

    Only hosts that still exist: a deleted host leaves its transcript but
    nothing to connect to, and a session with no hosts would be refused by
    every tool that reaches one.
    """
    if run.status not in TERMINAL_RUN_STATUSES:
        raise RunInvestigationRefused(
            f"This run is {run.status}. Investigate it once it has finished."
        )
    host_runs = list(
        (
            await db.execute(
                select(ActionHostRun)
                .where(ActionHostRun.action_run_id == run.id)
                .order_by(ActionHostRun.id)
            )
        )
        .scalars()
        .all()
    )
    wanted = [host_run] if host_run is not None else host_runs
    ids = sorted({hr.host_id for hr in wanted if hr.host_id is not None})
    if ids:
        existing = await db.execute(select(Host.id).where(Host.id.in_(ids)))
        ids = sorted(existing.scalars().all())
    if not ids:
        raise RunInvestigationRefused(
            "The hosts this run targeted have been deleted, so there is nothing "
            "for the assistant to check."
        )
    return host_runs, ids


async def existing_investigation(
    db: AsyncSession, run_id: int, host_ids: list[int]
) -> AISession | None:
    """The latest investigation of this run with the same hosts in scope."""
    sessions = (
        (
            await db.execute(
                select(AISession)
                .where(
                    AISession.investigates_action_run_id == run_id,
                    AISession.status.not_in(_ABANDONED),
                )
                .order_by(AISession.created_at.desc(), AISession.id.desc())
            )
        )
        .scalars()
        .all()
    )
    for session in sessions:
        if sorted(session.target_host_ids or []) == host_ids:
            return session
    return None


async def start_run_investigation(
    db: AsyncSession,
    run: ActionRun,
    provider: AIProvider,
    *,
    host_runs: list[ActionHostRun],
    host_ids: list[int],
    host_run: ActionHostRun | None = None,
    user=None,  # noqa: ANN001 - User
) -> AISession:
    """Create the session. Flushes and does not commit; the caller commits
    and only then dispatches, so a worker cannot read a session that has
    not landed."""
    from app.actions.registry import ACTION_REGISTRY
    from app.ai import service
    from app.ai.loop import build_system_prompt
    from app.ai.run_mission import build_mission
    from app.audit.logger import log_action

    defn = ACTION_REGISTRY.get(run.action_key)
    mission = build_mission(
        run,
        host_runs,
        only=host_run,
        action_name=defn.name if defn else None,
        action_description=defn.description if defn else None,
    )
    title = f"Run #{run.id}: {run.action_key}"
    if host_run is not None:
        title += f" on {host_run.hostname}"
    session = AISession(
        provider_id=provider.id,
        mode="chat",
        title=title[:200],
        mission=mission,
        autonomy_level="read_only",
        status="queued",
        target_host_ids=host_ids,
        investigates_action_run_id=run.id,
        created_by_user_id=user.id if user else None,
    )
    db.add(session)
    await db.flush()
    await service.append_message(
        db, session.id, role="system", content=build_system_prompt("read_only")
    )
    await service.append_message(db, session.id, role="user", content=mission)
    await log_action(
        db,
        action="ai_session_created",
        entity_type="ai_session",
        entity_id=session.id,
        user_id=user.id if user else None,
        after_state={
            "autonomy_level": session.autonomy_level,
            "target_host_ids": session.target_host_ids,
            "provider": provider.name,
            "investigates_action_run_id": run.id,
            "host_run_id": host_run.id if host_run is not None else None,
        },
    )
    return session
