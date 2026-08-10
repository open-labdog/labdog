"""Drive one :class:`AISession` through the Claude Agent SDK.

The counterpart to :class:`app.ai.loop.AgentLoop`, and deliberately its
peer rather than its subclass: the two agree on what a session *is* — the
same rows, the same SSE events, the same caps and the same tools — and
disagree entirely on who runs the loop. ``AgentLoop`` calls a provider one
turn at a time and decides what happens next. Here the SDK owns that, and
LabDog supplies the tools, the permission decisions, and the bookkeeping.

That inversion is what makes subscription billing possible, and it costs
the loop's natural place to put things. Three consequences are worth
knowing before reading the code:

*Caps become options, not checks.* ``max_iterations`` is handed over as
``max_turns`` because there is no per-iteration seam to test it in. The
wall clock is enforced from outside, as a timeout on the whole exchange.

*Tools run inside the SDK's call stack*, so the ``AIToolCall`` row, the
audit entry and the SSE events are written by :meth:`_execute_tool` rather
than by the driver.

**Every** database touch in this class therefore holds ``_db_lock`` —
including the driver's own, which is the part that is easy to miss. The
SDK dispatches tool callbacks concurrently with the message stream, so
``_execute_tool`` and the loop in :meth:`_exchange` interleave on one
``AsyncSession``, and a session cannot serve two operations at once.
Locking only the tool side was not enough, and the way it failed was
indirect: a tool's ``Session.add()`` landed mid-flush (SQLAlchemy warns,
then carries on), the next query died with asyncpg's *another operation
is in progress*, and what reached the operator was the third-order
symptom — ``This session is in 'prepared' state`` — from the error
handler trying to record the failure on a transaction that was already
unusable.

The one exception is :meth:`run` outside its ``_exchange`` call: no
client exists there, so no callback can fire.

*Refusals are decided before dispatch* by :meth:`_can_use_tool`, which is
the only place autonomy is enforced on this path. A refusal still gets an
``AIToolCall`` row, because "the model tried to restart nginx and was
stopped" is exactly what an operator needs to see afterwards.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import service
from app.ai.agent_sdk.bridge import NO_BUILTIN_TOOLS, build_tool_server, local_tool_name
from app.ai.agent_sdk.environment import build_sdk_env, ensure_state_dir
from app.ai.agent_sdk.gate import decide
from app.ai.loop import LoopCaps, LoopOutcome, build_system_prompt
from app.ai.models import AIProvider, AISession, AIToolCall
from app.ai.providers.base import Usage
from app.ai.providers.claude_cli import DEFAULT_CONFIG_DIR
from app.ai.providers.factory import decrypt_api_key
from app.ai.redaction import redact
from app.ai.tools import ToolContext, ToolHandler, ToolResult, tools_for_session
from app.audit.logger import log_action
from app.settings_service import get_setting_typed

logger = logging.getLogger(__name__)

#: Where the CLI keeps its own session files. ``resume`` reads them, so a
#: deployment that wants to park a session across a container restart has
#: to persist this path — it is under LabDog's state root for that reason.
SESSION_STATE_DIR = DEFAULT_CONFIG_DIR


class AgentSDKRunner:
    """Runs one session on the Claude Agent SDK."""

    def __init__(
        self,
        db: AsyncSession,
        session: AISession,
        provider_row: AIProvider,
        caps: LoopCaps,
        *,
        publish: Any = None,
        client_factory: Any = None,
    ) -> None:
        self.db = db
        self.session = session
        self.provider_row = provider_row
        self.caps = caps
        self._publish = publish
        # Injectable so tests can drive a scripted client instead of
        # spawning the real binary.
        self._client_factory = client_factory
        self._handlers: list[ToolHandler] = tools_for_session(
            session.autonomy_level, session.allowed_tools
        )
        self._permitted = {h.spec.name: h for h in self._handlers}
        # One database session cannot serve two concurrent tool calls, and
        # a turn may request several.
        self._db_lock = asyncio.Lock()
        # Correlates a permitted call with the row written when it runs.
        # The gate fires immediately before dispatch, so the most recent
        # id for a tool name is that call's.
        self._tool_use_ids: dict[str, str] = {}
        self._stopped_by = ""

    # -- plumbing ---------------------------------------------------------

    async def _emit(self, event: str, payload: dict) -> None:
        if self._publish is None:
            return
        try:
            await self._publish(event, payload)
        except Exception:
            # A broken stream must not abort the run; the transcript is
            # still being written to the database.
            logger.warning("ai session %s: publish failed", self.session.id, exc_info=True)

    async def _cancelled(self) -> bool:
        """Whether an operator cancelled since the last check.

        Read from the table, not the ORM object: the cancel arrives on a
        different connection and this session's identity map still holds
        the value written when the run started.
        """
        async with self._db_lock:
            result = await self.db.execute(
                select(AISession.status).where(AISession.id == self.session.id)
            )
        return result.scalar_one_or_none() == "cancelled"

    async def _record_usage(self, usage: dict | None) -> None:
        """Fold one result's token counts into the session and the ledger."""
        if not usage:
            self.session.cost_unknown = True
            return
        # Cache reads and cache writes are input tokens that were really
        # sent, so they count against the token cap even though they are
        # priced differently. Undercounting here would let a cached session
        # run past a limit an uncached one would hit.
        prompt = (
            int(usage.get("input_tokens") or 0)
            + int(usage.get("cache_creation_input_tokens") or 0)
            + int(usage.get("cache_read_input_tokens") or 0)
        )
        completion = int(usage.get("output_tokens") or 0)
        event = Usage(prompt_tokens=prompt, completion_tokens=completion)
        cost = service.estimate_cost(self.provider_row, prompt, completion)
        async with self._db_lock:
            self.session.prompt_tokens += event.prompt_tokens
            self.session.completion_tokens += event.completion_tokens
            self.session.cost += cost
            await service.record_usage(
                self.db,
                provider_id=self.provider_row.id,
                prompt_tokens=event.prompt_tokens,
                completion_tokens=event.completion_tokens,
                cost=cost,
            )
            await self.db.flush()

    # -- permission gate --------------------------------------------------

    async def _can_use_tool(self, tool_name: str, arguments: dict, context: Any):
        """Decide one call, before the SDK dispatches it.

        The SDK honours a denial by never entering the tool, so this is
        where autonomy is enforced on this path.
        """
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        verdict = decide(
            tool_name,
            arguments or {},
            permitted=self._permitted,
            autonomy_level=self.session.autonomy_level,
        )

        if verdict.allowed:
            tool_use_id = getattr(context, "tool_use_id", None)
            if tool_use_id:
                self._tool_use_ids[local_tool_name(tool_name)] = tool_use_id
            return PermissionResultAllow()

        # A refused call is part of the record. Without this the transcript
        # shows the model changing the subject for no visible reason.
        async with self._db_lock:
            name = local_tool_name(tool_name)
            self.db.add(
                AIToolCall(
                    session_id=self.session.id,
                    tool_name=name,
                    arguments=arguments or {},
                    classification=verdict.classification,
                    status="blocked",
                    result_summary=verdict.reason[:1000],
                    started_at=datetime.now(UTC),
                    finished_at=datetime.now(UTC),
                )
            )
            await self.db.commit()
        await self._emit(
            "tool_result",
            {
                "name": name,
                "ok": False,
                "classification": verdict.classification,
                "summary": verdict.reason,
            },
        )
        return PermissionResultDeny(message=verdict.reason)

    # -- tool execution ---------------------------------------------------

    async def _execute_tool(self, handler: ToolHandler, arguments: dict) -> ToolResult:
        """Run one permitted call and record everything about it."""
        name = handler.spec.name
        async with self._db_lock:
            started = datetime.now(UTC)
            record = AIToolCall(
                session_id=self.session.id,
                tool_name=name,
                arguments=arguments,
                classification=handler.classification,
                status="proposed",
                started_at=started,
            )
            self.db.add(record)
            await self.db.flush()

            await self._emit("tool_call", {"name": name, "arguments": arguments})

            try:
                result = await handler.run(self._ctx, arguments)
            except Exception as exc:
                logger.exception("ai session %s: tool %s failed", self.session.id, name)
                record.status = "error"
                record.result_summary = str(exc)[:500]
                record.finished_at = datetime.now(UTC)
                await self.db.commit()
                return ToolResult(f"The {name} tool failed: {exc}", ok=False)

            record.classification = result.classification or handler.classification
            record.target_host_id = result.target_host_id
            record.result_summary = (result.summary or result.content)[:1000]
            record.result_chars = len(result.content)
            record.finished_at = datetime.now(UTC)
            record.status = (
                "executed"
                if result.ok
                else ("blocked" if record.classification == "denied" else "error")
            )

            if name == "run_ssh_command":
                self.session.command_count += 1
                # Commands against managed hosts belong in the same audit
                # trail as any other change LabDog makes to them.
                await log_action(
                    self.db,
                    action="ai_command",
                    entity_type="host",
                    entity_id=result.target_host_id,
                    user_id=self.session.created_by_user_id,
                    after_state={
                        "session_id": self.session.id,
                        "command": redact(str(arguments.get("command", ""))),
                        "classification": record.classification,
                        "status": record.status,
                    },
                )

            await service.append_message(
                self.db,
                self.session.id,
                role="tool",
                content=result.content,
                tool_call_id=self._tool_use_ids.pop(name, None),
            )
            await self.db.commit()

        await self._emit(
            "tool_result",
            {
                "name": name,
                "ok": result.ok,
                "classification": record.classification,
                "summary": record.result_summary,
            },
        )
        return result

    # -- the run ----------------------------------------------------------

    def _build_options(self) -> Any:
        from claude_agent_sdk import ClaudeAgentOptions

        server = build_tool_server(self._handlers, self._execute_tool)
        resume = (self.session.resume_state or {}).get("sdk_session_id")

        return ClaudeAgentOptions(
            mcp_servers={"labdog": server},
            # Only the tools LabDog built. Omitting this hands the model
            # Bash, Read, Write and Edit inside LabDog's container.
            tools=NO_BUILTIN_TOOLS,
            strict_mcp_config=True,
            # Do not read the host's own Claude Code configuration: a
            # CLAUDE.md or an MCP server belonging to whoever administers
            # the box is not part of LabDog's threat model.
            setting_sources=[],
            # Deliberately empty. An entry here would auto-approve that
            # tool *before* can_use_tool is consulted, silently disabling
            # the gate below.
            allowed_tools=[],
            can_use_tool=self._can_use_tool,
            system_prompt=build_system_prompt(self.session.autonomy_level),
            model=self.provider_row.model or None,
            max_turns=self.caps.max_iterations,
            env=build_sdk_env(decrypt_api_key(self.provider_row), SESSION_STATE_DIR),
            cwd=ensure_state_dir(SESSION_STATE_DIR),
            resume=resume,
        )

    async def run(self) -> LoopOutcome:
        """Drive the session to completion."""
        session = self.session

        budget = await service.get_budget_status(self.db, self.provider_row)
        if budget.exceeded:
            await service.finish_session(self.db, session, status="failed", error=budget.reason)
            await self.db.commit()
            await self._emit("error", {"message": budget.reason})
            return LoopOutcome("failed", "", 0, budget.reason)

        session.status = "running"
        session.started_at = session.started_at or datetime.now(UTC)
        # Commit rather than flush: holding the row lock for the whole run
        # would block an operator's Cancel instead of applying it.
        await self.db.commit()

        self._ctx = ToolContext(
            db=self.db,
            session_id=session.id,
            autonomy_level=session.autonomy_level,
            target_host_ids=list(session.target_host_ids or []),
            action_run_id=session.action_run_id,
            user_id=session.created_by_user_id,
        )

        final_text = ""
        try:
            async with asyncio.timeout(self.caps.wall_clock_seconds):
                final_text = await self._exchange()
        except TimeoutError:
            self._stopped_by = f"time limit ({self.caps.wall_clock_seconds}s)"
        except Exception as exc:
            logger.exception("ai session %s: agent sdk run failed", session.id)
            message = str(exc) or exc.__class__.__name__
            await service.finish_session(self.db, session, status="failed", error=message)
            await self.db.commit()
            await self._emit("error", {"message": message})
            return LoopOutcome("failed", "", session.iterations, message)

        if await self._cancelled():
            await self._emit("status", {"status": "cancelled"})
            return LoopOutcome("cancelled", final_text, session.iterations, "cancelled by operator")

        status = "succeeded" if final_text else "failed"
        report = final_text or f"The session stopped early: {self._stopped_by or 'no output'}."
        if self._stopped_by:
            report = f"{report}\n\n---\n_Stopped early: {self._stopped_by}._"

        await service.finish_session(self.db, session, status=status, report=report)
        await self.db.commit()
        await self._emit("status", {"status": status, "stopped_by": self._stopped_by})
        return LoopOutcome(status, report, session.iterations, self._stopped_by)

    async def _exchange(self) -> str:
        """One prompt in, the assistant's final text out."""
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

        session = self.session
        options = self._build_options()
        factory = self._client_factory
        if factory is None:
            from claude_agent_sdk import ClaudeSDKClient

            factory = ClaudeSDKClient

        final_text = ""
        async with factory(options=options) as client:
            await client.query(session.mission)

            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    text = "".join(
                        block.text for block in message.content if isinstance(block, TextBlock)
                    ).strip()
                    session.iterations += 1
                    if text:
                        final_text = text
                        await self._emit("text", {"text": text})
                        async with self._db_lock:
                            await service.append_message(
                                self.db, session.id, role="assistant", content=text
                            )
                            await self.db.commit()

                    if await self._cancelled():
                        await client.interrupt()
                        break
                    if cap := self._cap_hit() or await self._budget_hit():
                        self._stopped_by = cap
                        await client.interrupt()
                        break

                elif isinstance(message, ResultMessage):
                    await self._record_usage(message.usage)
                    # Keep the CLI's own session id so an approval can park
                    # this run and resume it in a later process.
                    async with self._db_lock:
                        session.resume_state = {"sdk_session_id": message.session_id}
                        if message.is_error and not final_text:
                            self._stopped_by = str(
                                message.result or "the backend reported an error"
                            )
                        await self.db.commit()

        return final_text

    async def _budget_hit(self) -> str | None:
        """Re-check spend between turns, and warn before it bites.

        A long run can cross the limit partway through, so the check at
        the start of :meth:`run` is not sufficient on its own.
        """
        async with self._db_lock:
            budget = await service.get_budget_status(self.db, self.provider_row)
            warn_pct = int(await get_setting_typed("ai.budget_warn_pct", self.db))
        if budget.exceeded:
            return budget.reason
        if warn_pct and budget.warn_fraction() * 100 >= warn_pct:
            await self._emit(
                "budget_warning",
                {
                    "message": f"AI spend is at {budget.warn_fraction() * 100:.0f}% of budget.",
                    "day_spend": budget.day_spend,
                    "month_spend": budget.month_spend,
                },
            )
        return None

    def _cap_hit(self) -> str | None:
        """Caps the SDK cannot enforce for us.

        ``max_iterations`` is absent: it is handed to the SDK as
        ``max_turns``, and re-checking it here would race with the SDK's
        own count for no benefit.
        """
        if self.session.command_count >= self.caps.max_commands:
            return f"command limit ({self.caps.max_commands})"
        total = self.session.prompt_tokens + self.session.completion_tokens
        if total >= self.caps.max_tokens_total:
            return f"token budget ({self.caps.max_tokens_total})"
        return None
