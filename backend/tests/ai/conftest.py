"""Fixtures for the AI subsystem tests."""

from __future__ import annotations

import uuid

import pytest

from app.ai.loop import LoopCaps
from app.ai.models import AIProvider, AISession


@pytest.fixture
async def ai_provider(db):
    """A local, free provider — no egress policy, no USD budget effect."""
    provider = AIProvider(
        name=f"test-provider-{uuid.uuid4().hex[:8]}",
        provider_type="openai_compat",
        base_url="http://localhost:11434/v1",
        model="test-model",
        max_tokens=4096,
        temperature=0.0,
        is_default=True,
        enabled=True,
    )
    db.add(provider)
    await db.flush()
    return provider


@pytest.fixture
async def paid_provider(db):
    """A provider with non-zero pricing, so budgets actually engage."""
    provider = AIProvider(
        name=f"paid-provider-{uuid.uuid4().hex[:8]}",
        provider_type="anthropic",
        model="claude-opus-5",
        max_tokens=4096,
        input_cost_per_mtok=5.0,
        output_cost_per_mtok=25.0,
        enabled=True,
    )
    db.add(provider)
    await db.flush()
    return provider


@pytest.fixture
async def make_session(db, ai_provider):
    """Build an AISession with its system+mission turns already seeded."""

    async def _make(
        mission: str = "Check whether nginx is healthy.",
        *,
        autonomy_level: str = "read_only",
        target_host_ids: list[int] | None = None,
        provider: AIProvider | None = None,
    ) -> AISession:
        from app.ai import service
        from app.ai.loop import build_system_prompt

        session = AISession(
            provider_id=(provider or ai_provider).id,
            mode="chat",
            title=mission[:80],
            mission=mission,
            autonomy_level=autonomy_level,
            status="queued",
            target_host_ids=target_host_ids or [],
        )
        db.add(session)
        await db.flush()
        await service.append_message(
            db, session.id, role="system", content=build_system_prompt(autonomy_level)
        )
        await service.append_message(db, session.id, role="user", content=mission)
        return session

    return _make


@pytest.fixture
def small_caps():
    """Tight caps so cap-enforcement tests finish quickly."""
    return LoopCaps(
        max_iterations=3,
        max_commands=2,
        max_tokens_total=10_000,
        wall_clock_seconds=60,
    )
