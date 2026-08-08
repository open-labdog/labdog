"""Budget-enforcement tests.

The guarantee under test: once a limit is reached, no new session starts
and a running session stops at the next step. A budget that only warned
would be worse than none, because the operator would believe they had a
cap.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.ai import service
from app.ai.loop import AgentLoop, LoopCaps
from app.ai.models import AIUsageDay
from app.ai.service import AIDisabledError, BudgetExceededError
from app.models.app_setting import AppSetting
from app.settings_service import invalidate_cache
from tests.ai.fake_provider import FakeProvider, ScriptedTurn, call


async def _set(db, key: str, value: str) -> None:
    """Write a setting and drop the in-process cache.

    The cache is keyed by setting name with a 60s TTL, so a test that
    changed a value would otherwise read the previous one.
    """
    existing = (
        await db.execute(select(AppSetting).where(AppSetting.key == key))
    ).scalar_one_or_none()
    if existing:
        existing.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    await db.flush()
    invalidate_cache(key)


async def _spend(db, provider_id: int, cost: float) -> None:
    db.add(
        AIUsageDay(
            usage_date=datetime.now(UTC).date(),
            provider_id=provider_id,
            prompt_tokens=1000,
            completion_tokens=1000,
            cost=cost,
            turn_count=1,
        )
    )
    await db.flush()


@pytest.fixture(autouse=True)
async def _clear_settings_cache():
    invalidate_cache()
    yield
    invalidate_cache()


class TestKillSwitch:
    async def test_ai_disabled_blocks_sessions(self, db, ai_provider):
        await _set(db, "ai.enabled", "0")
        with pytest.raises(AIDisabledError, match="disabled"):
            await service.resolve_provider(db, ai_provider.id)

    async def test_ai_enabled_allows_local_provider(self, db, ai_provider):
        await _set(db, "ai.enabled", "1")
        provider = await service.resolve_provider(db, ai_provider.id)
        assert provider.id == ai_provider.id


class TestEgressPolicy:
    async def test_cloud_provider_blocked_by_default(self, db, paid_provider):
        """A cloud provider stays blocked until policy allows it."""
        await _set(db, "ai.enabled", "1")
        await _set(db, "ai.allow_cloud_providers", "0")
        with pytest.raises(AIDisabledError, match="off your network"):
            await service.resolve_provider(db, paid_provider.id)

    async def test_cloud_provider_allowed_when_both_flags_set(self, db, paid_provider):
        await _set(db, "ai.enabled", "1")
        await _set(db, "ai.allow_cloud_providers", "1")
        provider = await service.resolve_provider(db, paid_provider.id)
        assert provider.id == paid_provider.id

    async def test_global_policy_alone_decides(self, db, paid_provider):
        """The global setting is the only egress gate.

        There used to be a per-provider `allow_cloud_egress` flag as well,
        and both had to agree. It was dropped in 0019: the same operator set
        both in the same sitting, and the second gate only ever announced
        itself as a refusal at session time, long after the box was ticked.
        """
        await _set(db, "ai.enabled", "1")
        await _set(db, "ai.allow_cloud_providers", "0")
        with pytest.raises(AIDisabledError):
            await service.resolve_provider(db, paid_provider.id)

    async def test_the_refusal_names_the_setting_to_change(self, db, paid_provider):
        """A refusal that does not say which switch to flip is what sent an
        operator hunting through the UI for a per-provider checkbox that no
        longer exists."""
        await _set(db, "ai.enabled", "1")
        await _set(db, "ai.allow_cloud_providers", "0")
        with pytest.raises(AIDisabledError) as err:
            await service.resolve_provider(db, paid_provider.id)
        assert "ai.allow_cloud_providers" in str(err.value)

    async def test_local_provider_is_unaffected(self, db, ai_provider):
        await _set(db, "ai.enabled", "1")
        await _set(db, "ai.allow_cloud_providers", "0")
        assert (await service.resolve_provider(db, ai_provider.id)).id == ai_provider.id


class TestBudgetGate:
    async def test_daily_budget_blocks_a_new_session(self, db, paid_provider):
        await _set(db, "ai.budget_daily", "10.0")
        await _spend(db, paid_provider.id, 10.5)
        with pytest.raises(BudgetExceededError, match="Daily"):
            await service.assert_within_budget(db, paid_provider)

    async def test_monthly_budget_blocks_a_new_session(self, db, paid_provider):
        await _set(db, "ai.budget_daily", "0")
        await _set(db, "ai.budget_monthly", "20.0")
        await _spend(db, paid_provider.id, 25.0)
        with pytest.raises(BudgetExceededError, match="Monthly"):
            await service.assert_within_budget(db, paid_provider)

    async def test_per_provider_budget_blocks(self, db, paid_provider):
        await _set(db, "ai.budget_daily", "0")
        await _set(db, "ai.budget_monthly", "0")
        paid_provider.monthly_budget = 5.0
        await _spend(db, paid_provider.id, 6.0)
        with pytest.raises(BudgetExceededError, match="provider"):
            await service.assert_within_budget(db, paid_provider)

    async def test_spend_below_limit_is_allowed(self, db, paid_provider):
        await _set(db, "ai.budget_daily", "10.0")
        await _spend(db, paid_provider.id, 2.0)
        status = await service.assert_within_budget(db, paid_provider)
        assert status.day_spend == pytest.approx(2.0)

    async def test_zero_limit_means_unlimited(self, db, paid_provider):
        await _set(db, "ai.budget_daily", "0")
        await _set(db, "ai.budget_monthly", "0")
        await _spend(db, paid_provider.id, 9999.0)
        status = await service.assert_within_budget(db, paid_provider)
        assert not status.exceeded

    async def test_free_local_provider_ignores_usd_budgets(self, db, ai_provider):
        """A self-hosted model costs nothing, so a USD cap never trips."""
        await _set(db, "ai.budget_daily", "0.01")
        await _spend(db, ai_provider.id, 0.0)
        status = await service.assert_within_budget(db, ai_provider)
        assert not status.exceeded


class TestMidRunEnforcement:
    async def test_a_run_stops_when_it_crosses_the_budget(self, db, paid_provider, make_session):
        """Crossing the limit partway through ends the run, not just new ones."""
        await _set(db, "ai.budget_daily", "5.0")
        session = await make_session(provider=paid_provider)

        # Each turn bills 1M input tokens = $5, so the first turn exhausts it.
        turns = [
            ScriptedTurn(
                tool_calls=[call("list_hosts", f"c{i}")],
                prompt_tokens=1_000_000,
                completion_tokens=0,
            )
            for i in range(5)
        ]
        loop = AgentLoop(
            db,
            session,
            paid_provider,
            LoopCaps(max_iterations=50),
            provider=FakeProvider(turns),
        )
        outcome = await loop.run()

        assert "budget" in outcome.stopped_by.lower()
        assert session.iterations < 5

    async def test_a_run_within_budget_completes(self, db, paid_provider, make_session):
        await _set(db, "ai.budget_daily", "100.0")
        session = await make_session(provider=paid_provider)
        loop = AgentLoop(
            db,
            session,
            paid_provider,
            LoopCaps(),
            provider=FakeProvider([ScriptedTurn(text="All healthy.")]),
        )
        outcome = await loop.run()
        assert outcome.status == "succeeded"
        assert not outcome.stopped_by


class TestBudgetStatus:
    async def test_warn_fraction_reports_the_worst_limit(self, db, paid_provider):
        await _set(db, "ai.budget_daily", "10.0")
        await _set(db, "ai.budget_monthly", "1000.0")
        await _spend(db, paid_provider.id, 9.0)
        status = await service.get_budget_status(db, paid_provider)
        # 90% of the daily limit dominates 0.9% of the monthly one.
        assert status.warn_fraction() == pytest.approx(0.9)

    async def test_reason_is_empty_when_within_budget(self, db, paid_provider):
        await _set(db, "ai.budget_daily", "10.0")
        await _spend(db, paid_provider.id, 1.0)
        status = await service.get_budget_status(db, paid_provider)
        assert status.reason == ""
