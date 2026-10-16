"""Alerts: the list, what alerts may run, intake, and per-host view.

``/api/alerts`` is the home of everything about alerts that is not an AI
session. The list and the manual investigate button were under
``/api/ai/alerts`` before 1.0.0; those paths still answer, deprecated, for
the life of 1.x (:mod:`app.api.ai`).

The list of actions alerts may run (``/api/alerts/actions``) is LabDog's
half of the ``labdog_action`` label (:mod:`app.alerts.labels`): Grafana
says which action an alert asks for, and only an action on this list, on
a host inside its scope, is started. The list starts empty.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.registry import ACTION_REGISTRY
from app.actions.validation import build_param_model
from app.ai import service
from app.ai.models import AIRollback, AISession, AlertEvent
from app.ai.schemas import AlertDispatchOut, AlertEventResponse
from app.ai.service import AIDisabledError, BudgetExceededError
from app.alerts.models import AlertAction, AlertActionDispatch
from app.audit.logger import log_action
from app.auth.users import current_active_user
from app.db import get_db
from app.models.action_run import ActionRun
from app.models.host import Host
from app.models.host_group import HostGroup
from app.models.user import User
from app.schemas.alerts import (
    AlertActionIn,
    AlertActionOut,
    AlertIntakeStatus,
    HostAlertActionOut,
    HostAlertsResponse,
)
from app.settings_service import get_setting_typed

router = APIRouter(prefix="/alerts", tags=["alerts"])


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------

#: How much of a report to put on an alert row. Long enough for a verdict
#: ("healthy", "false positive", "the service is down"), short enough that
#: a list of alerts stays a list.
_SUMMARY_CHARS = 280


def _is_heading(paragraph: str) -> bool:
    """Whether a paragraph is only a markdown heading.

    Reports vary in how they open. Some begin with the verdict; others
    begin with ``## Summary`` and put the verdict underneath. Taking the
    first paragraph blindly turned the second kind into a row that said
    "## Summary" — a heading, rendered as prose, telling the operator
    nothing. Caught against real reports rather than the fixtures, which
    all happened to be of the first kind.
    """
    return all(line.lstrip().startswith("#") for line in paragraph.splitlines() if line.strip())


def _conclusion(report: str | None) -> str | None:
    """The first substantive paragraph of a report, trimmed to fit a row.

    Reports open with the verdict and then justify it, so the opening is
    the part an operator scanning the list needs — but only once any
    heading above it is skipped. Truncated on a word boundary with an
    ellipsis, so a cut is visible rather than looking like the assistant
    stopped mid-sentence.
    """
    if not report:
        return None
    para = next(
        (p.strip() for p in report.split("\n\n") if p.strip() and not _is_heading(p.strip())),
        "",
    )
    if not para:
        return None
    # Markdown emphasis reads as noise once the markup is not rendered.
    para = para.replace("**", "").replace("`", "")
    if len(para) <= _SUMMARY_CHARS:
        return para
    return para[:_SUMMARY_CHARS].rsplit(" ", 1)[0] + "…"


async def _with_investigation(db: AsyncSession, rows: list[AlertEvent]) -> list[AlertEventResponse]:
    """Attach each alert's investigation status and conclusion.

    One extra query for the whole page rather than a relationship on the
    model: this is the only place that needs it, and a lazy-loaded
    relationship on a list endpoint is how a hundred-row page becomes a
    hundred queries.
    """
    session_ids = [r.investigation_session_id for r in rows if r.investigation_session_id]
    sessions: dict[int, AISession] = {}
    if session_ids:
        found = await db.execute(select(AISession).where(AISession.id.in_(session_ids)))
        sessions = {s.id: s for s in found.scalars().all()}

    # The latest rollback per alert, automatic or not: a refused automatic
    # one followed by someone's successful one should read as rolled back.
    latest_rollback: dict[int, AIRollback] = {}
    event_ids = [r.id for r in rows]
    if event_ids:
        found = await db.execute(
            select(AIRollback)
            .where(AIRollback.alert_event_id.in_(event_ids))
            .order_by(AIRollback.id)
        )
        for rollback in found.scalars().all():
            latest_rollback[rollback.alert_event_id] = rollback

    actions: dict[int, list[AlertDispatchOut]] = {}
    if event_ids:
        found = await db.execute(
            select(AlertActionDispatch, ActionRun.status)
            .outerjoin(ActionRun, ActionRun.id == AlertActionDispatch.action_run_id)
            .where(AlertActionDispatch.alert_event_id.in_(event_ids))
            .order_by(AlertActionDispatch.id)
        )
        for dispatch, run_status in found.all():
            action = ACTION_REGISTRY.get(dispatch.action_key)
            actions.setdefault(dispatch.alert_event_id, []).append(
                AlertDispatchOut(
                    id=dispatch.id,
                    alert_action_id=dispatch.alert_action_id,
                    action_key=dispatch.action_key,
                    action_name=action.name if action else None,
                    host_id=dispatch.host_id,
                    action_run_id=dispatch.action_run_id,
                    run_status=run_status,
                    outcome=dispatch.outcome,
                    detail=dispatch.detail,
                    check_outcome=dispatch.check_outcome,
                    checked_at=dispatch.checked_at,
                )
            )

    out = []
    for row in rows:
        response = AlertEventResponse.model_validate(row)
        response.actions = actions.get(row.id, [])
        session = sessions.get(row.investigation_session_id or 0)
        if session is not None:
            response.investigation_status = session.status
            response.investigation_summary = _conclusion(session.report_markdown)
        rollback = latest_rollback.get(row.id)
        if rollback is not None:
            response.rollback_status = rollback.status
            response.rollback_detail = rollback.detail
        out.append(response)
    return out


@router.get("", response_model=list[AlertEventResponse])
async def list_alerts(
    limit: int = 100,
    status: str | None = None,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Recent alerts, newest first.

    Ordered by ``created_at`` rather than ``starts_at``: an alert that
    fired an hour ago but only reached LabDog now is new information to
    the operator reading this list, and burying it an hour down would
    hide exactly the case the poller exists to catch.
    """
    stmt = select(AlertEvent).order_by(AlertEvent.created_at.desc()).limit(min(limit, 500))
    if status in ("firing", "resolved"):
        stmt = stmt.where(AlertEvent.status == status)
    result = await db.execute(stmt)
    return await _with_investigation(db, list(result.scalars().all()))


