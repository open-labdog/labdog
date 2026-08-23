"""The SDK runner, driven by a scripted client.

These cover what the runner owns rather than what the SDK does: the
options it hands over (where the lockdown posture lives), the rows it
writes, and the accounting. The SDK's own behaviour — that it honours a
denial, suppresses built-ins for ``tools=[]``, and resumes a session —
was verified against the real binary and is recorded in
``plans/agent-sdk.md``; re-asserting it against a fake would only test
the fake.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.ai.models import AIMessage, AISession, AIToolCall

pytest.importorskip("claude_agent_sdk", reason="optional [agent] extra not installed")

from app.ai.agent_sdk.runner import AgentSDKRunner  # noqa: E402
from app.ai.loop import LoopCaps  # noqa: E402
from tests.ai.fake_sdk_client import FakeSDKClient, assistant, result  # noqa: E402


async def _run(db, session, provider_row, messages, *, caps=None, events=None, on_query=None):
    fake = FakeSDKClient(messages, on_query=on_query)
    publish = None
    if events is not None:

        async def publish(event, payload):  # noqa: ANN001
            events.append((event, payload))

    runner = AgentSDKRunner(
        db,
        session,
        provider_row,
        caps or LoopCaps(),
        publish=publish,
        client_factory=fake.factory,
    )
    outcome = await runner.run()
    return outcome, fake, runner


class TestTheOptionsHandedToTheSdk:
    """The safety posture is expressed entirely as options, so this is
    where it has to be pinned."""

    async def test_built_in_tools_are_withheld(self, db, ai_provider, make_session) -> None:
        """The single most important assertion in this file. Omitting
        ``tools`` does not disable a feature — it hands the model Bash,
        Read, Write and Edit inside LabDog's container."""
        session = await make_session()
        _, fake, _ = await _run(db, session, ai_provider, [assistant("done"), result()])

        assert fake.options.tools == [], "an empty list means no built-ins; None means all of them"

    async def test_allowed_tools_is_empty_so_the_gate_is_consulted(
        self, db, ai_provider, make_session
    ) -> None:
        """An entry in ``allowed_tools`` auto-approves that tool *before*
        ``can_use_tool`` runs, silently disabling the permission gate."""
        session = await make_session()
        _, fake, _ = await _run(db, session, ai_provider, [assistant("done"), result()])

        assert fake.options.allowed_tools == []
        assert fake.options.can_use_tool is not None

    async def test_host_configuration_is_not_loaded(self, db, ai_provider, make_session) -> None:
        """A CLAUDE.md or MCP server belonging to whoever administers the
        box is not part of LabDog's threat model."""
        session = await make_session()
        _, fake, _ = await _run(db, session, ai_provider, [assistant("done"), result()])

        assert fake.options.setting_sources == []
        assert fake.options.strict_mcp_config is True

    async def test_only_labdog_tools_are_served(self, db, ai_provider, make_session) -> None:
        session = await make_session()
        _, fake, _ = await _run(db, session, ai_provider, [assistant("done"), result()])

        assert set(fake.options.mcp_servers) == {"labdog"}

    async def test_the_iteration_cap_is_handed_over_as_max_turns(
        self, db, ai_provider, make_session, small_caps
    ) -> None:
        """There is no per-iteration seam to enforce it in, so the SDK
        has to be told."""
        session = await make_session()
        _, fake, _ = await _run(
            db, session, ai_provider, [assistant("done"), result()], caps=small_caps
        )

        assert fake.options.max_turns == small_caps.max_iterations

    async def test_the_mission_is_what_gets_asked(self, db, ai_provider, make_session) -> None:
        session = await make_session("Check disk usage on the media box.")
        _, fake, _ = await _run(db, session, ai_provider, [assistant("done"), result()])

        assert fake.prompts == ["Check disk usage on the media box."]


