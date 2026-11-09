"""Characterisation tests for agent-loop exits ``test_loop`` leaves dark.

``test_loop`` drives the main line and the iteration and token caps. These
pin the rest: the refusal of a tool-less backend, the exact event stream of
a round trip, a budget stop and warning, a cap hit before any turn, a turn
cut off at the output limit, a failing tool, a cancel that lands during the
final turn, and a failed wrap-up, so a restructuring of ``AgentLoop`` has
something to answer to.
"""

from __future__ import annotations

import dataclasses
import logging
from unittest.mock import AsyncMock, patch

from sqlalchemy import select, update

from app.ai.loop import AgentLoop, LoopCaps
from app.ai.models import AIMessage, AISession, AIToolCall
from app.ai.service import BudgetStatus
from app.ai.tools import TOOL_REGISTRY
from tests.ai.fake_provider import FakeProvider, ScriptedTurn, call


def _budget(day_spend: float = 0.0, day_limit: float = 0.0) -> BudgetStatus:
    return BudgetStatus(
        day_spend=day_spend,
        month_spend=0.0,
        day_limit=day_limit,
        month_limit=0.0,
        provider_month_spend=0.0,
        provider_month_limit=0.0,
    )


async def _loop(db, session, provider_row, fake, caps=None):
    events: list[tuple[str, dict]] = []

    async def publish(event, payload):  # noqa: ANN001
        events.append((event, payload))

    loop = AgentLoop(db, session, provider_row, caps or LoopCaps(), provider=fake, publish=publish)
    return loop, events


async def _messages(db, session_id) -> list[AIMessage]:
    return list(
        (
            await db.execute(
                select(AIMessage).where(AIMessage.session_id == session_id).order_by(AIMessage.seq)
            )
        )
        .scalars()
        .all()
    )


async def test_a_toolless_backend_is_refused_for_an_investigative_session(
    db, ai_provider, make_session
):
    session = await make_session()
    fake = FakeProvider([ScriptedTurn(text="I imagine it is fine.")], supports_tools=False)
    loop, events = await _loop(db, session, ai_provider, fake)

    outcome = await loop.run()

    message = (
        "This session needs tools to verify anything, and the 'fake' backend cannot "
        "run them. Nothing was run; no findings are available."
    )
    assert (outcome.status, outcome.report, outcome.iterations, outcome.stopped_by) == (
        "failed",
        "",
        0,
        message,
    )
    assert (session.status, session.error_message) == ("failed", message)
    assert session.started_at is None
    assert events == [("status", {"status": "failed", "error": message})]
    assert fake.calls == []


async def test_a_round_trip_publishes_its_events_in_order(db, ai_provider, make_session):
    session = await make_session()
    fake = FakeProvider(
        [ScriptedTurn(tool_calls=[call("list_hosts", "c1")]), ScriptedTurn(text="Done.")]
    )
    loop, events = await _loop(db, session, ai_provider, fake)

    with (
        patch(
            "app.ai.service.get_budget_status",
            new=AsyncMock(return_value=_budget(day_spend=9.0, day_limit=10.0)),
        ),
        patch("app.ai.loop.get_setting_typed", new=AsyncMock(return_value=80)),
    ):
        outcome = await loop.run()

    assert outcome.status == "succeeded"
    warning = {"message": "AI spend is at 90% of budget.", "day_spend": 9.0, "month_spend": 0.0}
    assert [name for name, _ in events] == [
        "budget_warning",
        "tool_call",
        "tool_result",
        "budget_warning",
        "text",
        "status",
    ]
    assert events[0] == ("budget_warning", warning)
    assert events[1] == ("tool_call", {"name": "list_hosts", "arguments": {}, "call_id": "c1"})
    result = events[2][1]
    assert (result["name"], result["call_id"], result["ok"], result["classification"]) == (
        "list_hosts",
        "c1",
        True,
        "read_only",
    )
    assert events[4] == ("text", {"text": "Done."})
    assert events[5] == ("status", {"status": "succeeded", "stopped_by": ""})
    # Both tools went to the provider, and the second turn saw the tool result.
    assert [s.name for s in fake.calls[0][1]] == [h.spec.name for h in loop._handlers]
    assert fake.calls[1][0][-1].role == "tool"


async def test_an_exceeded_budget_stops_the_run_before_the_next_turn(db, ai_provider, make_session):
    session = await make_session()
    fake = FakeProvider(
        [
            ScriptedTurn(tool_calls=[call("list_hosts", "c1")]),
            ScriptedTurn(text="Wrapped up within budget."),
        ]
    )
    loop, events = await _loop(db, session, ai_provider, fake)
    statuses = [_budget(), _budget(day_spend=10.0, day_limit=10.0)]

    with (
        patch("app.ai.service.get_budget_status", new=AsyncMock(side_effect=statuses)),
        patch("app.ai.loop.get_setting_typed", new=AsyncMock(return_value=0)),
    ):
        outcome = await loop.run()

    reason = "Daily AI budget reached: $10.00 of $10.00 spent today."
    assert outcome.stopped_by == reason
    assert outcome.status == "succeeded"
    assert outcome.iterations == 1
    assert outcome.report == f"Wrapped up within budget.\n\n---\n_Stopped early: {reason}._"
    # The second provider call is the wrap-up: no tools offered.
    assert len(fake.calls) == 2
    assert fake.calls[1][1] == []
    assert fake.calls[1][0][-1].role == "user"
    assert session.stopped_reason == reason
    assert "budget_warning" not in [name for name, _ in events]