@router.post("/{alert_id}/investigate", response_model=AlertEventResponse)
async def investigate_alert_now(
    alert_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Start an investigation for one alert by hand.

    The manual path deliberately skips the labels — an operator asking
    for this has already made the judgement labdog_ai exists to automate —
    but not the kill switch, the provider check, or
    the budget. Those are about whether LabDog *may* spend, which a
    button press does not change. Nor does it change the autonomy level,
    which comes from the same remediation policy as an automatic start.
    """
    alert = await db.get(AlertEvent, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    if alert.investigation_session_id is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "This alert already has an investigation.",
                "session_id": alert.investigation_session_id,
            },
        )

    try:
        provider = await service.resolve_provider(db, None)
        service.assert_can_investigate(provider)
        await service.assert_within_budget(db, provider)
    except AIDisabledError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BudgetExceededError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc

    from app.tasks import celery_app
    from app.tasks.ai_alerts import start_investigation

    # The same policy as the automatic path, full auto included: pressing
    # the button decides that the alert is worth a look, not what the
    # session may change. That stays with the remediation settings.
    session = await start_investigation(db, alert, provider, user=user)
    await db.commit()
    await db.refresh(alert)

    celery_app.send_task("app.tasks.ai_task.run_chat_session", kwargs={"session_id": session.id})
    # Through the same helper as the list, so the row the caller renders
    # immediately after clicking carries the session's status rather than
    # an empty field that fills in on the next refetch.
    return (await _with_investigation(db, [alert]))[0]


# ---------------------------------------------------------------------------
# Intake and hosts
# ---------------------------------------------------------------------------


@router.get("/intake", response_model=AlertIntakeStatus)
async def alert_intake_status(
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Whether alerts can reach LabDog, what they can set off, and when the
    last one arrived.

    The intake switch and the poll interval are settings; the webhook token
    is in the config file and has no other way to show up in the UI, so an
    intake that is on but refusing every webhook for want of a token looked
    exactly like one that was working and quiet.
    """
    from app.ai.alert_autonomy import MIN_WEBHOOK_TOKEN_CHARS
    from app.ai.full_auto_rules import active_rules
    from app.config import settings

    token = settings.alerts.webhook_token
    rules_enabled, rules = await active_rules(db)
    last = dict(
        (
            await db.execute(
                select(AlertEvent.source, func.max(AlertEvent.created_at)).group_by(
                    AlertEvent.source
                )
            )
        ).all()
    )
    allowed = await db.scalar(
        select(func.count()).select_from(AlertAction).where(AlertAction.enabled.is_(True))
    )
    return AlertIntakeStatus(
        intake_enabled=bool(int(await get_setting_typed("alerts.intake_enabled", db))),
        webhook_path="/api/webhooks/grafana-alerts",
        token_set=bool(token),
        token_long_enough_for_full_auto=len(token) >= MIN_WEBHOOK_TOKEN_CHARS,
        ai_enabled=bool(int(await get_setting_typed("ai.enabled", db))),
        auto_investigate_enabled=bool(
            int(await get_setting_typed("ai.auto_investigate_enabled", db))
        ),
        full_auto_rules_enabled=rules_enabled,
        full_auto_rules=len(rules),
        allowed_actions=allowed or 0,
        poll_minutes=int(await get_setting_typed("alerts.poll_minutes", db)),
        last_webhook_alert_at=last.get("grafana_webhook"),
        last_poll_alert_at=last.get("alertmanager_poll"),
    )


@router.get("/hosts/{host_id}", response_model=HostAlertsResponse)
async def host_alerts(
    host_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """What alerts may do to ``host_id``: the actions they may run, and
    whether they may fix it without asking.

    The per-host view of the list and the full-auto rules, so a host's page
    answers "can an alert change this machine unattended?" without reading
    either whole. Blockers are the safeguards that hold every alert back
    from full auto on this host: the standing ones (intake, the token, the
    snapshot) and the ones that hold for now whatever the alert (a fix that
    made the host worse in the last 24 h, a fix not yet checked). The
    cooldown, the daily cap and the rest depend on the alert, and its row
    on the Alerts page reports them.
    """
    from app.ai.alert_autonomy import MIN_WEBHOOK_TOKEN_CHARS, snapshot_refusal
    from app.ai.full_auto_rules import active_rules, host_context
    from app.ai.remediation import WORSE_SUSPENDS_FULL_AUTO, awaiting_check, recently_made_worse
    from app.alerts.labels import in_scope, scope_label
    from app.config import settings

    if await db.get(Host, host_id) is None:
        raise HTTPException(status_code=404, detail="Host not found")
    rules_enabled, rules = await active_rules(db)
    hostname, groups = await host_context(db, host_id)
    applicable = [r.text for r in rules if hostname and r.applies_to_host(hostname, groups)]

    blockers: list[str] = []
    if not int(await get_setting_typed("alerts.intake_enabled", db)):
        blockers.append("alert intake is off (alerts.intake_enabled)")
    if len(settings.alerts.webhook_token) < MIN_WEBHOOK_TOKEN_CHARS:
        blockers.append(
            f"the alert webhook token is shorter than {MIN_WEBHOOK_TOKEN_CHARS} characters, "
            f"so alerts that arrive by webhook cannot run at full auto"
        )
    if int(await get_setting_typed("ai.alert_full_auto_requires_snapshot", db)):
        refusal = await snapshot_refusal(db, host_id)
        if refusal:
            blockers.append(refusal)
    now = datetime.now(UTC)
    worse_at = await recently_made_worse(db, host_id, now)
    if worse_at is not None:
        until = worse_at + WORSE_SUSPENDS_FULL_AUTO
        blockers.append(
            f"an automatic fix made this host worse, so full auto is off it until "
            f"{until:%Y-%m-%d %H:%M} UTC"
        )
    if await awaiting_check(db, host_id, now):
        blockers.append("an automatic fix on this host has not been checked yet")

    alert_actions = []
    entries = (await db.execute(select(AlertAction).order_by(AlertAction.action_key))).scalars()
    for entry in entries.all():
        if await in_scope(db, entry, host_id):
            action = ACTION_REGISTRY.get(entry.action_key)
            alert_actions.append(
                HostAlertActionOut(
                    id=entry.id,
                    action_key=entry.action_key,
                    action_name=action.name if action else None,
                    scope=await scope_label(db, entry),
                    enabled=entry.enabled,
                )
            )
    return HostAlertsResponse(
        ai_enabled=bool(int(await get_setting_typed("ai.enabled", db))),
        auto_investigate_enabled=bool(
            int(await get_setting_typed("ai.auto_investigate_enabled", db))
        ),
        rules_enabled=rules_enabled,
        rules=applicable,
        blockers=blockers,
        alert_actions=alert_actions,
    )


# ---------------------------------------------------------------------------
# Actions alerts may run
# ---------------------------------------------------------------------------


async def _validate_entry(db: AsyncSession, body: AlertActionIn) -> None:
    action = ACTION_REGISTRY.get(body.action_key)
    if action is None:
        raise HTTPException(status_code=422, detail=f"No action has the key {body.action_key!r}.")
    if not action.supports_host:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{action.name} cannot run on a single host, and a run an alert starts is "
                f"always the one host the alert names."
            ),
        )
    if body.target_kind == "fleet":
        if body.target_id is not None:
            raise HTTPException(status_code=422, detail="All hosts takes no target_id.")
    elif body.target_id is None:
        raise HTTPException(status_code=422, detail=f"A {body.target_kind} needs a target_id.")
    elif body.target_kind == "host" and await db.get(Host, body.target_id) is None:
        raise HTTPException(status_code=404, detail="Host not found")
    elif body.target_kind == "group" and await db.get(HostGroup, body.target_id) is None:
        raise HTTPException(status_code=404, detail="Host group not found")
    try:
        build_param_model(action).model_validate(body.parameters)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_context=False)) from exc


