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

from app.ai.agent_sdk.runner import _INTERRUPTED_CALL as _INTERRUPTED  # noqa: E402
from app.ai.agent_sdk.runner import AgentSDKRunner  # noqa: E402
from app.ai.loop import LoopCaps  # noqa: E402
from tests.ai.fake_sdk_client import (  # noqa: E402
    FakeSDKClient,
    assistant,
    rate_limit,
    result,
)
from tests.conftest import create_host  # noqa: E402


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

    async def test_a_refused_call_records_its_host(self, db, ai_provider, make_session) -> None:
        """BUG-109: the API path's refusal carries the host and this one
        did not, so the audit trail said a command was refused but not
        where."""
        host = await create_host(db)
        session = await make_session(autonomy_level="read_only", target_host_ids=[host.id])
        runner = AgentSDKRunner(db, session, ai_provider, LoopCaps())

        await runner._can_use_tool(
            "mcp__labdog__run_ssh_command",
            {"host_id": host.id, "command": "systemctl restart nginx"},
            _FakeContext(),
        )

        [row] = (
            (await db.execute(select(AIToolCall).where(AIToolCall.session_id == session.id)))
            .scalars()
            .all()
        )
        assert row.status == "blocked"
        assert row.target_host_id == host.id

    @pytest.mark.parametrize("named", [987_654, "other", "1", None])
    async def test_a_host_it_may_not_touch_is_left_unset(
        self, db, ai_provider, make_session, named
    ) -> None:
        """The id comes from the model and the column is a foreign key: a
        made-up one would fail the insert and lose the record."""
        host = await create_host(db)
        other = await create_host(db, ip="10.0.0.2")
        session = await make_session(autonomy_level="read_only", target_host_ids=[host.id])
        runner = AgentSDKRunner(db, session, ai_provider, LoopCaps())

        await runner._can_use_tool(
            "mcp__labdog__run_ssh_command",
            {
                "host_id": other.id if named == "other" else named,
                "command": "systemctl restart nginx",
            },
            _FakeContext(),
        )

        [row] = (
            (await db.execute(select(AIToolCall).where(AIToolCall.session_id == session.id)))
            .scalars()
            .all()
        )
        assert row.status == "blocked"
        assert row.target_host_id is None

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


class TestASessionSaysWhyItStopped:
    """A truncated run used to be indistinguishable from a finished one.

    `status` is "succeeded" either way — a capped run did what it was asked
    until the budget ran out, which is not a failure — so the only trace of
    the cap was the Celery task's return value and a sentence at the bottom
    of the report. Neither is reachable from the UI, and the session list
    showed nothing at all.
    """

    async def test_the_reason_is_persisted(self, db, ai_provider, make_session) -> None:
        session = await make_session()
        turn = {"input_tokens": 50, "output_tokens": 10}
        await _run(
            db,
            session,
            ai_provider,
            [assistant("one", turn), assistant("two", turn), assistant("three", turn)],
            caps=LoopCaps(max_tokens_total=100),
        )

        assert session.stopped_reason, "the session cannot say why it stopped"
        assert "token budget" in session.stopped_reason

    async def test_a_run_that_finished_leaves_it_unset(self, db, ai_provider, make_session) -> None:
        """Otherwise every session would wear a "cut short" badge."""
        session = await make_session()
        await _run(db, session, ai_provider, [assistant("all done"), result()])

        assert not session.stopped_reason


