"""Writing back to a finished session.

A follow-up is the one turn the first run's code path never sees. On the
Agent SDK path the CLI resumes its own conversation, so the question has to
be handed over explicitly, and the session's limits have to count what the
earlier exchanges already spent.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select

pytest.importorskip("claude_agent_sdk")

from app.ai import service  # noqa: E402
from app.ai.agent_sdk.runner import AgentSDKRunner  # noqa: E402
from app.ai.loop import LoopCaps  # noqa: E402
from app.models.app_setting import AppSetting  # noqa: E402
from app.settings_service import invalidate_cache  # noqa: E402
from app.tasks import ai_task  # noqa: E402
from tests.ai.fake_sdk_client import FakeSDKClient, assistant, result  # noqa: E402


@pytest.fixture(autouse=True)
async def _clear_settings_cache():
    invalidate_cache()
    yield
    invalidate_cache()


async def _set(db, key: str, value: str) -> None:
    """Write a setting and drop it from the in-process cache."""
    existing = (
        await db.execute(select(AppSetting).where(AppSetting.key == key))
    ).scalar_one_or_none()
    if existing:
        existing.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    await db.flush()
    invalidate_cache(key)


async def _ask(db, session, text: str) -> None:
    await service.append_message(db, session.id, role="assistant", content="Findings.")
    await service.append_message(db, session.id, role="user", content=text)


class TestWhatTheModelIsAsked:
    async def test_a_first_run_asks_the_mission(self, db, make_session) -> None:
        session = await make_session("Check disk usage.")

        assert await ai_task._follow_up_prompt(db, session) is None

    async def test_a_follow_up_asks_the_question(self, db, make_session) -> None:
        """BUG-122. Session 39 was resumed twice, and the model was sent the
        mission three times and neither question."""
        session = await make_session("Why did the upgrade fail?")
        session.resume_state = {"sdk_session_id": "abc-123"}
        await _ask(db, session, "Why are the metrics not reaching Mimir?")

        assert await ai_task._follow_up_prompt(db, session) == (
            "Why are the metrics not reaching Mimir?"
        )

    async def test_the_newest_question_is_the_one_asked(self, db, make_session) -> None:
        session = await make_session("Why did the upgrade fail?")
        session.resume_state = {"sdk_session_id": "abc-123"}
        await _ask(db, session, "First question.")
        await _ask(db, session, "Second question.")

        assert await ai_task._follow_up_prompt(db, session) == "Second question."

    async def test_a_model_with_nothing_to_resume_gets_the_mission_too(
        self, db, make_session
    ) -> None:
        """The first run died before the CLI gave an id, so the new
        conversation has never heard what this is about."""
        session = await make_session("Why did the upgrade fail?")
        await _ask(db, session, "Try again, please.")

        prompt = await ai_task._follow_up_prompt(db, session)

        assert "Why did the upgrade fail?" in prompt
        assert prompt.endswith("Try again, please.")

    async def test_the_task_hands_the_question_to_the_runner(
        self, db, ai_provider, make_session
    ) -> None:
        """The wiring BUG-122 was missing: the question has to get from the
        transcript to the CLI, through the task that dispatches the run."""
        session = await make_session("Why did the upgrade fail?")
        session.resume_state = {"sdk_session_id": "abc-123"}
        await _ask(db, session, "Why are the metrics not reaching Mimir?")
        await _set(db, "ai.enabled", "1")
        fake = FakeSDKClient([assistant("Alloy cannot reach Mimir."), result()])

        def runner(db_, session_, provider_row, caps, publish, *, prompt=None):
            return AgentSDKRunner(
                db_,
                session_,
                provider_row,
                caps,
                publish=publish,
                client_factory=fake.factory,
                prompt=prompt,
            )

        @asynccontextmanager
        async def task_session():
            yield db

        redis_client = SimpleNamespace(close=lambda: None, publish=lambda *a: None)
        with (
            patch.object(ai_task, "task_session", task_session),
            patch.object(ai_task, "_redis_client", return_value=redis_client),
            patch.object(ai_task, "build_runner", runner),
            patch.object(ai_task, "_report_remediation"),
        ):
            await ai_task._run_session_async(session.id)

        assert fake.prompts == ["Why are the metrics not reaching Mimir?"]


class TestTheTokenCapOnAFollowUp:
    """BUG-123. The cap is for the session, so it has to count the exchanges
    before this one as well as this one's live estimate."""

    async def _run(self, db, session, provider_row, turn: dict, *, cap: int):
        fake = FakeSDKClient([assistant("looking", turn), assistant("more", turn), result()])
        runner = AgentSDKRunner(
            db, session, provider_row, LoopCaps(max_tokens_total=cap), client_factory=fake.factory
        )
        return await runner.run(), fake

    async def test_earlier_exchanges_count(self, db, ai_provider, make_session) -> None:
        """90 booked and 20 live is over a cap of 100, though neither alone is."""
        session = await make_session()
        session.prompt_tokens = 90

        outcome, fake = await self._run(
            db, session, ai_provider, {"input_tokens": 15, "output_tokens": 5}, cap=100
        )

        assert outcome.stopped_by == "token budget (100)"
        assert fake.interrupted

    async def test_a_follow_up_with_room_left_is_left_alone(
        self, db, ai_provider, make_session
    ) -> None:
        session = await make_session()
        session.prompt_tokens = 90

        outcome, fake = await self._run(
            db, session, ai_provider, {"input_tokens": 3, "output_tokens": 1}, cap=1000
        )

        assert not outcome.stopped_by
        assert not fake.interrupted