def _audit_payload(entry: AlertAction) -> dict[str, Any]:
    return {
        "action_key": entry.action_key,
        "target_kind": entry.target_kind,
        "target_id": entry.target_id,
        "parameters": entry.parameters,
        "snapshot_enabled": entry.snapshot_enabled,
        "verify_enabled": entry.verify_enabled,
        "auto_rollback": entry.auto_rollback,
        "cooldown_minutes": entry.cooldown_minutes,
        "enabled": entry.enabled,
    }


async def _hydrate(db: AsyncSession, entry: AlertAction) -> AlertActionOut:
    from app.actions.run_target import FLEET_LABEL, describe_target

    out = AlertActionOut.model_validate(entry)
    action = ACTION_REGISTRY.get(entry.action_key)
    if action is not None:
        out.action_name = action.name
        out.pack_name = action.pack_name
        out.destructive = action.destructive
    if entry.target_kind == "fleet":
        out.target_name = FLEET_LABEL
    else:
        _, out.target_name = await describe_target(
            db,
            host_id=entry.target_id if entry.target_kind == "host" else None,
            group_id=entry.target_id if entry.target_kind == "group" else None,
        )
    last = (
        await db.execute(
            select(ActionRun.id, ActionRun.status, ActionRun.created_at)
            .join(AlertActionDispatch, AlertActionDispatch.action_run_id == ActionRun.id)
            .where(AlertActionDispatch.alert_action_id == entry.id)
            .order_by(desc(ActionRun.created_at))
            .limit(1)
        )
    ).first()
    if last is not None:
        out.last_run_id, out.last_run_status, out.last_run_at = last
    return out