class TestThePlanQuotaIsAStopReason:
    """The limit that actually binds a subscription-billed provider.

    `claude_agent` books cost from `estimate_cost` and flags it
    `cost_unknown`, because on a subscription no per-token money is spent.
    The money budgets therefore measure something imaginary, and the plan's
    own quota is the only limit that can really end a run — the one thing
    the runner ignored. `RateLimitEvent` was not handled at all, so a
    refused run reported "the backend reported an error" at best, and at
    worst finished on whatever had already been said and wore a green
    `succeeded` badge with no indication the plan had cut it off.
    """

    async def test_a_refusal_stops_the_run(self, db, ai_provider, make_session) -> None:
        """The load-bearing assertion. Without the break, the exchange runs
        on into a turn the plan has already refused."""
        session = await make_session()
        await _run(
            db,
            session,
            ai_provider,
            [
                assistant("Checked disk usage."),
                rate_limit("rejected"),
                assistant("And now the network."),
                result(),
            ],
        )

        rows = (
            (
                await db.execute(
                    select(AIMessage.content)
                    .where(AIMessage.session_id == session.id, AIMessage.role == "assistant")
                    .order_by(AIMessage.id)
                )
            )
            .scalars()
            .all()
        )

        assert rows == ["Checked disk usage."], (
            "the exchange continued past a refused quota; the second turn was persisted"
        )

    async def test_the_refusal_is_what_the_session_says_stopped_it(
        self, db, ai_provider, make_session
    ) -> None:
        session = await make_session()
        outcome, _, _ = await _run(
            db,
            session,
            ai_provider,
            [assistant("Checked disk usage."), rate_limit("rejected"), result()],
        )

        assert "5-hour rate limit" in (outcome.stopped_by or "")
        assert "5-hour rate limit" in (session.stopped_reason or "")
        assert "Stopped early" in outcome.report

    async def test_the_reset_time_is_carried_into_the_reason(
        self, db, ai_provider, make_session
    ) -> None:
        """ "Come back later" is only useful with a "later" attached."""
        session = await make_session()
        outcome, _, _ = await _run(
            db,
            session,
            ai_provider,
            [
                assistant("Checked disk usage."),
                # 2026-09-10 17:30 UTC.
                rate_limit("rejected", resets_at=1789061400),
                result(),
            ],
        )

        assert "resetting at 17:30 UTC on 10 Sep" in (outcome.stopped_by or "")

    async def test_no_wrap_up_turn_is_attempted(self, db, ai_provider, make_session) -> None:
        """Every other stop reason buys one more turn to summarise with.
        This one cannot: the wrap-up is another request on the quota that
        was just refused."""
        session = await make_session()
        _, fake, _ = await _run(
            db,
            session,
            ai_provider,
            [assistant("Checked disk usage."), rate_limit("rejected"), result()],
        )

        assert len(fake.prompts) == 1, "a second prompt means the wrap-up turn was attempted"

    async def test_the_sdk_is_interrupted(self, db, ai_provider, make_session) -> None:
        session = await make_session()
        _, fake, _ = await _run(
            db,
            session,
            ai_provider,
            [assistant("Checked disk usage."), rate_limit("rejected"), result()],
        )

        assert fake.interrupted

    async def test_an_unnamed_window_still_reads_as_a_sentence(
        self, db, ai_provider, make_session
    ) -> None:
        """``rate_limit_type`` is optional. Substituting a placeholder for
        it produced "the plan's plan rate limit"."""
        session = await make_session()
        outcome, _, _ = await _run(
            db,
            session,
            ai_provider,
            [
                assistant("Checked disk usage."),
                rate_limit("rejected", rate_limit_type=None),
                result(),
            ],
        )

        assert "the plan's rate limit" in (outcome.stopped_by or "")

    async def test_an_unknown_window_still_names_itself(
        self, db, ai_provider, make_session
    ) -> None:
        """A rate-limit window this version has not heard of is still the
        reason the run stopped."""
        session = await make_session()
        outcome, _, _ = await _run(
            db,
            session,
            ai_provider,
            [
                assistant("Checked disk usage."),
                rate_limit("rejected", rate_limit_type="seven_day_haiku"),
                result(),
            ],
        )

        assert "seven_day_haiku" in (outcome.stopped_by or "")


