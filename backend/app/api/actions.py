from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy import case, exists, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.registry import ACTION_REGISTRY, reload_registry_async
from app.actions.run_target import describe_target
from app.actions.validation import DRY_RUN_PARAM, build_param_model
from app.ai.run_log import FAILURE_WINDOW_SQL, failure_reason
from app.auth.users import current_active_user
from app.db import get_db
from app.models.action_run import ActionHostRun, ActionRun
from app.models.host import Host
from app.models.host_group import HostGroup
from app.models.sync_job import JobStatus, SyncJob
from app.models.user import User
from app.schemas.actions import (
    ActionDefinitionOut,
    ActionHostRunOut,
    ActionParameterOut,
    ActionRunOut,
    HostRunEntry,
    RunCreateBody,
    RunHostFailure,
    RunInvestigateBody,
    RunInvestigationOut,
    RunInvestigationRef,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/actions", tags=["actions"])


def _definition_to_out(defn) -> ActionDefinitionOut:
    """Convert an ActionDefinition dataclass to ActionDefinitionOut."""
    params = [
        ActionParameterOut(
            key=p.key,
            label=p.label,
            type=p.type,
            default=p.default,
            required=p.required,
            choices=list(p.choices) if p.choices is not None else None,
            help_text=p.help_text,
        )
        for p in defn.parameters
    ]
    return ActionDefinitionOut(
        key=defn.key,
        name=defn.name,
        description=defn.description,
        icon=defn.icon,
        version=defn.version,
        estimated_duration=defn.estimated_duration,
        destructive=defn.destructive,
        supports_group=defn.supports_group,
        supports_host=defn.supports_host,
        supports_fleet=defn.supports_fleet,
        parameters=params,
        pack_name=defn.pack_name,
        winning_pack_id=defn.winning_pack_id,
        unresolved=defn.is_unresolved,
        overridden_from=list(defn.overridden_from),
        post_run_sync=list(defn.post_run_sync),
        post_run_register={
            module: [dict(item) for item in items]
            for module, items in defn.post_run_register.items()
        },
        metrics_backend=defn.metrics_backend,
    )


# ---------------------------------------------------------------------------
# GET /actions — List catalog
# ---------------------------------------------------------------------------


@router.get("/", response_model=list[ActionDefinitionOut])
async def list_actions(
    _: User = Depends(current_active_user),
):
    """Return all registered actions from the catalog."""
    return [_definition_to_out(defn) for defn in ACTION_REGISTRY.values()]


@router.post("/refresh")
async def refresh_actions(
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Sync every enabled pack from its source, then rebuild ACTION_REGISTRY.

    The same as the API does in the background at startup. Returns a
    summary so admins can confirm which packs contributed.

    This used to call a synchronous rebuild that needed psycopg2, which
    LabDog does not install. It fell back to the bundled pack alone and
    installed that, so every call dropped every DB pack's actions from
    the API until something rebuilt the registry again (BUG-96).
    """
    from app.packs.service import sync_enabled_packs  # noqa: PLC0415

    await sync_enabled_packs(db)
    registry = await reload_registry_async(db)
    packs = sorted({defn.pack_name for defn in registry.values()})
    return {
        "action_count": len(registry),
        "packs": packs,
    }


# ---------------------------------------------------------------------------
# POST /actions/runs — Create run + dispatch
# ---------------------------------------------------------------------------


@router.post("/runs", response_model=ActionRunOut, status_code=201)
async def create_run(
    body: RunCreateBody,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Create an action run and dispatch the Celery task."""
    # Exactly one of host_id / group_id must be set
    if (body.host_id is None) == (body.group_id is None):
        raise HTTPException(
            status_code=422,
            detail="Exactly one of host_id or group_id must be provided.",
        )

    # action_key must exist in registry
    action = ACTION_REGISTRY.get(body.action_key)
    if action is None:
        raise HTTPException(status_code=400, detail="Unknown action")

    # Reject unresolved contested keys before doing anything else.
    # ``is_unresolved`` means "multiple packs declare this key and the
    # operator hasn't pinned a winner" — there is no playbook to
    # dispatch and no global ordering to fall back on.
    if action.is_unresolved:
        raise HTTPException(
            status_code=409,
            detail={
                "kind": "action_unresolved",
                "action_key": action.key,
                "message": (
                    f"Action {action.key!r} has multiple candidate packs and "
                    "no winner pinned. Visit /action-packs to choose one."
                ),
            },
        )

    # Pack-supplied actions need a playbook on disk. Built-in
    # pseudo-actions have ``playbook_path=None`` — they're dispatched
    # via dedicated per-host wrappers in builtin_dispatchers.py and
    # don't need the file-existence check.
    if action.playbook_path is not None and not action.playbook_path.is_file():
        raise HTTPException(status_code=400, detail="Playbook file not found")

    # Check scope support. ``supports_host=False`` actions are
    # group-only — they're dispatched as a single ansible-runner
    # invocation against a flat all-hosts inventory by
    # ``app.tasks.action_group``. Reject host-target submissions up
    # front with a 400 + clear message rather than letting them fall
    # through to a less helpful error later.
    if body.host_id is not None and not action.supports_host:
        raise HTTPException(
            status_code=400,
            detail="This action can only target a group.",
        )
    if body.group_id is not None and not action.supports_group:
        raise HTTPException(
            status_code=422,
            detail="This action does not support group-scoped runs.",
        )

    # Validate parameters against the action's manifest schema. Catches
    # missing-required, type-mismatch, and unknown-key errors uniformly.
    #
    # ``DRY_RUN_PARAM`` is stripped first, and re-added below from
    # ``body.dry_run``. It is not an action parameter — it is how the flag
    # reaches the Celery task, which receives only ``ActionRun.parameters``
    # — so validating it against the manifest rejected every dry run with
    # "Extra inputs are not permitted". Accepting it from the client here
    # would leave the operator able to set it without asking for a dry run,
    # so it is discarded rather than honoured (BUG-53).
    submitted = {k: v for k, v in (body.parameters or {}).items() if k != DRY_RUN_PARAM}
    try:
        build_param_model(action).model_validate(submitted)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_context=False)) from exc

    stored_parameters = dict(submitted)
    if body.dry_run:
        stored_parameters[DRY_RUN_PARAM] = True

    # Validate that the target host/group actually exists
    if body.host_id is not None:
        host_exists = await db.scalar(select(Host.id).where(Host.id == body.host_id))
        if host_exists is None:
            raise HTTPException(status_code=404, detail="Host not found")
    if body.group_id is not None:
        group_exists = await db.scalar(select(HostGroup.id).where(HostGroup.id == body.group_id))
        if group_exists is None:
            raise HTTPException(status_code=404, detail="Group not found")

    # Advisory lock — prevents duplicate concurrent runs for same action+scope
    lock_key = f"actions.{body.action_key}.{body.host_id or body.group_id}"
    lock_result = await db.execute(
        text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
        {"key": lock_key},
    )
    got_lock = lock_result.scalar()
    if not got_lock:
        # Find the running/queued run
        running = await db.scalar(
            select(ActionRun).where(
                ActionRun.action_key == body.action_key,
                ActionRun.host_id == body.host_id
                if body.host_id is not None
                else ActionRun.group_id == body.group_id,
                ActionRun.status.in_(["queued", "running"]),
            )
        )
        raise HTTPException(
            status_code=409,
            detail={
                "detail": "An identical action is already running",
                "running_run_id": running.id if running else None,
            },
        )

    # Create the ActionRun record
    target_kind, target_label = await describe_target(
        db, host_id=body.host_id, group_id=body.group_id
    )
    run = ActionRun(
        action_key=body.action_key,
        action_version=action.version,
        host_id=body.host_id,
        group_id=body.group_id,
        target_kind=target_kind,
        target_label=target_label,
        parameters=stored_parameters,
        parallelism=body.parallelism,
        status="queued",
        triggered_by_user_id=user.id,
    )
    db.add(run)
    await db.flush()
    await db.refresh(run)

    run_id = run.id
    await db.commit()

    # Dispatch Celery task
    try:
        from app.tasks import celery_app

        celery_app.send_task("app.tasks.action_orchestrator.run_action", args=[run_id])
    except Exception as exc:
        logger.warning("Failed to dispatch actions.run task for run %d: %s", run_id, exc)

    # Reload to return fresh state (host_runs will be empty at this point)
    run = await db.get(ActionRun, run_id)
    return ActionRunOut.model_validate(run)