class TestBasicFlow:
    async def test_a_completed_run_succeeds(self, db, ai_provider, make_session) -> None:
        session = await make_session()
        outcome, _, _ = await _run(
            db, session, ai_provider, [assistant("nginx is healthy."), result()]
        )

        assert outcome.status == "succeeded"
        assert "nginx is healthy." in outcome.report
        assert session.status == "succeeded"
        assert session.finished_at is not None

    async def test_the_assistant_turn_is_persisted(self, db, ai_provider, make_session) -> None:
        session = await make_session()
        await _run(db, session, ai_provider, [assistant("All good."), result()])

        rows = (
            (
                await db.execute(
                    select(AIMessage)
                    .where(AIMessage.session_id == session.id, AIMessage.role == "assistant")
                    .order_by(AIMessage.seq)
                )
            )
            .scalars()
            .all()
        )
        assert [r.content for r in rows] == ["All good."]

    async def test_text_is_streamed_to_the_ui(self, db, ai_provider, make_session) -> None:
        session = await make_session()
        events: list = []
        await _run(db, session, ai_provider, [assistant("Looking now.")], events=events)

        assert ("text", {"text": "Looking now."}) in events

    async def test_a_run_with_no_text_is_not_reported_as_success(
        self, db, ai_provider, make_session
    ) -> None:
        """An empty report badged green is how a fabricated investigation
        got through once already."""
        session = await make_session()
        outcome, _, _ = await _run(db, session, ai_provider, [result()])

        assert outcome.status == "failed"


class TestResumeState:
    async def test_the_sdk_session_id_is_kept(self, db, ai_provider, make_session) -> None:
        """Phase 3 parks a session on an approval and resumes it in a
        later process; the CLI's own session id is the handle."""
        session = await make_session()
        await _run(db, session, ai_provider, [assistant("ok"), result(session_id="abc-123")])

        assert session.resume_state == {"sdk_session_id": "abc-123"}

    async def test_a_stored_id_is_passed_back_as_resume(
        self, db, ai_provider, make_session
    ) -> None:
        session = await make_session()
        session.resume_state = {"sdk_session_id": "earlier-session"}
        await db.flush()

        _, fake, _ = await _run(db, session, ai_provider, [assistant("ok"), result()])
        assert fake.options.resume == "earlier-session"

    async def test_a_fresh_session_resumes_nothing(self, db, ai_provider, make_session) -> None:
        session = await make_session()
        _, fake, _ = await _run(db, session, ai_provider, [assistant("ok"), result()])
        assert fake.options.resume is None


class TestUsageAccounting:
    async def test_tokens_are_recorded(self, db, ai_provider, make_session) -> None:
        session = await make_session()
        await _run(
            db,
            session,
            ai_provider,
            [assistant("ok"), result(usage={"input_tokens": 100, "output_tokens": 40})],
        )

        assert session.prompt_tokens == 100
        assert session.completion_tokens == 40

    async def test_cached_input_counts_towards_the_token_cap(
        self, db, ai_provider, make_session
    ) -> None:
        """Cache reads are input tokens that really were sent. Ignoring
        them would let a cached session run past a limit an uncached one
        would hit."""
        session = await make_session()
        await _run(
            db,
            session,
            ai_provider,
            [
                assistant("ok"),
                result(
                    usage={
                        "input_tokens": 10,
                        "cache_creation_input_tokens": 500,
                        "cache_read_input_tokens": 2000,
                        "output_tokens": 5,
                    }
                ),
            ],
        )

        assert session.prompt_tokens == 2510

    async def test_missing_usage_marks_the_cost_as_a_floor(
        self, db, ai_provider, make_session
    ) -> None:
        session = await make_session()
        await _run(db, session, ai_provider, [assistant("ok"), result(usage=None)])

        assert session.cost_unknown is True


class TestCancellation:
    async def test_a_cancel_stops_the_run(self, db, ai_provider, make_session) -> None:
        session = await make_session()

        async def cancel_midway():
            await db.execute(
                AISession.__table__.update()
                .where(AISession.id == session.id)
                .values(status="cancelled")
            )
            await db.commit()

        outcome, fake, _ = await _run(
            db,
            session,
            ai_provider,
            [assistant("starting"), assistant("still going"), result()],
            on_query=cancel_midway,
        )

        assert outcome.status == "cancelled"
        assert fake.interrupted is True, "the subprocess must be told to stop, not just abandoned"