class TestTheQuotaWarning:
    """`allowed_warning` is advance notice, not a stop. It is the only
    warning a subscription provider gets: `budget_warning` is computed from
    spend that, for these, is an estimate of money nobody pays."""

    async def test_a_warning_is_surfaced_and_the_run_continues(
        self, db, ai_provider, make_session
    ) -> None:
        session = await make_session()
        events: list = []
        outcome, _, _ = await _run(
            db,
            session,
            ai_provider,
            [
                rate_limit("allowed_warning", utilization=0.85),
                assistant("All healthy."),
                result(),
            ],
            events=events,
        )

        warnings = [payload for name, payload in events if name == "rate_limit_warning"]
        assert len(warnings) == 1
        assert "85% used" in warnings[0]["message"]
        assert warnings[0]["utilization"] == 0.85
        assert outcome.status == "succeeded"
        assert outcome.stopped_by == "", "a warning is not a stop"

    async def test_a_warning_without_a_figure_still_warns(
        self, db, ai_provider, make_session
    ) -> None:
        session = await make_session()
        events: list = []
        await _run(
            db,
            session,
            ai_provider,
            [rate_limit("allowed_warning", utilization=None), assistant("ok"), result()],
            events=events,
        )

        warnings = [payload for name, payload in events if name == "rate_limit_warning"]
        assert len(warnings) == 1
        assert "nearly used up" in warnings[0]["message"]

    async def test_the_same_window_warns_once(self, db, ai_provider, make_session) -> None:
        """The CLI re-emits on every transition. A banner that reappears
        each turn reads as a new problem rather than the same one."""
        session = await make_session()
        events: list = []
        await _run(
            db,
            session,
            ai_provider,
            [
                rate_limit("allowed_warning", utilization=0.85),
                assistant("one"),
                rate_limit("allowed_warning", utilization=0.9),
                assistant("two"),
                result(),
            ],
            events=events,
        )

        assert len([1 for name, _ in events if name == "rate_limit_warning"]) == 1

    async def test_a_second_window_warns_separately(self, db, ai_provider, make_session) -> None:
        """Deduping on the window rather than on "have we warned at all"
        — the 5-hour and 7-day limits are different problems with
        different answers."""
        session = await make_session()
        events: list = []
        await _run(
            db,
            session,
            ai_provider,
            [
                rate_limit("allowed_warning", utilization=0.85),
                assistant("one"),
                rate_limit("allowed_warning", rate_limit_type="seven_day", utilization=0.95),
                assistant("two"),
                result(),
            ],
            events=events,
        )

        windows = [p["window"] for name, p in events if name == "rate_limit_warning"]
        assert windows == ["5-hour", "7-day"]

    async def test_an_allowed_event_is_routine(self, db, ai_provider, make_session) -> None:
        """The CLI emits on every transition, including back down to
        normal. A run inside its quota has nothing to report."""
        session = await make_session()
        events: list = []
        outcome, _, _ = await _run(
            db,
            session,
            ai_provider,
            [rate_limit("allowed", utilization=0.2), assistant("All healthy."), result()],
            events=events,
        )

        assert not [1 for name, _ in events if name == "rate_limit_warning"]
        assert outcome.status == "succeeded"
        assert outcome.stopped_by == ""


