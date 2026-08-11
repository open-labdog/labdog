"""AI provider, session, and usage endpoints.

Provider CRUD mirrors ``app/api/grafana.py`` (encrypted secret, tri-state
update, connectivity test). Session streaming mirrors the action-run SSE
endpoint in ``app/api/actions.py`` — same Redis pub/sub relay, a different
channel — so the frontend has one streaming idiom to learn, not two.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import service
from app.ai.models import (
    AIApprovalRequest,
    AIMessage,
    AIProvider,
    AISession,
    AIToolCall,
    AIUsageDay,
)
from app.ai.providers.base import LLMProviderError
from app.ai.providers.factory import build_provider, runs_on_agent_sdk
from app.ai.schemas import (
    AIApprovalDecision,
    AIApprovalResponse,
    AIProviderCreate,
    AIProviderResponse,
    AIProviderTestResponse,
    AIProviderUpdate,
    AISessionCreate,
    AISessionDetail,
    AISessionMessageRequest,
    AISessionResponse,
    AIUsageDayResponse,
    AIUsageSummary,
    provider_to_response,
    session_to_response,
)
from app.ai.service import AIDisabledError, BudgetExceededError
from app.audit.logger import log_action
from app.auth.users import current_active_user
from app.crypto import encrypt_ssh_key, get_master_key
from app.db import get_db
from app.models.user import User
from app.settings_service import get_setting_typed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])

SESSION_CHANNEL = "ai.session.{id}"
TERMINAL_STATES = {"succeeded", "failed", "cancelled"}


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


async def _unset_other_defaults(db: AsyncSession, keep_id: int | None) -> None:
    stmt = update(AIProvider).values(is_default=False)
    if keep_id is not None:
        stmt = stmt.where(AIProvider.id != keep_id)
    await db.execute(stmt)


#: What `claude setup-token` emits. Checked at save time because the field
#: sits next to several other credential fields in the UI and accepts any
#: string: pasting the wrong secret stores something guaranteed to 401, and
#: without this the first sign of it is a failed session.
#:
#: Kept to a prefix test rather than a full format match — if Anthropic
#: lengthens or re-shapes the token this still passes, and it only has to
#: catch the case of a credential that plainly is not one of these.
SUBSCRIPTION_TOKEN_PREFIX = "sk-ant-oat"  # nosec B105 - prefix, not a secret

#: Backends whose credential is a Claude subscription token from
#: ``claude setup-token``, not an API key. Both drive Claude Code, so both
#: reject an API key pasted into the same field.
SUBSCRIPTION_PROVIDER_TYPES = frozenset({"claude_cli", "claude_agent"})


def _check_subscription_token(provider_type: str, api_key: str | None) -> None:
    """Reject a subscription token that is obviously not one.

    A blank value is fine and means "use whatever the host is logged in as".
    """
    if provider_type not in SUBSCRIPTION_PROVIDER_TYPES or not api_key:
        return
    if api_key.startswith(SUBSCRIPTION_TOKEN_PREFIX):
        return
    raise HTTPException(
        status_code=400,
        detail=(
            f"That does not look like a Claude subscription token — they begin "
            f"with {SUBSCRIPTION_TOKEN_PREFIX!r}. Run 'claude setup-token' on "
            f"your own machine and paste the value it prints. An Anthropic API "
            f"key will not work here; use an Anthropic provider for that."
        ),
    )


@router.get("/providers", response_model=list[AIProviderResponse])
async def list_providers(
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AIProvider).order_by(AIProvider.name))
    return [provider_to_response(p) for p in result.scalars().all()]


@router.post("/providers", response_model=AIProviderResponse, status_code=201)
async def create_provider(
    payload: AIProviderCreate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(AIProvider).where(AIProvider.name == payload.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="A provider with that name already exists")

    if payload.provider_type == "openai_compat" and not payload.base_url:
        raise HTTPException(
            status_code=400,
            detail="An OpenAI-compatible provider needs a base URL, e.g. http://localhost:11434/v1",
        )

    # The Anthropic API always authenticates, so a keyless provider is
    # guaranteed to fail — and it fails at first use, as a 401 from inside a
    # session, rather than here where the operator can see the cause.
    if payload.provider_type == "anthropic" and not payload.api_key:
        raise HTTPException(
            status_code=400,
            detail=(
                "An Anthropic provider needs an API key. Create one in the "
                "Claude Console at platform.claude.com under API keys."
            ),
        )

    _check_subscription_token(payload.provider_type, payload.api_key)

    provider = AIProvider(
        name=payload.name,
        provider_type=payload.provider_type,
        base_url=payload.base_url,
        model=payload.model,
        verify_ssl=payload.verify_ssl,
        ca_cert_pem=payload.ca_cert_pem,
        max_tokens=payload.max_tokens,
        temperature=payload.temperature,
        is_default=payload.is_default,
        input_cost_per_mtok=payload.input_cost_per_mtok,
        output_cost_per_mtok=payload.output_cost_per_mtok,
        monthly_budget=payload.monthly_budget,
        enabled=payload.enabled,
    )
    db.add(provider)
    await db.flush()

    if payload.api_key:
        provider.encrypted_api_key = encrypt_ssh_key(payload.api_key, get_master_key())
    if payload.is_default:
        await _unset_other_defaults(db, provider.id)

    await log_action(
        db,
        action="ai_provider_created",
        entity_type="ai_provider",
        entity_id=provider.id,
        user_id=user.id,
        after_state={
            "name": provider.name,
            "provider_type": provider.provider_type,
            "model": provider.model,
        },
    )
    await db.commit()
    await db.refresh(provider)
    return provider_to_response(provider)


@router.patch("/providers/{provider_id}", response_model=AIProviderResponse)
async def update_provider(
    provider_id: int,
    payload: AIProviderUpdate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    provider = await db.get(AIProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")

    data = payload.model_dump(exclude_unset=True)
    api_key = data.pop("api_key", None)

    for field, value in data.items():
        if value is not None:
            setattr(provider, field, value)

    if api_key == "" and provider.provider_type == "anthropic":
        raise HTTPException(
            status_code=400,
            detail="An Anthropic provider needs an API key; it cannot be cleared.",
        )

    _check_subscription_token(provider.provider_type, api_key)

    # Tri-state: absent keeps, "" clears, a value replaces.
    if api_key is not None:
        provider.encrypted_api_key = encrypt_ssh_key(api_key, get_master_key()) if api_key else None

    if data.get("is_default"):
        await _unset_other_defaults(db, provider.id)

    changed: dict = {"fields": sorted(data)}
    if api_key is not None:
        changed["api_key"] = "cleared" if api_key == "" else "changed"

    await log_action(
        db,
        action="ai_provider_updated",
        entity_type="ai_provider",
        entity_id=provider.id,
        user_id=user.id,
        after_state=changed,
    )
    await db.commit()
    await db.refresh(provider)
    return provider_to_response(provider)


@router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    provider = await db.get(AIProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    await log_action(
        db,
        action="ai_provider_deleted",
        entity_type="ai_provider",
        entity_id=provider.id,
        user_id=user.id,
        before_state={"name": provider.name, "provider_type": provider.provider_type},
    )
    await db.delete(provider)
    await db.commit()


@router.post("/providers/{provider_id}/test", response_model=AIProviderTestResponse)
async def test_provider(
    provider_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Check the endpoint answers with the stored credentials."""
    provider = await db.get(AIProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    try:
        if runs_on_agent_sdk(provider):
            # No LLMProvider exists for these — the SDK owns the loop — so
            # they carry their own probe rather than going through
            # build_provider(), which refuses them by design.
            from app.ai.agent_sdk.probe import test_connection as probe_agent_sdk

            message = await probe_agent_sdk(provider)
        else:
            backend = build_provider(provider)
            message = await backend.test_connection()
    except LLMProviderError as exc:
        return AIProviderTestResponse(ok=False, message=str(exc))
    except Exception as exc:
        logger.warning("ai: provider test failed for %s", provider_id, exc_info=True)
        return AIProviderTestResponse(ok=False, message=f"Unexpected error: {exc}")
    return AIProviderTestResponse(ok=True, message=message)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@router.get("/sessions", response_model=list[AISessionResponse])
async def list_sessions(
    limit: int = 50,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AISession).order_by(AISession.created_at.desc()).limit(min(limit, 200))
    )
    return [session_to_response(s) for s in result.scalars().all()]


