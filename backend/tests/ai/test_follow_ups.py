"""Writing back to a finished session.

A follow-up is the one turn the first run's code path never sees. On the
Agent SDK path the CLI resumes its own conversation, so the question has to
be handed over explicitly, and the session's limits have to count what the
earlier exchanges already spent.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select

pytest.importorskip("claude_agent_sdk")

from app.ai import service  # noqa: E402
from app.ai.agent_sdk.runner import AgentSDKRunner  # noqa: E402
from app.ai.loop import LoopCaps  # noqa: E402
from app.ai.models import AIMessage, AIProvider  # noqa: E402
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


@dataclass
class _Spent:
    iterations: int = 0
    command_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


class TestWhenASessionCannotTakeAnotherMessage:
    """BUG-124. Commands and tokens count over the whole session, so a
    session that has spent one can only be cut off at its first response."""

    CAPS = LoopCaps(max_iterations=40, max_commands=20, max_tokens_total=100_000)

    def _refusal(self, *, turns_carry_over: bool = True, **spent) -> str | None:
        return self.CAPS.follow_up_refusal(_Spent(**spent), turns_carry_over=turns_carry_over)

    def test_a_session_with_room_left_can(self) -> None:
        assert self._refusal(iterations=39, command_count=19, prompt_tokens=99_999) is None

    def test_tokens_are_prompt_and_completion_together(self) -> None:
        refusal = self._refusal(prompt_tokens=128_153, completion_tokens=8_009)

        assert "token budget (136,162 of 100,000)" in refusal

    def test_commands(self) -> None:
        assert "command limit (20 of 20)" in self._refusal(command_count=20)

    def test_turns_when_the_backend_keeps_counting_them(self) -> None:
        assert "turn limit (40 of 40)" in self._refusal(iterations=40)

    def test_turns_do_not_lock_a_session_whose_count_restarts(self) -> None:
        """The SDK's ``max_turns`` applies to one exchange, so a run that
        used all of them leaves the next one its full allowance."""
        assert self._refusal(iterations=40, turns_carry_over=False) is None

    def test_it_says_what_to_do_about_it(self) -> None:
        refusal = self._refusal(command_count=20)

        assert "Settings → AI" in refusal
        assert "new session" in refusal


class TestTheApi:
    @pytest.fixture
    async def spent(self, db, make_session):
        """A finished session, 1,500 tokens into a limit of 1,000."""
        await _set(db, "ai.enabled", "1")
        await _set(db, "ai.max_tokens_total", "1000")
        session = await make_session("Why did the upgrade fail?")
        session.status = "succeeded"
        session.prompt_tokens = 1500
        session.stopped_reason = "token budget (1000)"
        await db.flush()
        return session

    async def _send(self, client, session, text: str = "And the metrics?"):
        with patch("app.tasks.celery_app.send_task") as send:
            resp = await client.post(
                f"/api/ai/sessions/{session.id}/messages", json={"message": text}
            )
        return resp, send

    async def _user_turns(self, db, session) -> list[str]:
        rows = await db.execute(
            select(AIMessage.content)
            .where(AIMessage.session_id == session.id, AIMessage.role == "user")
            .order_by(AIMessage.seq)
        )
        return list(rows.scalars())

    async def test_the_message_is_refused(self, superuser_client, db, spent) -> None:
        resp, send = await self._send(superuser_client, spent)

        assert resp.status_code == 409
        assert "token budget (1,500 of 1,000)" in resp.json()["detail"]
        send.assert_not_called()

    async def test_a_refused_message_leaves_the_session_as_it_was(
        self, superuser_client, db, spent
    ) -> None:
        await self._send(superuser_client, spent)

        assert spent.status == "succeeded"
        assert spent.stopped_reason == "token budget (1000)"
        assert await self._user_turns(db, spent) == ["Why did the upgrade fail?"]

    async def test_the_session_says_so_before_anyone_types(
        self, superuser_client, db, spent
    ) -> None:
        detail = (await superuser_client.get(f"/api/ai/sessions/{spent.id}")).json()

        assert "token budget (1,500 of 1,000)" in detail["follow_up_refused"]

    async def test_a_session_still_working_is_not_called_spent(
        self, superuser_client, db, spent
    ) -> None:
        """The figures are moving, and it may stop by itself or park. The
        composer is locked anyway."""
        spent.status = "running"
        await db.flush()

        detail = (await superuser_client.get(f"/api/ai/sessions/{spent.id}")).json()

        assert detail["follow_up_refused"] is None

    async def test_raising_the_limit_lets_it_carry_on(self, superuser_client, db, spent) -> None:
        await _set(db, "ai.max_tokens_total", "2000")

        detail = (await superuser_client.get(f"/api/ai/sessions/{spent.id}")).json()
        resp, send = await self._send(superuser_client, spent)

        assert detail["follow_up_refused"] is None
        assert resp.status_code == 200
        send.assert_called_once()

    async def test_a_follow_up_clears_the_last_runs_stop(self, superuser_client, db, spent) -> None:
        """Only a run that stops early writes the column, so one that
        finishes properly would keep showing the old reason."""
        await _set(db, "ai.max_tokens_total", "2000")

        resp, _ = await self._send(superuser_client, spent)

        assert resp.json()["stopped_reason"] is None
        assert await self._user_turns(db, spent) == [
            "Why did the upgrade fail?",
            "And the metrics?",
        ]

    async def test_turns_lock_an_http_backend_only(
        self, superuser_client, db, make_session, ai_provider
    ) -> None:
        await _set(db, "ai.max_iterations", "5")
        sdk_provider = AIProvider(
            name="subscription", provider_type="claude_agent", model="claude", enabled=True
        )
        db.add(sdk_provider)
        await db.flush()

        async def refusal(provider: AIProvider) -> str | None:
            session = await make_session(provider=provider)
            session.status = "succeeded"
            session.iterations = 5
            await db.flush()
            detail = (await superuser_client.get(f"/api/ai/sessions/{session.id}")).json()
            return detail["follow_up_refused"]

        assert "turn limit (5 of 5)" in await refusal(ai_provider)
        assert await refusal(sdk_provider) is None