# ---------------------------------------------------------------------------
# GET /actions/runs — List runs
# ---------------------------------------------------------------------------


@router.get("/runs", response_model=list[ActionRunOut])
async def list_runs(
    host_id: int | None = Query(default=None),
    group_id: int | None = Query(default=None),
    status: str | None = Query(
        default=None, description="comma-separated run statuses, e.g. failed,partial"
    ),
    since: datetime | None = Query(default=None, description="only runs created at or after"),
    scheduled: bool | None = Query(
        default=None, description="true: only runs a schedule started; false: only the rest"
    ),
    limit: int = Query(default=20, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List action runs, newest first, optionally filtered by scope, status,
    age and origin.

    Each row carries ``host_counts`` and ``first_failure`` rather than its
    host rows, so a list of group runs can say "17 ok, 1 failed on media"
    in two extra queries however many runs it holds.
    """
    q = select(ActionRun).order_by(ActionRun.created_at.desc()).limit(limit).offset(offset)
    if host_id is not None:
        q = q.where(ActionRun.host_id == host_id)
    if group_id is not None:
        q = q.where(ActionRun.group_id == group_id)
    if status:
        wanted = [s.strip() for s in status.split(",") if s.strip()]
        if wanted:
            q = q.where(ActionRun.status.in_(wanted))
    if since is not None:
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        q = q.where(ActionRun.created_at >= since)
    if scheduled is True:
        q = q.where(ActionRun.scheduled_action_id.isnot(None))
    elif scheduled is False:
        q = q.where(ActionRun.scheduled_action_id.is_(None))

    result = await db.execute(q)
    runs = result.scalars().all()
    out = [ActionRunOut.model_validate(r) for r in runs]
    if not out:
        return out

    ids = [r.id for r in out]
    counts = await db.execute(
        select(ActionHostRun.action_run_id, ActionHostRun.status, func.count())
        .where(ActionHostRun.action_run_id.in_(ids))
        .group_by(ActionHostRun.action_run_id, ActionHostRun.status)
    )
    by_run: dict[int, dict[str, int]] = {}
    for run_id, host_status, n in counts.all():
        by_run.setdefault(run_id, {})[host_status] = n

    # Columns, not the entity: ``output`` is up to a mebibyte per host, and
    # only a short window of it from the first failed task is wanted.
    failures = await db.execute(
        select(
            ActionHostRun.id,
            ActionHostRun.action_run_id,
            ActionHostRun.host_id,
            ActionHostRun.hostname,
            Host.hostname,
            ActionHostRun.exit_code,
            ActionHostRun.error_message,
            func.substring(ActionHostRun.output, FAILURE_WINDOW_SQL),
        )
        .outerjoin(Host, Host.id == ActionHostRun.host_id)
        .where(ActionHostRun.action_run_id.in_(ids), ActionHostRun.status == "failed")
        .order_by(ActionHostRun.action_run_id, ActionHostRun.id)
        .distinct(ActionHostRun.action_run_id)
    )
    first_failure: dict[int, RunHostFailure] = {}
    for hr_id, run_id, h_id, run_hostname, hostname, rc, err, window in failures.all():
        first_failure[run_id] = RunHostFailure(
            host_run_id=hr_id,
            host_id=h_id,
            hostname=hostname or run_hostname,
            exit_code=rc,
            error_message=err,
            reason=failure_reason(window),
        )

    for item in out:
        item.host_counts = by_run.get(item.id, {})
        item.first_failure = first_failure.get(item.id)
    return out


# ---------------------------------------------------------------------------
# GET /actions/host-runs — One host's part in every run that reached it
# ---------------------------------------------------------------------------

#: What ``_rollback`` logs when the snapshot restore worked.
_ROLLED_BACK = "[rollback] success=True"


@router.get("/host-runs", response_model=list[HostRunEntry])
async def list_host_runs(
    host_id: int = Query(...),
    since: datetime | None = Query(default=None, description="only runs created at or after"),
    scheduled: bool | None = Query(
        default=None, description="true: only runs a schedule started; false: only the rest"
    ),
    limit: int = Query(default=100, ge=1, le=500),
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Every run that reached this host, newest first, with this host's
    own result.

    ``/runs?host_id=`` only finds runs aimed at the host itself. A host
    usually gets its scheduled work through a group, so its history is
    read from its per-host rows instead, joined to the run each belongs
    to.
    """
    failed = ActionHostRun.status == "failed"
    q = (
        select(
            ActionHostRun.id,
            ActionHostRun.action_run_id,
            ActionRun.action_key,
            ActionRun.status,
            ActionHostRun.status,
            ActionRun.target_kind,
            ActionRun.target_label,
            ActionRun.scheduled_action_id,
            ActionRun.alert_event_id,
            ActionRun.triggered_by_user_id,
            ActionRun.created_at,
            ActionHostRun.started_at,
            ActionHostRun.finished_at,
            ActionHostRun.exit_code,
            ActionHostRun.error_message,
            # Only a failed row needs its log read, and then only a window.
            case((failed, func.substring(ActionHostRun.output, FAILURE_WINDOW_SQL)), else_=None),
            case((failed, func.strpos(ActionHostRun.output, _ROLLED_BACK) > 0), else_=False),
        )
        .join(ActionRun, ActionRun.id == ActionHostRun.action_run_id)
        .where(ActionHostRun.host_id == host_id)
        .order_by(ActionRun.created_at.desc(), ActionHostRun.id.desc())
        .limit(limit)
    )
    if since is not None:
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        q = q.where(ActionRun.created_at >= since)
    if scheduled is True:
        q = q.where(ActionRun.scheduled_action_id.isnot(None))
    elif scheduled is False:
        q = q.where(ActionRun.scheduled_action_id.is_(None))

    rows = (await db.execute(q)).all()
    return [
        HostRunEntry(
            host_run_id=hr_id,
            action_run_id=run_id,
            action_key=key,
            run_status=run_status,
            status=host_status,
            target_kind=kind,
            target_label=label,
            scheduled_action_id=sched_id,
            alert_event_id=alert_id,
            triggered_by_user_id=user_id,
            created_at=created,
            started_at=started,
            finished_at=finished,
            exit_code=rc,
            error_message=err,
            reason=failure_reason(window),
            rolled_back=bool(rolled),
        )
        for (
            hr_id,
            run_id,
            key,
            run_status,
            host_status,
            kind,
            label,
            sched_id,
            alert_id,
            user_id,
            created,
            started,
            finished,
            rc,
            err,
            window,
            rolled,
        ) in rows
    ]


# ---------------------------------------------------------------------------
# GET /actions/runs/{id} — Single run with host runs
# ---------------------------------------------------------------------------


@router.get("/runs/{id}", response_model=ActionRunOut)
async def get_run(
    id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a single action run with all host run details."""
    run = await db.get(ActionRun, id)
    if run is None:
        raise HTTPException(status_code=404, detail="Action run not found")

    host_runs_result = await db.execute(
        select(ActionHostRun, Host.hostname)
        .outerjoin(Host, Host.id == ActionHostRun.host_id)
        .where(ActionHostRun.action_run_id == id)
        .order_by(ActionHostRun.id)
    )
    host_runs = host_runs_result.all()

    out = ActionRunOut.model_validate(run)
    host_runs_out: list[ActionHostRunOut] = []
    for hr, hostname in host_runs:
        hr_out = ActionHostRunOut.model_validate(hr)
        # The live name when the host is still there, the dispatch-time
        # snapshot when it is not. The outer join yields NULL for a
        # deleted host, which is the whole reason the snapshot exists.
        hr_out.hostname = hostname or hr.hostname
        host_runs_out.append(hr_out)
    out.host_runs = host_runs_out

    from app.ai.models import AISession

    sessions = (
        await db.execute(
            select(AISession)
            .where(AISession.investigates_action_run_id == id)
            .order_by(AISession.created_at.desc(), AISession.id.desc())
        )
    ).scalars()
    out.investigations = [
        RunInvestigationRef(
            session_id=s.id,
            status=s.status,
            target_host_ids=list(s.target_host_ids or []),
            created_at=s.created_at,
        )
        for s in sessions
    ]
    if run.alert_event_id is not None:
        from app.ai.models import AlertEvent

        out.alertname = (
            await db.execute(
                select(AlertEvent.alertname).where(AlertEvent.id == run.alert_event_id)
            )
        ).scalar_one_or_none()
    return out


# ---------------------------------------------------------------------------
# GET /actions/runs/{id}/hosts/{host_id}/output — Full output text
# ---------------------------------------------------------------------------


@router.get("/runs/{id}/hosts/{host_id}/output")
async def get_host_run_output(
    id: int,
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the raw Ansible output for a specific host run as plain text.

    Addressed by *host* id, so it cannot reach a run whose host has since
    been deleted — ``host_id`` is NULL on those rows and matches nothing.
    Those transcripts are still kept (BUG-77); read them through
    ``/runs/{id}/host-runs/{host_run_id}/output``, which the UI uses for
    every row. This route stays for callers that already have a host id.
    """
    host_run = await db.scalar(
        select(ActionHostRun).where(
            ActionHostRun.action_run_id == id,
            ActionHostRun.host_id == host_id,
        )
    )
    if host_run is None:
        raise HTTPException(status_code=404, detail="Host run not found")

    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(content=host_run.output or "")


@router.get("/runs/{id}/host-runs/{host_run_id}/output")
async def get_host_run_output_by_row(
    id: int,
    host_run_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Return one host run's raw output, addressed by the row's own id.

    The transcript outlives the host (BUG-77), so the row id is the only
    identifier guaranteed to still resolve. ``id`` is checked against the
    row's parent so a run id cannot be used to read another run's output.
    """
    host_run = await db.scalar(
        select(ActionHostRun).where(
            ActionHostRun.id == host_run_id,
            ActionHostRun.action_run_id == id,
        )
    )
    if host_run is None:
        raise HTTPException(status_code=404, detail="Host run not found")

    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(content=host_run.output or "")


# ---------------------------------------------------------------------------
# POST /actions/runs/{id}/investigate — Hand the run to the assistant
# ---------------------------------------------------------------------------


@router.post("/runs/{id}/investigate", response_model=RunInvestigationOut)
async def investigate_run(
    id: int,
    response: Response,
    body: RunInvestigateBody | None = None,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Start a read-only assistant session about this run, or reopen one.

    The session's mission carries the run's details and an excerpt of each
    host's log; the model reads the rest with ``get_action_run_output``.
    ``host_run_id`` narrows it to that host. A second press for the same
    hosts returns the earlier session (200) unless it failed or was
    stopped; a new one is 201.

    The same gates as the alerts page's investigate button: the kill
    switch, a provider that can run tools, and the budget. Opening an
    existing session needs none of them.
    """
    from app.ai import service
    from app.ai.run_investigation import (
        RunInvestigationRefused,
        existing_investigation,
        investigation_scope,
        start_run_investigation,
    )
    from app.ai.service import AIDisabledError, BudgetExceededError

    run = await db.get(ActionRun, id)
    if run is None:
        raise HTTPException(status_code=404, detail="Action run not found")
    host_run = None
    if body is not None and body.host_run_id is not None:
        host_run = await db.scalar(
            select(ActionHostRun).where(
                ActionHostRun.id == body.host_run_id, ActionHostRun.action_run_id == id
            )
        )
        if host_run is None:
            raise HTTPException(status_code=404, detail="Host run not found")

    try:
        host_runs, host_ids = await investigation_scope(db, run, host_run)
    except RunInvestigationRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    earlier = await existing_investigation(db, run.id, host_ids)
    if earlier is not None:
        return RunInvestigationOut(session_id=earlier.id, created=False)

    try:
        provider = await service.resolve_provider(db, None)
        service.assert_can_investigate(provider)
        await service.assert_within_budget(db, provider)
    except AIDisabledError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BudgetExceededError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc

    session = await start_run_investigation(
        db, run, provider, host_runs=host_runs, host_ids=host_ids, host_run=host_run, user=user
    )
    await db.commit()

    from app.tasks import celery_app

    celery_app.send_task("app.tasks.ai_task.run_chat_session", kwargs={"session_id": session.id})
    response.status_code = 201
    return RunInvestigationOut(session_id=session.id, created=True)


# ---------------------------------------------------------------------------
# POST /actions/runs/{id}/cancel — Cancel run
# ---------------------------------------------------------------------------


@router.post("/runs/{id}/cancel")
async def cancel_run(
    id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a run: stop the hosts it has not started, let running ones finish.

    The Redis token tells an orchestrator still driving the run, and a
    group task about to start, to stop. The hosts it has not started are
    cancelled here rather than left to that orchestrator: once it has died,
    or has dispatched everything and returned, nothing else would, and they
    read ``queued`` or ``pending`` for good with the run never showing an
    end (BUG-104). A ``pending`` run is cancelled too. Its token alone
    expired after an hour, and the host queue would then start the run.

    A host already running is left to finish. The run gets its
    ``finished_at`` here when no host is running, otherwise from
    ``finalise_run_if_complete`` when the last one ends.
    """
    run = await db.get(ActionRun, id)
    if run is None:
        raise HTTPException(status_code=404, detail="Action run not found")

    import redis as redis_lib

    from app.config import settings

    r = redis_lib.from_url(settings.redis.url)
    r.setex(f"actions.cancel.{id}", 3600, "1")

    if run.status not in ("queued", "running", "pending"):
        return {"ok": True}

    now = datetime.now(UTC)
    run.status = "cancelled"

    # A deferred ``_builtin.sync`` row stays ``pending`` while the SyncJob it
    # queued does the work, and that job closes it (BUG-81). Once the job is
    # running, the row is as good as running.
    syncing = exists().where(
        SyncJob.origin_action_host_run_id == ActionHostRun.id,
        SyncJob.status == JobStatus.running,
    )
    cancelled_ids = (
        (
            await db.execute(
                update(ActionHostRun)
                .where(
                    ActionHostRun.action_run_id == id,
                    ActionHostRun.status.in_(("queued", "pending")),
                    ~syncing,
                )
                .values(status="cancelled", finished_at=now)
                .returning(ActionHostRun.id)
            )
        )
        .scalars()
        .all()
    )
    if cancelled_ids:
        # Still queued, the job would run the sync the run was cancelled out of.
        await db.execute(
            update(SyncJob)
            .where(
                SyncJob.origin_action_host_run_id.in_(cancelled_ids),
                SyncJob.status == JobStatus.pending,
            )
            .values(status=JobStatus.cancelled, completed_at=now)
        )

    still_running = await db.scalar(
        select(
            exists().where(
                ActionHostRun.action_run_id == id,
                (ActionHostRun.status == "running") | syncing,
            )
        )
    )
    if not still_running:
        run.finished_at = now
    await db.commit()

    try:
        r.publish(f"actions.run.{id}", json.dumps({"event": "status", "status": "cancelled"}))
    except Exception:
        logger.debug("could not publish the cancel", exc_info=True)

    return {"ok": True}


# ---------------------------------------------------------------------------
# GET /actions/runs/{id}/stream — SSE stream
# ---------------------------------------------------------------------------


@router.get("/runs/{id}/stream")
async def stream_run_events(
    id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Server-Sent Events stream for a run. Subscribes to Redis pub/sub channel
    'actions.run.{id}', relays events. Closes when run reaches terminal status."""

    run = await db.get(ActionRun, id)
    if run is None:
        raise HTTPException(status_code=404, detail="Action run not found")

    terminal_states = {"succeeded", "failed", "partial", "cancelled"}
    run_status = run.status

    async def event_generator():
        import redis.asyncio as aioredis

        from app.config import settings

        client = aioredis.from_url(settings.redis.url)
        pubsub = client.pubsub()
        await pubsub.subscribe(f"actions.run.{id}")

        try:
            # If run is already terminal, send current status and close
            if run_status in terminal_states:
                yield f"event: status\ndata: {json.dumps({'status': run_status})}\n\n"
                return

            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue

                data = json.loads(message["data"])
                event_type = data.get("event", "output")
                payload = json.dumps(data)
                yield f"event: {event_type}\ndata: {payload}\n\n"

                # Close on terminal status
                if data.get("event") == "status" and data.get("status") in terminal_states:
                    return
        finally:
            await pubsub.unsubscribe(f"actions.run.{id}")
            await client.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# GET /actions/{action_key} — Single action
#
# NOTE: This route must be registered AFTER all /runs* routes.  FastAPI
# evaluates routes in registration order; placing /{action_key} before
# /runs would cause "GET /actions/runs" to be captured by the dynamic
# segment (action_key="runs") and return 404 "Action not found".
# ---------------------------------------------------------------------------


@router.get("/{action_key}", response_model=ActionDefinitionOut)
async def get_action(
    action_key: str,
    _: User = Depends(current_active_user),
):
    """Return a single action definition by key."""
    defn = ACTION_REGISTRY.get(action_key)
    if defn is None:
        raise HTTPException(status_code=404, detail="Action not found")
    return _definition_to_out(defn)