class TestAnInterruptedCallbackLeavesTheSessionUsable:
    """BUG-94: interrupting the SDK cancels whichever tool callback is in
    flight, and a cancellation inside a flush left the shared session
    needing a rollback nothing issued. The next query, ``_cancelled()`` in
    ``run``, raised ``PendingRollbackError``, and the session failed with
    that instead of stopping for its cap. Session 17 on lin-manager: token
    cap hit while ``run_ssh_command`` was being recorded."""

    OVER_THE_CAP = {"input_tokens": 500, "output_tokens": 10}

    @staticmethod
    def _handler(runner, run):
        import dataclasses

        return dataclasses.replace(runner._permitted["list_hosts"], run=run)

    @staticmethod
    async def _slow_inserts(db) -> None:
        """Make every insert into ai_tool_calls take long enough to be
        cancelled in the middle of it. Rolled back with the test."""
        from sqlalchemy import text

        await db.execute(
            text(
                "CREATE FUNCTION bug94_slow() RETURNS trigger AS $$ "
                "BEGIN PERFORM pg_sleep(0.5); RETURN NEW; END $$ LANGUAGE plpgsql"
            )
        )
        await db.execute(
            text(
                "CREATE TRIGGER bug94_slow BEFORE INSERT ON ai_tool_calls "
                "FOR EACH ROW EXECUTE FUNCTION bug94_slow()"
            )
        )

    @staticmethod
    async def _cancel_midway(coro, after: float = 0.2) -> None:
        import asyncio

        task = asyncio.ensure_future(coro)
        await asyncio.sleep(after)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def _run_with(self, db, session, provider_row, on_query_for, messages, fake=None):
        from app.ai.loop import LoopCaps

        fake = fake or FakeSDKClient(messages)
        runner = AgentSDKRunner(
            db,
            session,
            provider_row,
            LoopCaps(max_tokens_total=100),
            client_factory=fake.factory,
        )
        on_query = on_query_for(runner)
        if on_query is not None:
            # Once: the wrap-up turn after a cap asks the same fake again.
            done = []

            async def once():
                if not done:
                    done.append(True)
                    await on_query()

            fake._on_query = once
        return await runner.run(), runner

    async def _calls(self, db, session):
        return (
            (await db.execute(select(AIToolCall).where(AIToolCall.session_id == session.id)))
            .scalars()
            .all()
        )

    async def test_a_cancel_mid_flush_stops_the_run_for_its_reason(
        self, db, ai_provider, make_session
    ) -> None:
        session = await make_session()
        await self._slow_inserts(db)
        ran = []

        async def run_tool(ctx, arguments):  # noqa: ANN001
            ran.append(arguments)
            raise AssertionError("an interrupted call must not go on to run")

        def on_query_for(runner):
            async def on_query():
                await self._cancel_midway(runner._execute_tool(self._handler(runner, run_tool), {}))

            return on_query

        outcome, _ = await self._run_with(
            db,
            session,
            ai_provider,
            on_query_for,
            [assistant("checking chronyd", self.OVER_THE_CAP), result()],
        )

        assert outcome.status == "succeeded", outcome
        assert "token budget" in (outcome.stopped_by or "")
        await db.refresh(session)
        assert session.stopped_reason and "token budget" in session.stopped_reason
        assert ran == []
        [call] = await self._calls(db, session)
        assert call.status == "error"
        assert call.result_summary.startswith("Interrupted")
        assert call.finished_at is not None

    async def test_a_call_cancelled_while_running_is_closed_as_interrupted(
        self, db, ai_provider, make_session
    ) -> None:
        import asyncio

        session = await make_session()
        finished = []

        async def run_tool(ctx, arguments):  # noqa: ANN001
            await asyncio.sleep(5)
            finished.append(True)

        def on_query_for(runner):
            async def on_query():
                await self._cancel_midway(runner._execute_tool(self._handler(runner, run_tool), {}))

            return on_query

        outcome, _ = await self._run_with(
            db, session, ai_provider, on_query_for, [assistant("done"), result()]
        )

        assert outcome.status == "succeeded", outcome
        assert finished == []
        [call] = await self._calls(db, session)
        assert (call.status, call.result_summary) == ("error", _INTERRUPTED)

    async def test_a_broken_session_is_rolled_back_before_it_is_used(
        self, db, ai_provider, make_session
    ) -> None:
        """Covers what the shielding cannot: a tool's own queries, and the
        driver loop on a wall-clock timeout, can still be cancelled
        mid-operation. Broken here with a failed flush as the interrupt
        goes out, which is when the real one broke it and leaves the
        session in the same state."""
        from sqlalchemy.exc import IntegrityError

        class BreaksTheSessionOnInterrupt(FakeSDKClient):
            async def interrupt(self) -> None:
                self.interrupted = True
                db.add(AIToolCall(session_id=2_000_000_000, tool_name="x"))
                with pytest.raises(IntegrityError):
                    await db.flush()

        session = await make_session()
        fake = BreaksTheSessionOnInterrupt(
            [assistant("one", self.OVER_THE_CAP), assistant("two"), result()]
        )

        outcome, _ = await self._run_with(
            db, session, ai_provider, lambda runner: None, [], fake=fake
        )

        assert outcome.status == "succeeded", outcome
        assert "token budget" in (outcome.stopped_by or "")
        await db.refresh(session)
        assert session.status == "succeeded"
        assert session.stopped_reason and "token budget" in session.stopped_reason

    async def test_an_error_while_stopping_keeps_the_stop_reason(
        self, db, ai_provider, make_session
    ) -> None:
        class FailsOnInterrupt(FakeSDKClient):
            async def interrupt(self) -> None:
                raise RuntimeError("control request failed")

        session = await make_session()
        fake = FailsOnInterrupt([assistant("one", self.OVER_THE_CAP), result()])

        outcome, _ = await self._run_with(
            db, session, ai_provider, lambda runner: None, [], fake=fake
        )

        assert outcome.status == "succeeded", outcome
        assert "token budget" in (outcome.stopped_by or "")


