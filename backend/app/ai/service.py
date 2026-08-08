"""Session lifecycle, provider selection, and spend accounting.

Kept separate from the loop so the API layer can create sessions, check
budgets, and read usage without importing the agent machinery.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import AIMessage, AIProvider, AISession, AIUsageDay
from app.ai.providers.factory import sends_data_offsite, supports_tools
from app.settings_service import get_setting_typed

logger = logging.getLogger(__name__)


class AIDisabledError(Exception):
    """The kill switch is off, or policy forbids this provider."""


class BudgetExceededError(Exception):
    """A spend limit would be crossed by continuing."""


@dataclass(frozen=True)
class BudgetStatus:
    day_spend: float
    month_spend: float
    day_limit: float
    month_limit: float
    provider_month_spend: float
    provider_month_limit: float

    @property
    def exceeded(self) -> bool:
        return bool(
            (self.day_limit and self.day_spend >= self.day_limit)
            or (self.month_limit and self.month_spend >= self.month_limit)
            or (
                self.provider_month_limit and self.provider_month_spend >= self.provider_month_limit
            )
        )

    @property
    def reason(self) -> str:
        if self.day_limit and self.day_spend >= self.day_limit:
            return (
                f"Daily AI budget reached: ${self.day_spend:.2f} of "
                f"${self.day_limit:.2f} spent today."
            )
        if self.month_limit and self.month_spend >= self.month_limit:
            return (
                f"Monthly AI budget reached: ${self.month_spend:.2f} of "
                f"${self.month_limit:.2f} spent this month."
            )
        if self.provider_month_limit and self.provider_month_spend >= self.provider_month_limit:
            return (
                f"This provider's monthly budget is reached: "
                f"${self.provider_month_spend:.2f} of "
                f"${self.provider_month_limit:.2f}."
            )
        return ""

    def warn_fraction(self) -> float:
        """Highest fraction of any active limit currently consumed."""
        fractions = [
            self.day_spend / self.day_limit if self.day_limit else 0.0,
            self.month_spend / self.month_limit if self.month_limit else 0.0,
            (
                self.provider_month_spend / self.provider_month_limit
                if self.provider_month_limit
                else 0.0
            ),
        ]
        return max(fractions)


async def get_default_provider(db: AsyncSession) -> AIProvider | None:
    """The provider used when a caller does not name one."""
    result = await db.execute(
        select(AIProvider)
        .where(AIProvider.is_default.is_(True), AIProvider.enabled.is_(True))
        .limit(1)
    )
    if provider := result.scalar_one_or_none():
        return provider
    # A single configured provider is unambiguous even if nobody ticked
    # "default", which is the common homelab case.
    result = await db.execute(
        select(AIProvider).where(AIProvider.enabled.is_(True)).order_by(AIProvider.id).limit(2)
    )
    providers = result.scalars().all()
    return providers[0] if len(providers) == 1 else None


async def resolve_provider(db: AsyncSession, provider_id: int | None) -> AIProvider:
    """Pick the provider for a session and check it against policy."""
    if not int(await get_setting_typed("ai.enabled", db)):
        raise AIDisabledError(
            "AI features are disabled. Enable ai.enabled in Settings to use them."
        )

    if provider_id is not None:
        provider = (
            await db.execute(select(AIProvider).where(AIProvider.id == provider_id))
        ).scalar_one_or_none()
        if provider is None:
            raise AIDisabledError(f"No AI provider with id {provider_id}.")
        if not provider.enabled:
            raise AIDisabledError(f"Provider {provider.name!r} is disabled.")
    else:
        provider = await get_default_provider(db)
        if provider is None:
            raise AIDisabledError(
                "No default AI provider is configured. Add one under Integrations "
                "or mark an existing provider as the default."
            )

    if sends_data_offsite(provider):
        # One gate, deliberately. This used to also require a per-provider
        # `allow_cloud_egress` flag, but the two were set by the same operator
        # in the same session and the second only ever surfaced as a refusal
        # long after the box had been ticked. Per-provider granularity also
        # bought little: once any provider may egress, host data leaves the
        # network, and blocking a second one changes nothing about that.
        if not int(await get_setting_typed("ai.allow_cloud_providers", db)):
            raise AIDisabledError(
                f"Provider {provider.name!r} sends host data off your network. "
                f"Enable ai.allow_cloud_providers in Settings to permit that."
            )
    return provider


def assert_can_investigate(provider: AIProvider) -> None:
    """Refuse an investigation on a backend that cannot run tools.

    Withholding the tools is not enough on its own, and the failure this
    prevents was observed live. A single-shot backend was given the normal
    agent system prompt — which instructs it to "start by finding out what
    is in scope with list_hosts" and to "base every claim on something a
    tool actually returned" — and no tools to do it with. It followed the
    instruction, the CLI rendered the absent result as the word
    ``undefined``, and the model supplied the rest from imagination: a
    host that does not exist, on a subnet one character away from the real
    one, with plausible roles and notes. The session was recorded as
    succeeded.

    Fabricated infrastructure reported as fact is the worst thing this
    feature can do, so a session that cannot verify anything must not
    start at all.
    """
    if supports_tools(provider):
        return
    raise AIDisabledError(
        f"Provider {provider.name!r} cannot run tools, so it cannot look anything "
        f"up — an investigation using it would be guesswork presented as fact. "
        f"Choose an OpenAI-compatible or Anthropic provider for assistant "
        f"sessions."
    )


def estimate_cost(provider: AIProvider, prompt_tokens: int, completion_tokens: int) -> float:
    """Cost of one turn, from the operator-entered per-million rates.

    Unit-agnostic: the result is in whatever currency the operator
    entered the rates in. No conversion happens anywhere in LabDog.
    """
    return (
        prompt_tokens * provider.input_cost_per_mtok
        + completion_tokens * provider.output_cost_per_mtok
    ) / 1_000_000


async def record_usage(
    db: AsyncSession,
    *,
    provider_id: int | None,
    prompt_tokens: int,
    completion_tokens: int,
    cost: float,
) -> None:
    """Add one turn's usage to the daily ledger.

    An upsert rather than read-modify-write so concurrent sessions on the
    same day cannot lose each other's spend.
    """
    today = datetime.now(UTC).date()
    stmt = (
        pg_insert(AIUsageDay)
        .values(
            usage_date=today,
            provider_id=provider_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=cost,
            turn_count=1,
            updated_at=datetime.now(UTC),
        )
        .on_conflict_do_update(
            index_elements=[AIUsageDay.usage_date, AIUsageDay.provider_id],
            set_={
                "prompt_tokens": AIUsageDay.prompt_tokens + prompt_tokens,
                "completion_tokens": AIUsageDay.completion_tokens + completion_tokens,
                "cost": AIUsageDay.cost + cost,
                "turn_count": AIUsageDay.turn_count + 1,
                "updated_at": datetime.now(UTC),
            },
        )
    )
    await db.execute(stmt)


async def _spend_since(db: AsyncSession, start: date, provider_id: int | None = None) -> float:
    stmt = select(func.coalesce(func.sum(AIUsageDay.cost), 0.0)).where(
        AIUsageDay.usage_date >= start
    )
    if provider_id is not None:
        stmt = stmt.where(AIUsageDay.provider_id == provider_id)
    return float((await db.execute(stmt)).scalar_one())


async def get_budget_status(db: AsyncSession, provider: AIProvider | None) -> BudgetStatus:
    """Current spend against every applicable limit."""
    today = datetime.now(UTC).date()
    month_start = today.replace(day=1)
    provider_id = provider.id if provider else None

    return BudgetStatus(
        day_spend=await _spend_since(db, today),
        month_spend=await _spend_since(db, month_start),
        day_limit=float(await get_setting_typed("ai.budget_daily", db) or 0.0),
        month_limit=float(await get_setting_typed("ai.budget_monthly", db) or 0.0),
        provider_month_spend=(
            await _spend_since(db, month_start, provider_id) if provider_id else 0.0
        ),
        provider_month_limit=float(provider.monthly_budget) if provider else 0.0,
    )


async def assert_within_budget(db: AsyncSession, provider: AIProvider) -> BudgetStatus:
    """Raise :class:`BudgetExceededError` if a limit is already reached."""
    status = await get_budget_status(db, provider)
    if status.exceeded:
        raise BudgetExceededError(status.reason)
    return status


async def next_message_seq(db: AsyncSession, session_id: int) -> int:
    result = await db.execute(
        select(func.coalesce(func.max(AIMessage.seq), -1)).where(AIMessage.session_id == session_id)
    )
    return int(result.scalar_one()) + 1


async def append_message(
    db: AsyncSession,
    session_id: int,
    *,
    role: str,
    content: str = "",
    tool_calls: list | None = None,
    tool_call_id: str | None = None,
    tokens: int = 0,
) -> AIMessage:
    """Append one transcript entry, assigning the next sequence number."""
    message = AIMessage(
        session_id=session_id,
        seq=await next_message_seq(db, session_id),
        role=role,
        content=content,
        tool_calls=tool_calls,
        tool_call_id=tool_call_id,
        tokens=tokens,
    )
    db.add(message)
    await db.flush()
    return message


async def load_transcript(db: AsyncSession, session_id: int) -> list[AIMessage]:
    result = await db.execute(
        select(AIMessage).where(AIMessage.session_id == session_id).order_by(AIMessage.seq)
    )
    return list(result.scalars().all())


async def finish_session(
    db: AsyncSession,
    session: AISession,
    *,
    status: str,
    report: str | None = None,
    error: str | None = None,
) -> None:
    session.status = status
    session.finished_at = datetime.now(UTC)
    if report is not None:
        session.report_markdown = report
    if error is not None:
        session.error_message = error
    await db.flush()