class TestRefusalsAreRecorded:
    """A denied call never reaches the tool, so the gate is the only
    place that can write it down. Without the row the transcript shows
    the model changing the subject for no visible reason."""

    async def test_a_refused_call_gets_a_blocked_row(self, db, ai_provider, make_session) -> None:
        session = await make_session(autonomy_level="read_only")
        runner = AgentSDKRunner(db, session, ai_provider, LoopCaps())

        decision = await runner._can_use_tool(
            "mcp__labdog__run_ssh_command",
            {"host_id": 1, "command": "systemctl restart nginx"},
            _FakeContext(),
        )

        assert decision.behavior == "deny"
        rows = (
            (await db.execute(select(AIToolCall).where(AIToolCall.session_id == session.id)))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].status == "blocked"
        assert rows[0].tool_name == "run_ssh_command"
        assert rows[0].classification == "mutating"

    async def test_a_permitted_call_is_allowed_and_not_pre_recorded(
        self, db, ai_provider, make_session
    ) -> None:
        """The row for a call that runs is written by the executor, with
        its result — writing one here too would double-count it."""
        session = await make_session(autonomy_level="read_only")
        runner = AgentSDKRunner(db, session, ai_provider, LoopCaps())

        decision = await runner._can_use_tool(
            "mcp__labdog__run_ssh_command",
            {"host_id": 1, "command": "journalctl -u nginx -n 20"},
            _FakeContext(),
        )

        assert decision.behavior == "allow"
        rows = (
            (await db.execute(select(AIToolCall).where(AIToolCall.session_id == session.id)))
            .scalars()
            .all()
        )
        assert rows == []


class _FakeContext:
    tool_use_id = "toolu_fake"


class TestTruncationIsNotSuccess:
    """Observed in production. A session hit the turn limit mid-sweep and
    the report shown to the operator was the model's last half-finished
    sentence — "All docker containers are up and healthy. Now let me check
    network/connectivity and CPU details to round out the picture." —
    badged succeeded, with no indication it had been cut off.

    That is the fabrication failure in different clothes: incomplete work
    presented as a conclusion. The old guard only recorded a stop reason
    when there was *no* text, so a run truncated after saying something
    looked exactly like one that finished.
    """

    async def test_hitting_the_turn_limit_is_recorded(self, db, ai_provider, make_session) -> None:
        session = await make_session()
        outcome, _, _ = await _run(
            db,
            session,
            ai_provider,
            [assistant("Now let me check the network."), result(subtype="error_max_turns")],
        )

        assert outcome.stopped_by, "a truncated run must say why it stopped"
        assert "turn limit" in outcome.stopped_by

    async def test_the_report_says_it_stopped_early(self, db, ai_provider, make_session) -> None:
        """The operator has to be able to tell a conclusion from an
        interruption without reading the transcript."""
        session = await make_session()
        outcome, _, _ = await _run(
            db,
            session,
            ai_provider,
            [assistant("Now let me check the network."), result(subtype="error_max_turns")],
        )

        assert "Stopped early" in outcome.report

    async def test_text_does_not_mask_truncation(self, db, ai_provider, make_session) -> None:
        """The exact regression: the run produced text *and* was cut off."""
        session = await make_session()
        outcome, _, _ = await _run(
            db,
            session,
            ai_provider,
            [assistant("Some findings so far."), result(subtype="error_max_turns")],
        )

        assert outcome.stopped_by != ""

    async def test_a_clean_finish_is_not_flagged(self, db, ai_provider, make_session) -> None:
        session = await make_session()
        outcome, _, _ = await _run(
            db, session, ai_provider, [assistant("nginx is healthy."), result()]
        )

        assert outcome.stopped_by == ""
        assert "Stopped early" not in outcome.report
        assert outcome.status == "succeeded"

    async def test_turns_are_counted_the_way_the_cap_counts_them(
        self, db, ai_provider, make_session
    ) -> None:
        """Counting assistant messages reported 41 turns for a run capped
        at 15, which makes the cap look broken. The SDK's own count is the
        one the cap is compared against."""
        session = await make_session()
        await _run(
            db,
            session,
            ai_provider,
            [assistant("a"), assistant("b"), assistant("c"), result(num_turns=7)],
        )

        assert session.iterations == 7

    async def test_a_provider_error_is_recorded_too(self, db, ai_provider, make_session) -> None:
        session = await make_session()
        outcome, _, _ = await _run(
            db,
            session,
            ai_provider,
            [assistant("partial"), result(is_error=True, result_text="overloaded")],
        )

        assert outcome.stopped_by != ""