async def test_a_cap_hit_before_any_turn_goes_straight_to_the_wrap_up(
    db, ai_provider, make_session
):
    session = await make_session()
    fake = FakeProvider([ScriptedTurn(text="Nothing checked yet.")])
    loop, events = await _loop(db, session, ai_provider, fake, LoopCaps(wall_clock_seconds=0))

    outcome = await loop.run()

    assert outcome.stopped_by == "time limit (0s)"
    assert outcome.iterations == 0
    assert outcome.report == "Nothing checked yet.\n\n---\n_Stopped early: time limit (0s)._"
    assert len(fake.calls) == 1
    assert fake.calls[0][1] == []
    rows = await _messages(db, session.id)
    assert [r.role for r in rows] == ["system", "user", "assistant"]
    assert events[-1] == ("status", {"status": "succeeded", "stopped_by": "time limit (0s)"})


async def test_a_turn_cut_off_at_the_output_limit_is_reported_as_stopped(
    db, ai_provider, make_session
):
    session = await make_session()
    fake = FakeProvider([ScriptedTurn(text="Half an answer", stop_reason="max_tokens")])
    loop, _ = await _loop(db, session, ai_provider, fake)

    outcome = await loop.run()

    limit = "the model's per-turn output limit"
    assert outcome.status == "succeeded"
    assert outcome.stopped_by == limit
    assert outcome.report == f"Half an answer\n\n---\n_Stopped early: {limit}._"
    # The turn's own text is the report: no wrap-up turn is asked for.
    assert len(fake.calls) == 1


async def test_a_failing_tool_is_recorded_and_reported_to_the_model(
    db, ai_provider, make_session, caplog
):
    session = await make_session()
    fake = FakeProvider(
        [ScriptedTurn(tool_calls=[call("list_hosts", "c1")]), ScriptedTurn(text="Recovered.")]
    )
    loop, events = await _loop(db, session, ai_provider, fake)

    async def _boom(ctx, arguments):  # noqa: ARG001
        raise RuntimeError("inventory on fire")

    broken = dataclasses.replace(TOOL_REGISTRY["list_hosts"], run=_boom)
    with (
        patch.dict(TOOL_REGISTRY, {"list_hosts": broken}),
        caplog.at_level(logging.ERROR, logger="app.ai.loop"),
    ):
        outcome = await loop.run()

    assert outcome.status == "succeeded"
    assert f"ai session {session.id}: tool list_hosts failed" in caplog.text
    record = (
        await db.execute(select(AIToolCall).where(AIToolCall.session_id == session.id))
    ).scalar_one()
    assert (record.status, record.result_summary) == ("error", "inventory on fire")
    assert record.finished_at is not None
    tool_rows = [r for r in await _messages(db, session.id) if r.role == "tool"]
    assert [r.content for r in tool_rows] == ["The list_hosts tool failed: inventory on fire"]
    names = [name for name, _ in events]
    assert "tool_call" in names
    assert "tool_result" not in names


async def test_a_cancel_during_the_final_turn_wins_over_the_outcome(db, ai_provider, make_session):
    session = await make_session()

    class _CancelledMidTurn(FakeProvider):
        async def stream_turn(self, messages, tools, **kwargs):
            async for event in super().stream_turn(messages, tools, **kwargs):
                yield event
            await db.execute(
                update(AISession).where(AISession.id == session.id).values(status="cancelled")
            )

    fake = _CancelledMidTurn([ScriptedTurn(text="Done.")])
    loop, events = await _loop(db, session, ai_provider, fake)

    outcome = await loop.run()

    assert (outcome.status, outcome.report, outcome.iterations, outcome.stopped_by) == (
        "cancelled",
        "Done.",
        1,
        "cancelled by operator",
    )
    assert events[-1] == ("status", {"status": "cancelled"})
    assert session.finished_at is None
    assert session.report_markdown is None


async def test_a_failed_wrap_up_leaves_a_failed_run(db, ai_provider, make_session, caplog):
    session = await make_session()
    fake = FakeProvider([ScriptedTurn(error="provider unreachable")])
    loop, events = await _loop(db, session, ai_provider, fake, LoopCaps(wall_clock_seconds=0))

    with caplog.at_level(logging.INFO, logger="app.ai.loop"):
        outcome = await loop.run()

    stopped = "time limit (0s)"
    assert outcome.status == "failed"
    assert outcome.report == (
        f"The session stopped early: {stopped}.\n\n---\n_Stopped early: {stopped}._"
    )
    assert f"ai session {session.id}: wrap-up failed: provider unreachable" in caplog.text
    assert [r.role for r in await _messages(db, session.id)] == ["system", "user"]
    assert (session.status, session.stopped_reason) == ("failed", stopped)
    assert events[-1] == ("status", {"status": "failed", "stopped_by": stopped})


async def test_a_cap_hit_mid_turn_leaves_the_rest_of_the_turn_unanswered(
    db, ai_provider, make_session
):
    session = await make_session()
    fake = FakeProvider(
        [
            ScriptedTurn(tool_calls=[call("list_hosts", "c1"), call("list_hosts", "c2")]),
            ScriptedTurn(text="Summary."),
        ]
    )
    caps = LoopCaps(max_iterations=1)
    loop, _ = await _loop(db, session, ai_provider, fake, caps)

    outcome = await loop.run()

    assert outcome.stopped_by == "iteration limit (1)"
    tool_rows = [r for r in await _messages(db, session.id) if r.role == "tool"]
    assert [r.tool_call_id for r in tool_rows] == ["c1"]
    calls = (
        (await db.execute(select(AIToolCall).where(AIToolCall.session_id == session.id)))
        .scalars()
        .all()
    )
    assert len(calls) == 1