@router.post("/sessions", response_model=AISessionResponse, status_code=201)
async def create_session(
    payload: AISessionCreate,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a chat session and dispatch it to a worker."""
    try:
        provider = await service.resolve_provider(db, payload.provider_id)
        # A chat session is an investigation by definition, so a backend
        # with no tools cannot serve one — it would answer from imagination.
        service.assert_can_investigate(provider)
        await service.assert_within_budget(db, provider)
    except AIDisabledError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BudgetExceededError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc

    session = AISession(
        provider_id=provider.id,
        mode="chat",
        title=payload.title or payload.mission[:80],
        mission=payload.mission,
        autonomy_level=payload.autonomy_level,
        status="queued",
        target_host_ids=payload.target_host_ids or [],
        skip_snapshots=payload.skip_snapshots,
        created_by_user_id=user.id,
    )
    db.add(session)
    await db.flush()

    # Seed the transcript so a worker can pick the session up with no extra
    # state: the system prompt and mission are already turn 0 and 1.
    from app.ai.loop import build_system_prompt

    await service.append_message(
        db, session.id, role="system", content=build_system_prompt(session.autonomy_level)
    )
    await service.append_message(db, session.id, role="user", content=payload.mission)

    await log_action(
        db,
        action="ai_session_created",
        entity_type="ai_session",
        entity_id=session.id,
        user_id=user.id,
        after_state={
            "autonomy_level": session.autonomy_level,
            "target_host_ids": session.target_host_ids,
            "provider": provider.name,
        },
    )
    await db.commit()
    await db.refresh(session)

    from app.tasks import celery_app

    celery_app.send_task("app.tasks.ai_task.run_chat_session", kwargs={"session_id": session.id})
    return session_to_response(session)


@router.get("/sessions/{session_id}", response_model=AISessionDetail)
async def get_session(
    session_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    session = await db.get(AISession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = (
        (
            await db.execute(
                select(AIMessage).where(AIMessage.session_id == session_id).order_by(AIMessage.seq)
            )
        )
        .scalars()
        .all()
    )
    tool_calls = (
        (
            await db.execute(
                select(AIToolCall)
                .where(AIToolCall.session_id == session_id)
                .order_by(AIToolCall.id)
            )
        )
        .scalars()
        .all()
    )

    requests = (
        (
            await db.execute(
                select(AIApprovalRequest)
                .where(AIApprovalRequest.session_id == session_id)
                .order_by(AIApprovalRequest.id)
            )
        )
        .scalars()
        .all()
    )

    detail = AISessionDetail.model_validate(session)
    # The system prompt is scaffolding, not conversation — showing it in the
    # transcript would bury the actual exchange.
    detail.messages = [m for m in messages if m.role != "system"]
    detail.tool_calls = list(tool_calls)
    detail.approvals = await _with_snapshot_expectation(db, list(requests))
    return detail


@router.post("/sessions/{session_id}/messages", response_model=AISessionResponse)
async def send_message(
    session_id: int,
    payload: AISessionMessageRequest,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a follow-up turn and re-dispatch the session."""
    session = await db.get(AISession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status in ("running", "queued"):
        raise HTTPException(
            status_code=409, detail="The session is still working; wait for it to finish"
        )
    if session.status == "waiting_approval":
        # Accepting a turn here would re-dispatch the session and leave the
        # pending request stranded: nothing would ever act on it, and the
        # command the operator was asked about would silently never run.
        raise HTTPException(
            status_code=409,
            detail=(
                "This session is waiting for your decision on a command. "
                "Approve or reject it, and the session continues from there."
            ),
        )

    try:
        provider = await service.resolve_provider(db, session.provider_id)
        await service.assert_within_budget(db, provider)
    except AIDisabledError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BudgetExceededError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc

    await service.append_message(db, session.id, role="user", content=payload.message)
    session.status = "queued"
    session.finished_at = None
    session.error_message = None
    await db.commit()
    await db.refresh(session)

    from app.tasks import celery_app

    celery_app.send_task("app.tasks.ai_task.run_chat_session", kwargs={"session_id": session.id})
    return session_to_response(session)


@router.post("/sessions/{session_id}/cancel", response_model=AISessionResponse)
async def cancel_session(
    session_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    session = await db.get(AISession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status in TERMINAL_STATES:
        return session_to_response(session)
    session.status = "cancelled"
    session.finished_at = datetime.now(UTC)
    # A cancelled session will never act on a pending request, so leaving
    # one open would park it in the approvals queue forever, inviting a
    # click that silently does nothing. Recorded as expired rather than
    # rejected: nobody weighed the command and said no to it.
    await db.execute(
        update(AIApprovalRequest)
        .where(
            AIApprovalRequest.session_id == session_id,
            AIApprovalRequest.status == "pending",
        )
        .values(
            status="expired",
            decided_at=datetime.now(UTC),
            decision_note="The session was cancelled before anyone decided.",
        )
    )
    await log_action(
        db,
        action="ai_session_cancelled",
        entity_type="ai_session",
        entity_id=session.id,
        user_id=user.id,
    )
    await db.commit()
    await db.refresh(session)
    return session_to_response(session)


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: int,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a session and its transcript.

    Refused while the session is still live. A running session is owned by
    a Celery task that is actively writing to it, and deleting the row out
    from under it turns a working run into a confusing crash. Cancel first,
    then delete.

    Spend is unaffected: the daily ledger in ``ai_usage_days`` is keyed by
    date and provider, not by session, precisely so that accounting
    survives housekeeping like this. Messages and tool calls go with the
    session via ON DELETE CASCADE.
    """
    session = await db.get(AISession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status not in TERMINAL_STATES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This session is {session.status}. Cancel it before deleting, so the "
                "run that owns it can stop cleanly."
            ),
        )

    # Any snapshot this session took is about to lose the row that records
    # it, which is the only thing the retention sweep works from. Naming
    # them here is what stops them becoming invisible as well as orphaned —
    # they are still on the hypervisor, and someone has to be able to find
    # out that they exist.
    orphaned = (
        (
            await db.execute(
                select(AIToolCall.snapshot_name).where(
                    AIToolCall.session_id == session_id,
                    AIToolCall.snapshot_name.is_not(None),
                    AIToolCall.snapshot_pruned_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    # The transcript is about to be unrecoverable, so what it was goes in
    # the audit trail — a deleted investigation should not be invisible.
    await log_action(
        db,
        action="ai_session_deleted",
        entity_type="ai_session",
        entity_id=session.id,
        user_id=user.id,
        before_state={
            "snapshots_left_behind": list(orphaned),
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
    await db.commit()


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


async def _with_snapshot_expectation(
    db: AsyncSession, requests: list[AIApprovalRequest]
) -> list[AIApprovalResponse]:
    """Answer "will this be undoable?" for each request.

    The operator is authorising a change to a host, and whether a rollback
    point will exist is part of what they are deciding — a host with no VM
    mapping gets no snapshot, and saying nothing would let them approve
    under an assumption LabDog knows to be false.

    Resolved in two queries rather than per row: the queue endpoint can
    return up to 200.
    """
    if not requests:
        return []

    enabled = bool(int(await get_setting_typed("ai.snapshot_before_mutating", db)))
    session_ids = {r.session_id for r in requests}
    host_ids = {r.target_host_id for r in requests if r.target_host_id is not None}

    opted_out: set[int] = set()
    if enabled and session_ids:
        rows = await db.execute(
            select(AISession.id).where(
                AISession.id.in_(session_ids), AISession.skip_snapshots.is_(True)
            )
        )
        opted_out = set(rows.scalars().all())

    mapped: set[int] = set()
    if enabled and host_ids:
        from app.proxmox.vm_mapping import VMMapping

        rows = await db.execute(select(VMMapping.host_id).where(VMMapping.host_id.in_(host_ids)))
        mapped = set(rows.scalars().all())

    out: list[AIApprovalResponse] = []
    for request in requests:
        response = AIApprovalResponse.model_validate(request)
        response.snapshot_expected = bool(
            enabled and request.session_id not in opted_out and request.target_host_id in mapped
        )
        out.append(response)
    return out


@router.get("/approvals", response_model=list[AIApprovalResponse])
async def list_approvals(
    status: str = "pending",
    limit: int = 50,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Approval requests, newest first — pending ones by default.

    A queue rather than a per-session lookup because a parked session is
    invisible otherwise: nothing else on the assistant page tells an
    operator that a scheduled run stopped overnight waiting for them.
    """
    stmt = select(AIApprovalRequest).order_by(AIApprovalRequest.id.desc())
    if status != "all":
        stmt = stmt.where(AIApprovalRequest.status == status)
    result = await db.execute(stmt.limit(min(limit, 200)))
    return await _with_snapshot_expectation(db, list(result.scalars().all()))


@router.post("/approvals/{approval_id}", response_model=AIApprovalResponse)
async def decide_approval(
    approval_id: int,
    payload: AIApprovalDecision,
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Approve or reject a paused command, and resume the session.

    Deciding twice is refused rather than ignored. The first decision may
    already have run a command on a host, and a second request that
    quietly returned 200 would leave the operator believing their latest
    click was the one that counted.
    """
    approval = await db.get(AIApprovalRequest, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval request not found")
    if approval.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=(
                f"This request was already {approval.status}"
                + (f" at {approval.decided_at:%Y-%m-%d %H:%M UTC}." if approval.decided_at else ".")
            ),
        )

    session = await db.get(AISession, approval.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="The session this belongs to is gone")
    if session.status != "waiting_approval":
        raise HTTPException(
            status_code=409,
            detail=(
                f"The session is {session.status}, so this request can no longer be "
                f"acted on. Nothing was run."
            ),
        )

    approval.status = "approved" if payload.approve else "rejected"
    approval.decision_note = (payload.note or "").strip() or None
    approval.decided_by_user_id = user.id
    approval.decided_at = datetime.now(UTC)

    # Audited on both branches. An approval is a human authorising a change
    # to a host, which is exactly what the audit trail is for; a rejection
    # is the record that someone looked and said no.
    await log_action(
        db,
        action="ai_approval_approved" if payload.approve else "ai_approval_rejected",
        entity_type="ai_session",
        entity_id=session.id,
        user_id=user.id,
        after_state={
            "approval_id": approval.id,
            "tool_name": approval.tool_name,
            "command": approval.command_preview[:1000],
            "target_host_id": approval.target_host_id,
            "classification": approval.classification,
            "note": approval.decision_note,
        },
    )
    await db.commit()
    await db.refresh(approval)

    from app.tasks import celery_app

    celery_app.send_task("app.tasks.ai_task.resume_session", kwargs={"session_id": session.id})
    return AIApprovalResponse.model_validate(approval)


@router.get("/sessions/{session_id}/stream")
async def stream_session(
    session_id: int,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Relay a session's live events as SSE.

    Same mechanism as ``GET /api/actions/runs/{id}/stream``: the worker
    publishes to a Redis channel, this endpoint forwards it.
    """
    session = await db.get(AISession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    channel = SESSION_CHANNEL.format(id=session_id)
    current_status = session.status

    async def event_generator():
        import redis.asyncio as aioredis

        from app.config import settings

        client = aioredis.from_url(settings.redis.url)
        pubsub = client.pubsub()
        await pubsub.subscribe(channel)
        try:
            if current_status in TERMINAL_STATES:
                yield f"event: status\ndata: {json.dumps({'status': current_status})}\n\n"
                return
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                data = json.loads(message["data"])
                event_type = data.get("event", "text")
                yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
                if event_type == "status" and data.get("status") in TERMINAL_STATES:
                    return
        finally:
            await pubsub.unsubscribe(channel)
            await client.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Usage and budget
# ---------------------------------------------------------------------------


@router.get("/usage", response_model=AIUsageSummary)
async def get_usage(
    days: int = 30,
    _: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Spend against every active limit, plus a per-day breakdown."""
    days = max(1, min(days, 365))
    since = (datetime.now(UTC) - timedelta(days=days)).date()

    rows = (
        await db.execute(
            select(AIUsageDay, AIProvider.name)
            .join(AIProvider, AIProvider.id == AIUsageDay.provider_id, isouter=True)
            .where(AIUsageDay.usage_date >= since)
            .order_by(AIUsageDay.usage_date)
        )
    ).all()

    status = await service.get_budget_status(db, None)
    return AIUsageSummary(
        day_spend=status.day_spend,
        month_spend=status.month_spend,
        day_limit=status.day_limit,
        month_limit=status.month_limit,
        warn_pct=int(await get_setting_typed("ai.budget_warn_pct", db)),
        exceeded=status.exceeded,
        reason=status.reason,
        currency=str(await get_setting_typed("ai.currency", db) or "USD"),
        days=[
            AIUsageDayResponse(
                usage_date=usage.usage_date,
                provider_id=usage.provider_id,
                provider_name=provider_name,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                cost=usage.cost,
                turn_count=usage.turn_count,
            )
            for usage, provider_name in rows
        ],
    )
