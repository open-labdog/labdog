"""The agentic turn loop.

One iteration is: send the transcript to the provider, stream the reply,
execute any tools it asked for, append the results, repeat — until the
model stops asking for tools or a cap stops the loop.

Every exit is bounded. A run ends because the model finished, or because
it hit the iteration, command, token, wall-clock, or budget ceiling; there
is no path where it simply keeps going. That matters more here than in a
chat assistant, because this loop has SSH access to production hosts.

Phase 1 runs to completion in one pass. Phase 3 splits the same loop at
an approval gate by persisting ``AISession.resume_state`` and returning,
so a worker is never blocked on a human.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import service
from app.ai.models import AIProvider, AISession, AIToolCall
from app.ai.providers.base import (
    LLMProvider,
    LLMProviderError,
    NormalizedMessage,
    TextDelta,
    ToolCall,
    ToolCallEnd,
    TurnEnd,
    Usage,
)
from app.ai.providers.factory import build_provider
from app.ai.redaction import redact
from app.ai.tools import TOOL_REGISTRY, ToolContext, tools_for_session
from app.audit.logger import log_action
from app.settings_service import get_setting_typed

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are LabDog's system administration agent, operating on a personal \
homelab. You investigate and report on Linux hosts through the tools you \
have been given.

How to work:
- Start by finding out what is in scope with list_hosts.
- Base every claim on something a tool actually returned. If you did not \
verify something, say so rather than assuming.

Choosing a tool. Each result is read back in full on every later turn, so \
a large one is paid for repeatedly, not once. Narrow first, then look \
closely:
- Counting, comparing, or "which one is worst" — aggregate at the source. \
count_over_time in query_loki, or query_mimir_range, answer these with a \
handful of numbers. Pulling raw lines and counting them yourself is the \
same answer for a hundred times the cost.
- The same question across several hosts — one query_loki or query_mimir \
call spans them all, where run_ssh_command needs a separate call per host.
- Reading a specific file, or checking live state that is not shipped to \
Loki or Mimir — run_ssh_command, bounded. Use --since and -n with \
journalctl and grep or head on anything that could be large.
- Not every tool is offered in every session. Work with what you have; if \
something you need is missing, say so in your findings.

What you may change:
- This session's autonomy level is {autonomy}. {autonomy_note}
- A small set of destructive commands is blocked outright at every level.
- If you believe a change is needed but cannot make it, stop and explain \
what should be done and why. Do not look for a way around the restriction.

How to answer:
- Lead with what you found. Detail and evidence come after.
- Write for an operator reading this later without having watched you \
work: complete sentences, no shorthand you invented along the way.
- When you are done investigating, give your findings as your final \
message with no further tool calls.
"""

AUTONOMY_NOTES = {
    "read_only": (
        "You may only run commands that read state. Any command that would "
        "modify the host will be refused."
    ),
    "approval": (
        "Commands that read state run immediately. Commands that would modify "
        "the host require the operator's approval first."
    ),
    "full_auto": (
        "You may run commands that modify the host. Be conservative: prefer "
        "the smallest change that addresses the problem, and verify the result "
        "afterwards."
    ),
}


@dataclass
class LoopCaps:
    max_iterations: int = 15
    max_commands: int = 20
    max_tokens_total: int = 200_000
    wall_clock_seconds: int = 900

    @classmethod
    async def from_settings(cls, db: AsyncSession) -> LoopCaps:
        return cls(
            max_iterations=int(await get_setting_typed("ai.max_iterations", db)),
            max_commands=int(await get_setting_typed("ai.max_commands", db)),
            max_tokens_total=int(await get_setting_typed("ai.max_tokens_total", db)),
            wall_clock_seconds=int(await get_setting_typed("ai.wall_clock_seconds", db)),
        )


@dataclass
class LoopOutcome:
    status: str
    report: str
    iterations: int
    stopped_by: str = ""


def build_system_prompt(autonomy_level: str) -> str:
    return SYSTEM_PROMPT.format(
        autonomy=autonomy_level,
        autonomy_note=AUTONOMY_NOTES.get(autonomy_level, AUTONOMY_NOTES["read_only"]),
    )