async def _get_entry(db: AsyncSession, entry_id: int) -> AlertAction:
    entry = await db.get(AlertAction, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Not on the list of actions alerts may run")
    return entry


_DUPLICATE = "{key} is already on the list of actions alerts may run. Edit that entry instead."


@router.get("/actions", response_model=list[AlertActionOut])
async def list_alert_actions(
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """The actions alerts may run, by action key."""
    entries = (await db.execute(select(AlertAction).order_by(AlertAction.action_key))).scalars()
    return [await _hydrate(db, entry) for entry in entries.all()]


@router.post("/actions", response_model=AlertActionOut, status_code=201)
async def create_alert_action(
    body: AlertActionIn,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Allow alerts to run an action: ``labdog_action=<key>`` then starts it."""
    await _validate_entry(db, body)
    if await db.scalar(select(AlertAction.id).where(AlertAction.action_key == body.action_key)):
        raise HTTPException(status_code=409, detail=_DUPLICATE.format(key=body.action_key))
    entry = AlertAction(**body.model_dump())
    db.add(entry)
    try:
        await db.flush()
    except IntegrityError as exc:
        # Lost a race with another create for the same key.
        raise HTTPException(status_code=409, detail=_DUPLICATE.format(key=body.action_key)) from exc
    await db.refresh(entry)
    await log_action(
        db,
        action="alert_action.created",
        entity_type="alert_action",
        entity_id=entry.id,
        user_id=user.id,
        after_state=_audit_payload(entry),
    )
    await db.commit()
    return await _hydrate(db, entry)


@router.get("/actions/{entry_id}", response_model=AlertActionOut)
async def get_alert_action(
    entry_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    return await _hydrate(db, await _get_entry(db, entry_id))


@router.put("/actions/{entry_id}", response_model=AlertActionOut)
async def update_alert_action(
    entry_id: int,
    body: AlertActionIn,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Change an entry. The action key cannot change: the label names it,
    and Grafana rules already point at it."""
    entry = await _get_entry(db, entry_id)
    if body.action_key != entry.action_key:
        raise HTTPException(
            status_code=422,
            detail="The action cannot change; take it off the list and allow the other one.",
        )
    await _validate_entry(db, body)
    before = _audit_payload(entry)
    for field, value in body.model_dump(exclude={"action_key"}).items():
        setattr(entry, field, value)
    await db.flush()
    await db.refresh(entry)
    await log_action(
        db,
        action="alert_action.updated",
        entity_type="alert_action",
        entity_id=entry.id,
        user_id=user.id,
        before_state=before,
        after_state=_audit_payload(entry),
    )
    await db.commit()
    return await _hydrate(db, entry)


@router.delete("/actions/{entry_id}", status_code=204)
async def delete_alert_action(
    entry_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Take an action off the list. Alerts naming it are refused from now
    on; what earlier alerts did with it stays on their rows."""
    entry = await _get_entry(db, entry_id)
    before = _audit_payload(entry)
    await db.delete(entry)
    await log_action(
        db,
        action="alert_action.deleted",
        entity_type="alert_action",
        entity_id=entry_id,
        user_id=user.id,
        before_state=before,
    )
    await db.commit()
