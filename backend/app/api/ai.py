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
from app.ai.models import AIMessage, AIProvider, AISession, AIToolCall, AIUsageDay
from app.ai.providers.base import LLMProviderError
from app.ai.providers.factory import build_provider
from app.ai.schemas import (
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
        allow_cloud_egress=payload.allow_cloud_egress,
        input_cost_per_mtok=payload.input_cost_per_mtok,
        output_cost_per_mtok=payload.output_cost_per_mtok,
        monthly_budget_usd=payload.monthly_budget_usd,
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

    detail = AISessionDetail.model_validate(session)
    # The system prompt is scaffolding, not conversation — showing it in the
    # transcript would bury the actual exchange.
    detail.messages = [m for m in messages if m.role != "system"]
    detail.tool_calls = list(tool_calls)
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
        days=[
            AIUsageDayResponse(
                usage_date=usage.usage_date,
                provider_id=usage.provider_id,
                provider_name=provider_name,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                cost_usd=usage.cost_usd,
                turn_count=usage.turn_count,
            )
            for usage, provider_name in rows
        ],
    )