def _to_normalized(rows) -> list[NormalizedMessage]:
    """Rebuild the provider-facing transcript from stored rows."""
    messages: list[NormalizedMessage] = []
    for row in rows:
        calls = [
            ToolCall(id=c["id"], name=c["name"], arguments=c.get("arguments") or {})
            for c in (row.tool_calls or [])
        ]
        messages.append(
            NormalizedMessage(
                role=row.role,
                content=row.content or "",
                tool_calls=calls,
                tool_call_id=row.tool_call_id,
            )
        )
    return messages


class AgentLoop:
    """Drives one :class:`AISession` to completion."""

    def __init__(
        self,
        db: AsyncSession,
        session: AISession,
        provider_row: AIProvider,
        caps: LoopCaps,
        *,
        provider: LLMProvider | None = None,
        publish=None,
    ) -> None:
        self.db = db
        self.session = session
        self.provider_row = provider_row
        self.caps = caps
        # Injectable so tests can drive the loop with a scripted provider.
        self.provider = provider or build_provider(provider_row)
        # Resolved once: the same list gates what the provider is offered
        # and what the loop will actually execute.
        self._handlers = tools_for_session(session.autonomy_level, session.allowed_tools)
        # Async callable taking (event_type, payload) — the SSE bridge.
        self._publish = publish
        self._started = time.monotonic()

    async def _emit(self, event: str, payload: dict) -> None:
        if self._publish is None:
            return
        try:
            await self._publish(event, payload)
        except Exception:
            # A broken stream must not abort the run; the transcript is
            # still being written to the database.
            logger.warning("ai session %s: publish failed", self.session.id, exc_info=True)

    def _elapsed(self) -> float:
        return time.monotonic() - self._started

    async def _cancelled(self) -> bool:
        """Whether an operator cancelled this session since the last check.

        Read straight from the table rather than the ORM object: the cancel
        arrives on a different connection, and this session's identity map
        still holds the value written at the start of the run.
        """
        result = await self.db.execute(
            select(AISession.status).where(AISession.id == self.session.id)
        )
        return result.scalar_one_or_none() == "cancelled"

    def _cap_hit(self) -> str | None:
        if self.session.iterations >= self.caps.max_iterations:
            return f"iteration limit ({self.caps.max_iterations})"
        if self.session.command_count >= self.caps.max_commands:
            return f"command limit ({self.caps.max_commands})"
        total = self.session.prompt_tokens + self.session.completion_tokens
        if total >= self.caps.max_tokens_total:
            return f"token budget ({self.caps.max_tokens_total})"
        if self._elapsed() >= self.caps.wall_clock_seconds:
            return f"time limit ({self.caps.wall_clock_seconds}s)"
        return None

    async def _record_usage(self, usage: Usage) -> None:
        cost = service.estimate_cost(
            self.provider_row, usage.prompt_tokens, usage.completion_tokens
        )
        self.session.prompt_tokens += usage.prompt_tokens
        self.session.completion_tokens += usage.completion_tokens
        self.session.cost += cost
        if usage.unknown:
            self.session.cost_unknown = True
        await service.record_usage(
            self.db,
            provider_id=self.provider_row.id,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cost=cost,
        )
        await self.db.flush()

    async def _run_tool(self, call: ToolCall, ctx: ToolContext) -> str:
        """Execute one tool call, record it, and return the model-facing text."""
        handler = TOOL_REGISTRY.get(call.name)
        started = datetime.now(UTC)

        record = AIToolCall(
            session_id=self.session.id,
            tool_name=call.name,
            arguments=call.arguments,
            classification=handler.classification if handler else "unknown",
            status="proposed",
            started_at=started,
        )
        self.db.add(record)

        # The allowlist is re-checked here, not just when building the tool
        # specs. A model can call a name it was never offered, and this is
        # the only place that actually gates execution.
        permitted = {h.spec.name for h in self._handlers}

        if handler is None or call.name not in permitted:
            record.status = "blocked" if handler is not None else "error"
            record.result_summary = (
                "tool not permitted for this session" if handler is not None else "unknown tool"
            )
            record.finished_at = datetime.now(UTC)
            await self.db.flush()
            available = ", ".join(sorted(permitted))
            if handler is not None:
                return (
                    f"The {call.name} tool is not available to this session. "
                    f"You may use: {available}"
                )
            return f"There is no tool called {call.name!r}. Available tools: {available}"

        await self._emit(
            "tool_call",
            {"name": call.name, "arguments": call.arguments, "call_id": call.id},
        )

        try:
            result = await handler.run(ctx, call.arguments)
        except Exception as exc:
            logger.exception("ai session %s: tool %s failed", self.session.id, call.name)
            record.status = "error"
            record.result_summary = str(exc)[:500]
            record.finished_at = datetime.now(UTC)
            await self.db.flush()
            return f"The {call.name} tool failed: {exc}"

        record.classification = result.classification or handler.classification
        record.target_host_id = result.target_host_id
        record.result_summary = (result.summary or result.content)[:1000]
        # What this call actually cost in context. Recorded per call so the
        # relative expense of tools is observable rather than assumed.
        record.result_chars = len(result.content)
        record.finished_at = datetime.now(UTC)
        record.status = (
            "executed"
            if result.ok
            else ("blocked" if record.classification == "denied" else "error")
        )

        if call.name == "run_ssh_command":
            self.session.command_count += 1
            # Commands against managed hosts belong in the same audit trail
            # as any other change LabDog makes to them.
            await log_action(
                self.db,
                action="ai_command",
                entity_type="host",
                entity_id=result.target_host_id,
                user_id=self.session.created_by_user_id,
                after_state={
                    "session_id": self.session.id,
                    "command": redact(str(call.arguments.get("command", ""))),
                    "classification": record.classification,
                    "status": record.status,
                },
            )

        await self.db.flush()
        await self._emit(
            "tool_result",
            {
                "name": call.name,
                "call_id": call.id,
                "ok": result.ok,
                "classification": record.classification,
                "summary": record.result_summary,
            },
        )
        return result.content

    async def run(self) -> LoopOutcome:
        """Iterate until the model is done or a cap stops it."""
        session = self.session
        session.status = "running"
        session.started_at = session.started_at or datetime.now(UTC)
        # Commit rather than flush: holding the row lock for the whole run
        # would block an operator's Cancel instead of applying it, and would
        # lose the transcript if the worker died mid-session.
        await self.db.commit()

        ctx = ToolContext(
            db=self.db,
            session_id=session.id,
            autonomy_level=session.autonomy_level,
            target_host_ids=list(session.target_host_ids or []),
            action_run_id=session.action_run_id,
            user_id=session.created_by_user_id,
        )
        specs = [h.spec for h in self._handlers] if self.provider.supports_tools else []

        final_text = ""
        stopped_by = ""

        while True:
            if await self._cancelled():
                await self._emit("status", {"status": "cancelled"})
                return LoopOutcome("cancelled", "", session.iterations, "cancelled by operator")

            if cap := self._cap_hit():
                stopped_by = cap
                break

            # Budget is re-checked every iteration, not just at the start: a
            # long run can cross the limit partway through.
            budget = await service.get_budget_status(self.db, self.provider_row)
            if budget.exceeded:
                stopped_by = budget.reason
                break
            warn_pct = int(await get_setting_typed("ai.budget_warn_pct", self.db))
            if warn_pct and budget.warn_fraction() * 100 >= warn_pct:
                await self._emit(
                    "budget_warning",
                    {
                        "message": (
                            f"AI spend is at {budget.warn_fraction() * 100:.0f}% of budget."
                        ),
                        "day_spend": budget.day_spend,
                        "month_spend": budget.month_spend,
                    },
                )

            transcript = _to_normalized(await service.load_transcript(self.db, session.id))
            turn_text: list[str] = []
            tool_calls: list[ToolCall] = []
            turn_end: TurnEnd | None = None

            try:
                async for event in self.provider.stream_turn(
                    transcript,
                    specs,
                    max_tokens=self.provider_row.max_tokens,
                    temperature=self.provider_row.temperature,
                ):
                    if isinstance(event, TextDelta):
                        turn_text.append(event.text)
                        await self._emit("text", {"text": event.text})
                    elif isinstance(event, ToolCallEnd):
                        tool_calls.append(event.call)
                    elif isinstance(event, Usage):
                        await self._record_usage(event)
                    elif isinstance(event, TurnEnd):
                        turn_end = event
            except LLMProviderError as exc:
                logger.warning("ai session %s: provider error: %s", session.id, exc)
                await service.finish_session(self.db, session, status="failed", error=str(exc))
                await self.db.commit()
                await self._emit("error", {"message": str(exc)})
                return LoopOutcome("failed", "", session.iterations, str(exc))

            session.iterations += 1
            text = "".join(turn_text).strip()

            await service.append_message(
                self.db,
                session.id,
                role="assistant",
                content=text,
                tool_calls=[
                    {"id": c.id, "name": c.name, "arguments": c.arguments} for c in tool_calls
                ]
                or None,
            )

            if not tool_calls:
                final_text = text
                if turn_end and turn_end.stop_reason == "max_tokens":
                    stopped_by = "the model's per-turn output limit"
                await self.db.commit()
                break

            for call in tool_calls:
                content = await self._run_tool(call, ctx)
                await service.append_message(
                    self.db,
                    session.id,
                    role="tool",
                    content=content,
                    tool_call_id=call.id,
                )
                if cap := self._cap_hit():
                    stopped_by = cap
                    break

            # Land the turn before the next provider call: the transcript is
            # what a reconnecting UI reads, and what a resumed session would
            # replay if the worker died here.
            await self.db.commit()
            if stopped_by:
                break

        if stopped_by and not final_text:
            final_text = await self._summarize_partial(stopped_by)

        status = "succeeded" if final_text else "failed"
        report = final_text or f"The session stopped early: {stopped_by or 'no output'}."
        if stopped_by:
            report = f"{report}\n\n---\n_Stopped early: {stopped_by}._"

        # A cancel that landed during the final turn wins over the outcome:
        # the operator asked for this to stop, and reporting "succeeded"
        # would misrepresent what happened.
        if await self._cancelled():
            await self._emit("status", {"status": "cancelled"})
            return LoopOutcome("cancelled", report, session.iterations, "cancelled by operator")

        await service.finish_session(self.db, session, status=status, report=report)
        await self.db.commit()
        await self._emit("status", {"status": status, "stopped_by": stopped_by})
        return LoopOutcome(status, report, session.iterations, stopped_by)

    async def _summarize_partial(self, stopped_by: str) -> str:
        """Ask for a wrap-up when a cap cut the run short.

        Without this a capped run returns nothing useful — the operator gets
        a transcript of tool calls and no conclusion. One extra turn with no
        tools offered is a cheap way to salvage the work already paid for.
        """
        transcript = _to_normalized(await service.load_transcript(self.db, self.session.id))
        transcript.append(
            NormalizedMessage(
                role="user",
                content=(
                    f"You have reached this session's limit ({stopped_by}) and cannot "
                    f"run further tools. Summarise what you established, what remains "
                    f"unverified, and what you would do next. Do not call any tools."
                ),
            )
        )
        chunks: list[str] = []
        try:
            async for event in self.provider.stream_turn(
                transcript,
                [],
                max_tokens=self.provider_row.max_tokens,
                temperature=self.provider_row.temperature,
            ):
                if isinstance(event, TextDelta):
                    chunks.append(event.text)
                    await self._emit("text", {"text": event.text})
                elif isinstance(event, Usage):
                    await self._record_usage(event)
        except LLMProviderError as exc:
            logger.info("ai session %s: wrap-up failed: %s", self.session.id, exc)
            return ""
        text = "".join(chunks).strip()
        if text:
            await service.append_message(self.db, self.session.id, role="assistant", content=text)
        return text


def redis_publisher(redis_client, channel: str):
    """Build a publish callable that mirrors the actions SSE channel."""

    async def publish(event: str, payload: dict) -> None:
        redis_client.publish(channel, json.dumps({"event": event, **payload}))

    return publish