class TestTheTokenCapCanActuallyFire:
    """Regression cover for a cap that was enforced against a value that
    was always zero.

    ``_record_usage`` ran only in the ``ResultMessage`` branch, and
    ``ResultMessage`` is the SDK's terminal message. So the session's token
    columns stayed at 0 for the whole run, ``_cap_hit`` compared 0 against
    the limit every turn, and the real total arrived once there was nothing
    left to stop. Observed in production: a session capped at 10,000 tokens
    ran to completion having spent 111,857.

    What makes these tests bite is the ``usage`` on each assistant message.
    Without it the runner has nothing to count and every one of them passes
    against the broken code.
    """

    async def test_a_run_over_the_cap_is_stopped(self, db, ai_provider, make_session) -> None:
        session = await make_session()
        # 60 tokens a turn against a 100 limit: under after one, over after
        # two. A single oversized turn would also pass on code that only
        # ever checked once.
        turn = {"input_tokens": 50, "output_tokens": 10}
        outcome, fake, _ = await _run(
            db,
            session,
            ai_provider,
            [assistant("one", turn), assistant("two", turn), assistant("three", turn), result()],
            caps=LoopCaps(max_tokens_total=100),
        )

        assert "token budget" in (outcome.stopped_by or ""), (
            f"the cap did not fire; stopped_by={outcome.stopped_by!r}"
        )
        assert fake.interrupted, "hitting the cap must interrupt the SDK, not just flag it"

    async def test_cache_tokens_count_toward_the_cap(self, db, ai_provider, make_session) -> None:
        """A cached run must not get a larger allowance than an uncached one.

        Cache reads are input tokens that were really sent. Counting only
        ``input_tokens`` would let a session with a warm cache run far past
        a limit an identical cold one would hit.
        """
        session = await make_session()
        cached = {"input_tokens": 5, "cache_read_input_tokens": 900, "output_tokens": 5}
        outcome, _, _ = await _run(
            db,
            session,
            ai_provider,
            [assistant("one", cached), assistant("two", cached), result()],
            caps=LoopCaps(max_tokens_total=500),
        )

        assert "token budget" in (outcome.stopped_by or "")

    async def test_a_run_under_the_cap_is_left_alone(self, db, ai_provider, make_session) -> None:
        """The other half of the contract, and the one that would catch an
        estimate that over-counts: a cap must not fire early."""
        session = await make_session()
        outcome, fake, _ = await _run(
            db,
            session,
            ai_provider,
            [assistant("one", {"input_tokens": 10, "output_tokens": 5}), result()],
            caps=LoopCaps(max_tokens_total=100_000),
        )

        assert "token budget" not in (outcome.stopped_by or "")
        assert not fake.interrupted

    async def test_the_estimate_is_not_booked_as_spend(self, db, ai_provider, make_session) -> None:
        """The live estimate gates the cap; ``ResultMessage`` books the run.

        They come from different sources and need not agree, so folding the
        estimate into the session's columns would make reported spend depend
        on which message type arrived last — and would double count.
        """
        session = await make_session()
        await _run(
            db,
            session,
            ai_provider,
            [
                assistant("one", {"input_tokens": 500, "output_tokens": 500}),
                assistant("two", {"input_tokens": 500, "output_tokens": 500}),
                result(usage={"input_tokens": 10, "output_tokens": 5}),
            ],
            caps=LoopCaps(max_tokens_total=100_000),
        )

        assert session.prompt_tokens == 10, "the authoritative figure, not the estimate"
        assert session.completion_tokens == 5

    async def test_a_capped_run_still_books_its_tokens(self, db, ai_provider, make_session) -> None:
        """The runs that hit a limit are the ones worth accounting for.

        Interrupting the SDK ends the exchange without its ``ResultMessage``,
        and that message was the only thing that booked a run — so hitting
        the cap recorded zero tokens and zero cost, and the daily ledger got
        no row at all. Seen in production the first time the cap fired: a
        session stopped on its token budget having spent real tokens and
        reported none.

        The estimate is booked instead, flagged ``cost_unknown`` because it
        is an approximation rather than the CLI's own aggregate.
        """
        session = await make_session()
        turn = {"input_tokens": 50, "output_tokens": 10}
        # No result() in the script: an interrupted exchange does not get one.
        await _run(
            db,
            session,
            ai_provider,
            [assistant("one", turn), assistant("two", turn), assistant("three", turn)],
            caps=LoopCaps(max_tokens_total=100),
        )

        assert session.prompt_tokens + session.completion_tokens > 0, (
            "a stopped run recorded no usage at all"
        )
        assert session.cost_unknown, "an estimate must be marked as one"
