"""The verify step, end to end, on a scripted provider.

Exercises the real path — session creation, the agent loop, cost
accounting, the verdict — with no network and no model. What these are
really checking is that a verify session cannot become an investigation:
it gets no tools, it is asked its own question rather than the chat
agent's, and every way of not reaching a verdict lands on the same
policy rather than on a quiet pass.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.ai.evidence import EvidenceItem
from app.ai.models import AIMessage, AISession, AIToolCall
from app.ai.verdict import FAILED, INCONCLUSIVE, PASSED
from app.ai.verify import SYSTEM_PROMPT, build_prompt, run_verify_session
from app.models.app_setting import AppSetting
from app.settings_service import invalidate_cache
from tests.ai.fake_provider import FakeProvider, ScriptedTurn, call
from tests.conftest import create_host, create_ssh_key

EVIDENCE = [
    EvidenceItem.reading("Managed services", "- nginx: active", "ssh: systemctl is-active"),
    EvidenceItem.optional("System load (1 minute)", 0.42, "ssh: cat /proc/loadavg"),
]


def _fake(*replies: str, tool_calls=None):
    """A provider that answers with ``replies``, one per turn."""
    turns = [ScriptedTurn(text=reply, tool_calls=list(tool_calls or [])) for reply in replies]
    return FakeProvider(turns)


async def _set(db, key: str, value: str) -> None:
    """Write a setting and drop the in-process cache, which has a 60s TTL."""
    existing = (
        await db.execute(select(AppSetting).where(AppSetting.key == key))
    ).scalar_one_or_none()
    if existing:
        existing.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    await db.flush()
    invalidate_cache(key)


async def _verify(db, fake, *, fail_closed: bool = False, **kwargs):
    """Run one verify session against a scripted provider."""
    with patch("app.ai.loop.build_provider", return_value=fake):
        return await run_verify_session(
            db,
            hostname="jellyfin",
            ip="10.0.0.9",
            instructions="Confirm nginx survived the upgrade.",
            evidence=EVIDENCE,
            fail_closed=fail_closed,
            **kwargs,
        )


@pytest.fixture
async def ai_on(db):
    """The kill switch, on. Sessions are refused without it."""
    await _set(db, "ai.enabled", "1")


class TestReachingAVerdict:
    async def test_a_pass_passes(self, db, ai_provider, ai_on) -> None:
        outcome = await _verify(db, _fake("PASS — nginx is active and the journal is quiet."))
        assert outcome.verdict == PASSED
        assert outcome.passed is True
        assert outcome.session_id is not None

    async def test_a_fail_fails(self, db, ai_provider, ai_on) -> None:
        outcome = await _verify(db, _fake("FAIL — nginx did not come back up."))
        assert outcome.verdict == FAILED
        assert outcome.passed is False

    async def test_an_english_pass_does_not_roll_a_host_back(self, db, ai_provider, ai_on) -> None:
        """The end-to-end form of the parser regression: this reply used
        to reach ``passed=False``, and a failed verification restores a
        snapshot."""
        outcome = await _verify(db, _fake("Everything looks fine; nothing failed."))
        assert outcome.verdict == INCONCLUSIVE
        assert outcome.passed is True

    async def test_the_same_reply_fails_a_fail_closed_action(self, db, ai_provider, ai_on) -> None:
        outcome = await _verify(
            db, _fake("Everything looks fine; nothing failed."), fail_closed=True
        )
        assert outcome.passed is False


class TestTheSessionItRuns:
    async def test_it_is_recorded_as_a_verify_session(self, db, ai_provider, ai_on) -> None:
        """So it appears in the assistant UI, counts against the budget,
        and is auditable — none of which the old subprocess call was."""
        outcome = await _verify(db, _fake("PASS — fine."))
        session = await db.get(AISession, outcome.session_id)
        assert session.mode == "verify"
        assert session.autonomy_level == "read_only"
        assert session.status == "succeeded"

    async def test_it_records_the_host_it_judged(self, db, ai_provider, ai_on) -> None:
        key = await create_ssh_key(db)
        target = await create_host(db, ssh_key_id=key.id)
        outcome = await _verify(db, _fake("PASS — fine."), host_id=target.id)
        session = await db.get(AISession, outcome.session_id)
        assert session.target_host_ids == [target.id]

    async def test_the_spend_is_recorded(self, db, ai_provider, ai_on) -> None:
        outcome = await _verify(db, _fake("PASS — fine."))
        session = await db.get(AISession, outcome.session_id)
        assert session.prompt_tokens > 0

    async def test_it_gets_the_verify_prompt_not_the_chat_agent_s(
        self, db, ai_provider, ai_on
    ) -> None:
        """The chat prompt tells the model to "start by finding out what
        is in scope with list_hosts" and to base every claim on a tool
        result. Handed to a session with no tools, that instruction is
        how a run invents its findings."""
        outcome = await _verify(db, _fake("PASS — fine."))
        system = (
            await db.execute(
                select(AIMessage.content).where(
                    AIMessage.session_id == outcome.session_id, AIMessage.role == "system"
                )
            )
        ).scalar_one()
        assert system == SYSTEM_PROMPT
        assert "list_hosts" not in system
        assert "INCONCLUSIVE" in system

    async def test_the_evidence_reaches_the_provider(self, db, ai_provider, ai_on) -> None:
        fake = _fake("PASS — fine.")
        await _verify(db, fake)
        sent, _ = fake.calls[0]
        user_turn = next(m for m in sent if m.role == "user")
        assert "cat /proc/loadavg" in user_turn.content
        assert "Confirm nginx survived the upgrade." in user_turn.content


class TestItCannotGoLookingAround:
    async def test_no_tools_are_offered(self, db, ai_provider, ai_on) -> None:
        """A verify verdict can restore a snapshot. A session deciding
        that must not also be opening SSH connections to the host it is
        deciding about."""
        fake = _fake("PASS — fine.")
        await _verify(db, fake)
        _, tools = fake.calls[0]
        assert tools == []

    def test_the_mode_is_what_withholds_them(self) -> None:
        """``allowed_tools=[]`` is not enough on its own — an empty
        allowlist still yields ALWAYS_ALLOWED, which is right for a
        narrowed investigation and wrong here."""
        from app.ai.tools import tools_for_session

        assert tools_for_session("read_only", [], mode="chat") != []
        assert tools_for_session("read_only", [], mode="verify") == []
        assert tools_for_session("read_only", None, mode="verify") == []

    async def test_a_tool_call_from_a_verify_session_is_refused(
        self, db, ai_provider, ai_on
    ) -> None:
        """Belt and braces. A model can ask for a tool it was never
        offered — the specs are a suggestion, the allowlist check in
        ``_run_tool`` is the gate — so the one that matters is that
        nothing runs."""
        fake = _fake(
            "Let me check.",
            "PASS — fine.",
            tool_calls=[call("run_ssh_command", host_id=1, command="reboot")],
        )
        outcome = await _verify(db, fake)
        assert outcome.verdict == PASSED

        rows = (
            (
                await db.execute(
                    select(AIToolCall).where(AIToolCall.session_id == outcome.session_id)
                )
            )
            .scalars()
            .all()
        )
        assert [row.status for row in rows] == ["blocked"]


class TestNotReachingAVerdict:
    async def test_ai_switched_off_is_inconclusive_not_a_pass(self, db, ai_provider) -> None:
        """``ai.enabled`` defaults to 0. The old step returned a pass
        here, which meant a fail-closed action could be let through by a
        setting nobody had touched."""
        outcome = await _verify(db, _fake("PASS — fine."), fail_closed=True)
        assert outcome.verdict == INCONCLUSIVE
        assert outcome.passed is False
        assert outcome.session_id is None

    async def test_the_same_case_still_passes_an_open_action(self, db, ai_provider) -> None:
        outcome = await _verify(db, _fake("PASS — fine."))
        assert outcome.passed is True

    async def test_no_session_row_is_written_for_a_run_that_cannot_start(
        self, db, ai_provider
    ) -> None:
        before = len((await db.execute(select(AISession.id))).scalars().all())
        await _verify(db, _fake("PASS — fine."))
        after = len((await db.execute(select(AISession.id))).scalars().all())
        assert after == before

    async def test_a_provider_error_is_inconclusive(self, db, ai_provider, ai_on) -> None:
        fake = FakeProvider([ScriptedTurn(error="connection refused")])
        outcome = await _verify(db, fake, fail_closed=True)
        assert outcome.verdict == INCONCLUSIVE
        assert outcome.passed is False
        # The session still exists, so the failure is auditable.
        assert outcome.session_id is not None

    async def test_a_budget_stop_is_inconclusive(self, db, ai_provider, ai_on) -> None:
        await _set(db, "ai.budget_daily", "0.01")
        from app.ai import service

        await service.record_usage(
            db, provider_id=ai_provider.id, prompt_tokens=1, completion_tokens=1, cost=5.0
        )
        await db.flush()

        outcome = await _verify(db, _fake("PASS — fine."), fail_closed=True)
        assert outcome.verdict == INCONCLUSIVE
        assert outcome.passed is False


class TestThePromptItself:
    def test_a_missing_instruction_falls_back_to_the_generic_question(self) -> None:
        """Reached when verification runs because journal errors turned
        up rather than because someone configured a prompt."""
        text = build_prompt(hostname="h", ip="1.2.3.4", instructions="", evidence=EVIDENCE)
        assert "No specific checks were configured" in text

    def test_the_host_is_named(self) -> None:
        text = build_prompt(hostname="jellyfin", ip="10.0.0.9", instructions="x", evidence=EVIDENCE)
        assert "jellyfin" in text
        assert "10.0.0.9" in text
