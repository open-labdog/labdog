"""Agent-loop tests.

Driven by the scripted :class:`FakeProvider`, so these exercise the real
control flow — tool dispatch, transcript growth, usage accounting, and
every cap — with no network and no model.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.ai.loop import AgentLoop, LoopCaps
from app.ai.models import AIMessage, AIToolCall, AIUsageDay
from tests.ai.fake_provider import FakeProvider, ScriptedTurn, call


async def _run(db, session, provider_row, turns, caps=None, events=None):
    fake = FakeProvider(turns)
    publish = None
    if events is not None:

        async def publish(event, payload):  # noqa: ANN001
            events.append((event, payload))

    loop = AgentLoop(
        db,
        session,
        provider_row,
        caps or LoopCaps(),
        provider=fake,
        publish=publish,
    )
    outcome = await loop.run()
    return outcome, fake


class TestBasicFlow:
    async def test_single_turn_no_tools(self, db, ai_provider, make_session):
        session = await make_session()
        outcome, fake = await _run(
            db,
            session,
            ai_provider,
            [ScriptedTurn(text="nginx is running normally.")],
        )

        assert outcome.status == "succeeded"
        assert outcome.iterations == 1
        assert "nginx is running normally." in outcome.report
        assert session.status == "succeeded"
        assert session.finished_at is not None
        # The seeded system prompt reached the provider.
        sent_messages, _ = fake.calls[0]
        assert sent_messages[0].role == "system"

    async def test_assistant_turn_is_persisted(self, db, ai_provider, make_session):
        session = await make_session()
        await _run(db, session, ai_provider, [ScriptedTurn(text="All good.")])

        rows = (
            (
                await db.execute(
                    select(AIMessage)
                    .where(AIMessage.session_id == session.id)
                    .order_by(AIMessage.seq)
                )
            )
            .scalars()
            .all()
        )
        assert [r.role for r in rows] == ["system", "user", "assistant"]
        assert rows[-1].content == "All good."

    async def test_tool_call_round_trip(self, db, ai_provider, make_session):
        """A tool call produces a tool turn, and the loop asks again."""
        session = await make_session()
        outcome, fake = await _run(
            db,
            session,
            ai_provider,
            [
                ScriptedTurn(tool_calls=[call("list_hosts")]),
                ScriptedTurn(text="No hosts are in scope, so nothing to check."),
            ],
        )

        assert outcome.status == "succeeded"
        assert outcome.iterations == 2
        roles = [
            r.role
            for r in (
                await db.execute(
                    select(AIMessage)
                    .where(AIMessage.session_id == session.id)
                    .order_by(AIMessage.seq)
                )
            )
            .scalars()
            .all()
        ]
        assert roles == ["system", "user", "assistant", "tool", "assistant"]

        # The tool result was fed back on the second request.
        second_messages, _ = fake.calls[1]
        assert second_messages[-1].role == "tool"

    async def test_tool_call_is_recorded(self, db, ai_provider, make_session):
        session = await make_session()
        await _run(
            db,
            session,
            ai_provider,
            [ScriptedTurn(tool_calls=[call("list_hosts")]), ScriptedTurn(text="Done.")],
        )
        records = (
            (await db.execute(select(AIToolCall).where(AIToolCall.session_id == session.id)))
            .scalars()
            .all()
        )
        assert len(records) == 1
        assert records[0].tool_name == "list_hosts"
        assert records[0].status == "executed"
        assert records[0].classification == "read_only"

    async def test_unknown_tool_is_reported_not_fatal(self, db, ai_provider, make_session):
        """A hallucinated tool name comes back as an error the model can fix."""
        session = await make_session()
        outcome, _ = await _run(
            db,
            session,
            ai_provider,
            [
                ScriptedTurn(tool_calls=[call("teleport_to_host")]),
                ScriptedTurn(text="Recovered and finished."),
            ],
        )
        assert outcome.status == "succeeded"
        tool_msg = (
            (
                await db.execute(
                    select(AIMessage).where(
                        AIMessage.session_id == session.id, AIMessage.role == "tool"
                    )
                )
            )
            .scalars()
            .one()
        )
        assert "no tool called" in tool_msg.content.lower()

    async def test_provider_error_fails_the_session(self, db, ai_provider, make_session):
        session = await make_session()
        outcome, _ = await _run(
            db, session, ai_provider, [ScriptedTurn(error="connection refused")]
        )
        assert outcome.status == "failed"
        assert session.status == "failed"
        assert "connection refused" in (session.error_message or "")


class TestUsageAccounting:
    async def test_tokens_and_cost_land_on_the_session(
        self, db, paid_provider, make_session
    ):
        session = await make_session(provider=paid_provider)
        await _run(
            db,
            session,
            paid_provider,
            [ScriptedTurn(text="Done.", prompt_tokens=1_000_000, completion_tokens=100_000)],
        )
        assert session.prompt_tokens == 1_000_000
        assert session.completion_tokens == 100_000
        # 1M in at $5/M + 100k out at $25/M
        assert session.cost_usd == pytest.approx(5.0 + 2.5)

    async def test_usage_rolls_into_the_daily_ledger(
        self, db, paid_provider, make_session
    ):
        session = await make_session(provider=paid_provider)
        await _run(
            db,
            session,
            paid_provider,
            [ScriptedTurn(text="Done.", prompt_tokens=1_000_000, completion_tokens=0)],
        )
        row = (
            (
                await db.execute(
                    select(AIUsageDay).where(AIUsageDay.provider_id == paid_provider.id)
                )
            )
            .scalars()
            .one()
        )
        assert row.prompt_tokens == 1_000_000
        assert row.cost_usd == pytest.approx(5.0)
        assert row.turn_count == 1

    async def test_ledger_accumulates_across_turns(self, db, paid_provider, make_session):
        """Two turns upsert into one row rather than colliding."""
        session = await make_session(provider=paid_provider)
        await _run(
            db,
            session,
            paid_provider,
            [
                ScriptedTurn(
                    tool_calls=[call("list_hosts")], prompt_tokens=1_000_000, completion_tokens=0
                ),
                ScriptedTurn(text="Done.", prompt_tokens=1_000_000, completion_tokens=0),
            ],
        )
        row = (
            (
                await db.execute(
                    select(AIUsageDay).where(AIUsageDay.provider_id == paid_provider.id)
                )
            )
            .scalars()
            .one()
        )
        assert row.prompt_tokens == 2_000_000
        assert row.turn_count == 2

    async def test_local_provider_costs_nothing(self, db, ai_provider, make_session):
        session = await make_session()
        await _run(
            db,
            session,
            ai_provider,
            [ScriptedTurn(text="Done.", prompt_tokens=999_999, completion_tokens=999_999)],
        )
        assert session.cost_usd == 0.0

    async def test_unreported_usage_marks_cost_as_a_floor(
        self, db, paid_provider, make_session
    ):
        session = await make_session(provider=paid_provider)
        await _run(
            db,
            session,
            paid_provider,
            [ScriptedTurn(text="Done.", usage_unknown=True, prompt_tokens=0, completion_tokens=0)],
        )
        assert session.cost_unknown is True


class TestCaps:
    async def test_iteration_cap_stops_the_loop(
        self, db, ai_provider, make_session, small_caps
    ):
        """A model that never stops calling tools is stopped for it."""
        session = await make_session()
        turns = [ScriptedTurn(tool_calls=[call("list_hosts", f"c{i}")]) for i in range(10)]
        outcome, _ = await _run(db, session, ai_provider, turns, caps=small_caps)

        assert session.iterations <= small_caps.max_iterations
        assert "iteration limit" in outcome.stopped_by
        assert "Stopped early" in outcome.report

    async def test_token_cap_stops_the_loop(self, db, ai_provider, make_session):
        caps = LoopCaps(max_iterations=50, max_commands=50, max_tokens_total=500)
        session = await make_session()
        turns = [
            ScriptedTurn(
                tool_calls=[call("list_hosts", f"c{i}")],
                prompt_tokens=400,
                completion_tokens=200,
            )
            for i in range(10)
        ]
        outcome, _ = await _run(db, session, ai_provider, turns, caps=caps)
        assert "token budget" in outcome.stopped_by

    async def test_capped_run_still_produces_a_report(
        self, db, ai_provider, make_session, small_caps
    ):
        """Hitting a cap must not throw away the work already paid for."""
        session = await make_session()
        turns = [ScriptedTurn(tool_calls=[call("list_hosts", f"c{i}")]) for i in range(5)]
        turns.append(ScriptedTurn(text="Partial findings: nothing conclusive yet."))
        outcome, _ = await _run(db, session, ai_provider, turns, caps=small_caps)

        assert outcome.report
        assert "Stopped early" in outcome.report


class TestStreaming:
    async def test_text_and_tool_events_are_published(
        self, db, ai_provider, make_session
    ):
        events: list[tuple[str, dict]] = []
        session = await make_session()
        await _run(
            db,
            session,
            ai_provider,
            [ScriptedTurn(tool_calls=[call("list_hosts")]), ScriptedTurn(text="Done.")],
            events=events,
        )
        kinds = [name for name, _ in events]
        assert "tool_call" in kinds
        assert "tool_result" in kinds
        assert "text" in kinds
        assert kinds[-1] == "status"

    async def test_a_broken_stream_does_not_abort_the_run(
        self, db, ai_provider, make_session
    ):
        """The transcript is the source of truth; SSE is best-effort."""

        async def exploding_publish(event, payload):  # noqa: ANN001
            raise RuntimeError("redis is down")

        loop = AgentLoop(
            db,
            session := await make_session(),
            ai_provider,
            LoopCaps(),
            provider=FakeProvider([ScriptedTurn(text="Still fine.")]),
            publish=exploding_publish,
        )
        outcome = await loop.run()
        assert outcome.status == "succeeded"
        assert session.status == "succeeded"