class TestUninterruptible:
    async def test_the_work_finishes_and_the_caller_is_still_cancelled(self) -> None:
        import asyncio

        from app.ai.agent_sdk.runner import _uninterruptible

        steps = []

        async def work():
            steps.append("start")
            await asyncio.sleep(0.2)
            steps.append("end")
            return "done"

        caller = asyncio.ensure_future(_uninterruptible(work()))
        await asyncio.sleep(0.05)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert steps == ["start", "end"]

    async def test_without_a_cancel_it_returns_the_result(self) -> None:
        from app.ai.agent_sdk.runner import _uninterruptible

        async def work():
            return 42

        assert await _uninterruptible(work()) == 42


class TestOneResponseIsCountedOnce:
    """BUG-95: the CLI sends one ``AssistantMessage`` per content block, and
    each carries the whole response's usage. Session 17 on lin-manager: two
    responses, each a thinking block and a tool call, about 5,400 tokens,
    counted as 10,712 and stopped at a 10,000 cap."""

    # lin-manager session 17, response by response.
    FIRST = {"input_tokens": 2, "cache_creation_input_tokens": 2582, "output_tokens": 66}
    SECOND = {
        "input_tokens": 2,
        "cache_creation_input_tokens": 114,
        "cache_read_input_tokens": 2582,
        "output_tokens": 8,
    }

    def _session_17(self):
        return [
            assistant("", self.FIRST, message_id="msg_1"),
            assistant("", self.FIRST, message_id="msg_1"),
            assistant("", self.SECOND, message_id="msg_2"),
            assistant("the clock is fine", self.SECOND, message_id="msg_2"),
            result(),
        ]

    async def test_session_17_stays_under_its_cap(self, db, ai_provider, make_session) -> None:
        session = await make_session()
        outcome, fake, runner = await _run(
            db, session, ai_provider, self._session_17(), caps=LoopCaps(max_tokens_total=10_000)
        )

        assert not fake.interrupted, f"stopped at {outcome.stopped_by!r}"
        assert runner._live_prompt + runner._live_completion == 2650 + 2706

    async def test_distinct_responses_still_add_up(self, db, ai_provider, make_session) -> None:
        session = await make_session()
        turn = {"input_tokens": 50, "output_tokens": 10}
        outcome, fake, _ = await _run(
            db,
            session,
            ai_provider,
            [
                assistant("one", turn, message_id="msg_1"),
                assistant("two", turn, message_id="msg_2"),
                result(),
            ],
            caps=LoopCaps(max_tokens_total=100),
        )

        assert fake.interrupted
        assert "token budget" in (outcome.stopped_by or "")

    async def test_an_unfinished_run_books_each_response_once(
        self, db, ai_provider, make_session
    ) -> None:
        """Session 16 booked twice its input tokens: interrupted runs book
        the live estimate, and the estimate was doubled."""
        session = await make_session()
        turn = {"input_tokens": 3000, "output_tokens": 100}
        await _run(
            db,
            session,
            ai_provider,
            [
                assistant("", turn, message_id="msg_1"),
                assistant("", turn, message_id="msg_1"),
                assistant("", turn, message_id="msg_2"),
                assistant("", turn, message_id="msg_2"),
            ],
        )

        await db.refresh(session)
        assert session.prompt_tokens == 6000
        assert session.completion_tokens == 200

    async def test_turns_are_counted_per_response(self, db, ai_provider, make_session) -> None:
        session = await make_session()
        _, _, runner = await _run(db, session, ai_provider, self._session_17()[:-1])
        assert session.iterations == 2
