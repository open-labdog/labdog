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

from app.ai import approvals, service
from app.ai.agent_sdk.bridge import NO_BUILTIN_TOOLS, build_tool_server, local_tool_name
from app.ai.agent_sdk.environment import build_sdk_env, ensure_state_dir
from app.ai.gate import decide
from app.ai.loop import LoopCaps, LoopOutcome, build_system_prompt
from app.ai.models import AIApprovalRequest, AIProvider, AISession, AIToolCall
from app.ai.providers.claude_cli import DEFAULT_CONFIG_DIR
from app.ai.providers.factory import decrypt_api_key
from app.ai.redaction import redact
from app.ai.snapshots import snapshot_if_mutating
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
        prompt: str | None = None,
    ) -> None:
        self.db = db
        self.session = session
        self.provider_row = provider_row
        self.caps = caps
        # Running token estimate, summed from each assistant message so the
        # token cap has something to test mid-run. See `_note_live_usage`
        # for why these are separate from the session's own counters.
        self._live_prompt = 0
        self._live_completion = 0
        # Whether this run's tokens reached the ledger. An interrupted run
        # never gets a ResultMessage, so without this the exits that stop a
        # run early would book nothing at all.
        self._usage_booked = False
        self._publish = publish
        # What to send. Normally the mission; on resume, the operator's
        # decision, with ``resume_state`` restoring the rest of the
        # conversation from the CLI's own session file.
        self._prompt = prompt
        # Injectable so tests can drive a scripted client instead of
        # spawning the real binary.
        self._client_factory = client_factory
        self._handlers: list[ToolHandler] = tools_for_session(
            session.autonomy_level, session.allowed_tools, mode=session.mode
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
        # Set when a call needs an operator's decision. The SDK has no way
        # to suspend a turn, so the gate refuses the call and this flag
        # tells the driver to stop the exchange rather than let the model
        # go looking for another route to the same change.
        self._parked: AIApprovalRequest | None = None
        # Loaded from the transcript in run(); see _load_system_prompt.
        self._system_prompt = ""

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

    @staticmethod
    def _usage_totals(usage: dict) -> tuple[int, int]:
        """Split one usage block into (prompt, completion).

        Cache reads and cache writes are input tokens that were really
        sent, so they count against the token cap even though they are
        priced differently. Undercounting here would let a cached session
        run past a limit an uncached one would hit.
        """
        prompt = (
            int(usage.get("input_tokens") or 0)
            + int(usage.get("cache_creation_input_tokens") or 0)
            + int(usage.get("cache_read_input_tokens") or 0)
        )
        return prompt, int(usage.get("output_tokens") or 0)

    def _note_live_usage(self, usage: dict | None) -> None:
        """Accumulate a running token estimate from one assistant message.

        The token cap was unenforceable without this. ``ResultMessage`` is
        the SDK's *terminal* message, so folding usage in only there left
        ``session.prompt_tokens`` at 0 for the entire run: ``_cap_hit``
        compared 0 against the limit every turn, never tripped, and the
        real figure landed when there was nothing left to stop. A run
        capped at 10,000 tokens finished having spent 111,857.

        Each ``AssistantMessage`` carries the raw per-response usage from
        the API (``data["message"]["usage"]``), so summing them gives a
        live signal that only ever grows.

        Deliberately kept out of the session row and the ledger. This is an
        estimate assembled from a different source than the CLI's own
        aggregate, and the two need not agree; writing it into the columns
        that back cost reporting would make spend figures depend on which
        message type happened to arrive last. It bounds the run, and
        ``ResultMessage`` remains the only thing that books it.
        """
        if not usage:
            return
        prompt, completion = self._usage_totals(usage)
        self._live_prompt += prompt
        self._live_completion += completion

    async def _book(self, prompt: int, completion: int, *, estimated: bool) -> None:
        """Write tokens and cost onto the session and the daily ledger."""
        cost = service.estimate_cost(self.provider_row, prompt, completion)
        async with self._db_lock:
            self.session.prompt_tokens += prompt
            self.session.completion_tokens += completion
            self.session.cost += cost
            if estimated:
                self.session.cost_unknown = True
            await service.record_usage(
                self.db,
                provider_id=self.provider_row.id,
                prompt_tokens=prompt,
                completion_tokens=completion,
                cost=cost,
            )
            await self.db.flush()
        self._usage_booked = True

    async def _book_estimate_if_unbooked(self) -> None:
        """Fall back to the live estimate when no ``ResultMessage`` arrived.

        Interrupting the SDK — for a cap, a cancellation, or an approval
        gate — ends the exchange without its terminal message, and that
        message is the only thing that books a run. So the runs which hit a
        limit were precisely the ones missing from the usage panel: session
        15 stopped on its token budget having spent real tokens and
        recorded zero, and the daily ledger had no row for the day at all.

        Booked as ``cost_unknown`` because this is the estimate rather than
        the CLI's own aggregate. Understating a stopped run as free is
        worse than booking an approximation and saying it is one.

        Safe on a parked session that resumes later: the resumed run is a
        separate SDK exchange with its own terminal message covering only
        its own turns, so this cannot double count the earlier leg.
        """
        if self._usage_booked:
            return
        if not (self._live_prompt or self._live_completion):
            return
        logger.info(
            "ai session %s: no result message; booking the live estimate of %d tokens",
            self.session.id,
            self._live_prompt + self._live_completion,
        )
        await self._book(self._live_prompt, self._live_completion, estimated=True)

    async def _record_usage(self, usage: dict | None) -> None:
        """Fold one result's token counts into the session and the ledger."""
        if not usage:
            self.session.cost_unknown = True
            return
        prompt, completion = self._usage_totals(usage)
        # Both numbers, once, where they can be compared. The live estimate
        # gates the cap while the run is in flight but is never booked, so
        # a drift between them is invisible in the data — and a cap that
        # fires early or late is exactly what that drift would look like to
        # an operator.
        logger.info(
            "ai session %s usage: authoritative=%d live_estimate=%d",
            self.session.id,
            prompt + completion,
            self._live_prompt + self._live_completion,
        )
        await self._book(prompt, completion, estimated=False)

    # -- permission gate --------------------------------------------------

    async def _can_use_tool(self, tool_name: str, arguments: dict, context: Any):
        """Decide one call, before the SDK dispatches it.

        The SDK honours a denial by never entering the tool, so this is
        where autonomy is enforced on this path.
        """
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        name = local_tool_name(tool_name)
        verdict = decide(
            name,
            arguments or {},
            permitted=self._permitted,
            autonomy_level=self.session.autonomy_level,
        )

        if verdict.allowed:
            tool_use_id = getattr(context, "tool_use_id", None)
            if tool_use_id:
                self._tool_use_ids[name] = tool_use_id
            return PermissionResultAllow()

        # Refused only because nobody has said yes yet. Park the session
        # rather than answering "no": the run ends here, the worker is
        # freed, and a decision restarts it.
        if verdict.needs_approval and verdict.verdict is not None:
            async with self._db_lock:
                approval = await approvals.park(
                    self.db,
                    self.session,
                    tool_name=name,
                    arguments=arguments or {},
                    verdict=verdict.verdict,
                )
                await self.db.commit()
            self._parked = approval
            await self._emit(
                "approval_required",
                {
                    "approval_id": approval.id,
                    "tool_name": approval.tool_name,
                    "command_preview": approval.command_preview,
                    "target_host_id": approval.target_host_id,
                    "reason": approval.reason,
                    "summary": approval.summary,
                },
            )
            return PermissionResultDeny(message=approvals.PARKED_RESULT)

        # A refused call is part of the record. Without this the transcript
        # shows the model changing the subject for no visible reason.
        async with self._db_lock:
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

            # Re-derived rather than carried over from the permission
            # callback: `verdict_for` is pure, and threading state between
            # two points in the SDK's own call stack would be one more
            # thing to get wrong under concurrent tool dispatch.
            snapshot_name, refusal = await snapshot_if_mutating(
                self.db,
                classification=handler.verdict_for(arguments).classification,
                arguments=arguments,
                session_id=self.session.id,
                label=str(arguments.get("command") or name),
                skip=self.session.skip_snapshots,
            )
            if refusal:
                record.status = "blocked"
                record.result_summary = refusal[:1000]
                record.finished_at = datetime.now(UTC)
                await self.db.commit()
                return ToolResult(refusal, ok=False)
            record.snapshot_name = snapshot_name

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

    def _build_options(self, *, with_tools: bool = True, max_turns: int | None = None) -> Any:
        """Options for one exchange.

        ``with_tools=False`` builds the wrap-up variant: no MCP server at
        all, so the model cannot answer a request for a summary by going
        and looking something else up.
        """
        from claude_agent_sdk import ClaudeAgentOptions

        # No handlers means no server, not an empty one: a verify session
        # is toolless by design (see TOOLLESS_MODES), and advertising an
        # MCP server with nothing in it invites the model to go looking
        # for what it should have.
        servers = (
            {"labdog": build_tool_server(self._handlers, self._execute_tool)}
            if with_tools and self._handlers
            else {}
        )
        resume = (self.session.resume_state or {}).get("sdk_session_id")

        return ClaudeAgentOptions(
            mcp_servers=servers,
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
            system_prompt=self._system_prompt or build_system_prompt(self.session.autonomy_level),
            model=self.provider_row.model or None,
            max_turns=max_turns or self.caps.max_iterations,
            env=build_sdk_env(decrypt_api_key(self.provider_row), SESSION_STATE_DIR),
            cwd=ensure_state_dir(SESSION_STATE_DIR),
            resume=resume,
        )

    async def _load_system_prompt(self) -> str:
        """The system prompt this session was created with.

        Read from the transcript rather than rebuilt. Every creator
        already writes it as the first message, so rebuilding it here
        made the same prompt exist in two places that could disagree —
        and they would have: a verify session carries its own prompt
        (:mod:`app.ai.verify`), and this path would have handed it the
        chat agent's instead. That prompt tells the model to "start by
        finding out what is in scope with list_hosts" and to base every
        claim on a tool result, to a session that has no tools, which is
        precisely the fabrication ``assert_can_investigate`` exists to
        prevent.

        Falls back to the chat prompt when there is no system row, which
        should not happen but is survivable: an odd prompt beats a
        session that cannot start.
        """
        async with self._db_lock:
            rows = await service.load_transcript(self.db, self.session.id)
        for row in rows:
            if row.role == "system" and row.content:
                return row.content
        logger.warning(
            "ai session %s: no system message in the transcript; using the default prompt",
            self.session.id,
        )
        return ""

    def _stop_reason(self, message: Any) -> str:
        """Why the SDK ended the exchange, in words, or "" if it just finished.

        Read from ``subtype`` rather than ``is_error`` alone. A run that
        hits the turn limit *after* producing some text is still truncated,
        and the earlier version only recorded a stop reason when there was
        no text at all — so a session that was cut off mid-investigation
        was badged succeeded, and its last half-finished sentence ("Now let
        me check network and CPU details") was shown to the operator as the
        report. That is the fabrication failure wearing different clothes:
        incomplete work presented as a conclusion.
        """
        subtype = (getattr(message, "subtype", "") or "").strip()
        if subtype == "error_max_turns":
            return f"the turn limit ({self.caps.max_iterations})"
        if subtype.startswith("error"):
            detail = str(getattr(message, "result", "") or "").strip()
            return detail or subtype.replace("_", " ")
        if getattr(message, "is_error", False):
            status = getattr(message, "api_error_status", None)
            detail = str(getattr(message, "result", "") or "").strip()
            if status:
                return f"a provider error (HTTP {status})"
            return detail or "the backend reported an error"
        return ""

    async def _summarise_partial(self) -> str:
        """Ask for a conclusion when the run was cut short.

        Without this the report is whatever the model happened to be
        saying when it ran out of turns, which reads as a finding rather
        than as an interrupted thought. One extra turn with no tools
        attached is a cheap way to salvage work already paid for — the
        same trade AgentLoop makes for the same reason.
        """
        from claude_agent_sdk import AssistantMessage, TextBlock

        factory = self._client_factory
        if factory is None:
            from claude_agent_sdk import ClaudeSDKClient

            factory = ClaudeSDKClient

        prompt = (
            f"You have reached this session's limit ({self._stopped_by}) and cannot "
            f"run any further tools. Summarise what you established, what remains "
            f"unverified, and what you would do next. Do not call any tools."
        )
        text: list[str] = []
        try:
            async with asyncio.timeout(self.caps.wall_clock_seconds):
                async with factory(
                    options=self._build_options(with_tools=False, max_turns=1)
                ) as client:
                    await client.query(prompt)
                    async for message in client.receive_response():
                        if isinstance(message, AssistantMessage):
                            text.append(
                                "".join(b.text for b in message.content if isinstance(b, TextBlock))
                            )
        except Exception:
            # Best effort. A failed wrap-up must not turn a truncated run
            # into a failed one — the transcript is still there.
            logger.warning(
                "ai session %s: wrap-up after %s failed",
                self.session.id,
                self._stopped_by,
                exc_info=True,
            )
            return ""
        return "".join(text).strip()

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

        self._system_prompt = await self._load_system_prompt()

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
            await self._book_estimate_if_unbooked()
            await self.db.commit()
            await self._emit("status", {"status": "cancelled"})
            return LoopOutcome("cancelled", final_text, session.iterations, "cancelled by operator")

        # Parked, not finished: no report, no finished_at, and deliberately
        # no wrap-up turn. The investigation is not over — it is waiting on
        # a person — and summarising it now would present an interrupted
        # run as a conclusion.
        if self._parked is not None:
            await self._book_estimate_if_unbooked()
            async with self._db_lock:
                session.status = "waiting_approval"
                await self.db.commit()
            await self._emit("status", {"status": "waiting_approval"})
            return LoopOutcome(
                "waiting_approval",
                final_text,
                session.iterations,
                "waiting for operator approval",
            )

        # A truncated run's last message is whatever the model was saying
        # when it ran out of turns — mid-sentence, mid-investigation, and
        # indistinguishable from a conclusion once it is sitting in the
        # report pane. Ask for a real one instead.
        if self._stopped_by:
            if wrapped := await self._summarise_partial():
                final_text = wrapped
                await self._emit("text", {"text": wrapped})
                async with self._db_lock:
                    await service.append_message(
                        self.db, session.id, role="assistant", content=wrapped
                    )
                    await self.db.commit()

        status = "succeeded" if final_text else "failed"
        report = final_text or f"The session stopped early: {self._stopped_by or 'no output'}."
        if self._stopped_by:
            report = f"{report}\n\n---\n_Stopped early: {self._stopped_by}._"

        await self._book_estimate_if_unbooked()
        await service.finish_session(self.db, session, status=status, report=report)
        await self.db.commit()
        await self._emit("status", {"status": status, "stopped_by": self._stopped_by})
        return LoopOutcome(status, report, session.iterations, self._stopped_by)

    async def _exchange(self) -> str:
        """One prompt in, the assistant's final text out."""
        from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, TextBlock

        session = self.session
        options = self._build_options()
        factory = self._client_factory
        if factory is None:
            from claude_agent_sdk import ClaudeSDKClient

            factory = ClaudeSDKClient

        final_text = ""
        async with factory(options=options) as client:
            await client.query(self._prompt or session.mission)

            async for message in client.receive_response():
                if isinstance(message, SystemMessage):
                    # The CLI announces its session id in the `init`
                    # message, before any tool runs. Taking it here rather
                    # than only from the ResultMessage is what makes an
                    # approval survivable: parking interrupts the exchange,
                    # and an interrupted exchange is exactly the case where
                    # a final result may never arrive.
                    await self._remember_sdk_session(message)
                    continue

                if isinstance(message, AssistantMessage):
                    # Before the cap check below, which reads it. This turn's
                    # tokens are already spent by the time the message
                    # arrives, so counting them now is what lets the next
                    # turn be refused.
                    self._note_live_usage(getattr(message, "usage", None))
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

                    if self._parked is not None:
                        # The gate already refused the call. Stopping here
                        # too is what makes it a pause rather than a no:
                        # left running, the model would spend the rest of
                        # its turns working around the refusal, and an
                        # operator approving thirty seconds later would
                        # find the session had moved on without them.
                        await client.interrupt()
                        break
                    if await self._cancelled():
                        await client.interrupt()
                        break
                    if cap := self._cap_hit() or await self._budget_hit():
                        self._stopped_by = cap
                        await client.interrupt()
                        break

                elif isinstance(message, ResultMessage):
                    await self._record_usage(message.usage)
                    async with self._db_lock:
                        # Keep the CLI's own session id so an approval can
                        # park this run and resume it in a later process.
                        session.resume_state = {"sdk_session_id": message.session_id}
                        # The SDK counts turns against max_turns; counting
                        # assistant messages here instead reported 41 for a
                        # run capped at 15, which makes the cap look broken.
                        if message.num_turns:
                            session.iterations = message.num_turns
                        self._stopped_by = self._stop_reason(message) or self._stopped_by
                        await self.db.commit()

        return final_text

    async def _remember_sdk_session(self, message: Any) -> None:
        """Persist the CLI's session id as soon as it is known.

        Tolerant of shape: the SDK exposes ``session_id`` as an attribute
        on some system messages and only inside ``data`` on the generic
        ones, and an id LabDog fails to record is a session it cannot
        resume.
        """
        data = getattr(message, "data", None)
        sdk_session_id = getattr(message, "session_id", None) or (
            data.get("session_id") if isinstance(data, dict) else None
        )
        if not sdk_session_id:
            return
        if (self.session.resume_state or {}).get("sdk_session_id") == sdk_session_id:
            return
        async with self._db_lock:
            self.session.resume_state = {"sdk_session_id": sdk_session_id}
            await self.db.commit()

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
        # Whichever source has seen more. Mid-run only the live estimate has
        # anything in it; once `ResultMessage` books the authoritative
        # figure the session's own counters take over. Taking the larger of
        # the two means neither can mask the other on a resumed run, where
        # the columns are already populated from the earlier process while
        # the estimate restarts at zero.
        total = max(
            self.session.prompt_tokens + self.session.completion_tokens,
            self._live_prompt + self._live_completion,
        )
        if total >= self.caps.max_tokens_total:
            return f"token budget ({self.caps.max_tokens_total})"
        return None
