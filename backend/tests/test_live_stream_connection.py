"""BUG-152: a live output stream must not hold a database connection.

Both SSE endpoints read one row and then relay Redis pub/sub for as long as
the run lasts. The row was read through the request's ``get_db`` session,
which is closed only after the response has finished streaming, so every
open stream kept a pooled connection checked out. Fifteen streams (the
5 + 10 pool) and every other API request waited out ``pool_timeout`` and
failed.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api._sse import relay_channel
from app.api.actions import stream_run_events
from app.models.action_run import ActionRun


class _FakePubSub:
    def __init__(self, messages: list[dict]):
        self.messages = messages
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def unsubscribe(self, channel: str) -> None:
        self.unsubscribed.append(channel)

    async def listen(self):
        yield {"type": "subscribe", "data": 1}
        for message in self.messages:
            yield {"type": "message", "data": json.dumps(message)}


class _FakeRedis:
    def __init__(self, messages: list[dict]):
        self.pubsub_obj = _FakePubSub(messages)
        self.closed = False

    def pubsub(self) -> _FakePubSub:
        return self.pubsub_obj

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def fake_redis(monkeypatch):
    holder: dict[str, _FakeRedis] = {}

    def install(messages: list[dict]) -> _FakeRedis:
        fake = _FakeRedis(messages)
        holder["fake"] = fake
        monkeypatch.setattr("redis.asyncio.from_url", lambda *_a, **_k: fake)
        return fake

    return install


async def _drain(response) -> list[str]:
    return [chunk async for chunk in response.body_iterator]


async def test_an_open_stream_holds_no_pooled_connection(pg_url, fake_redis):
    """A pool of one: with the stream open, another request still gets the
    connection, at once."""
    engine = create_async_engine(pg_url, pool_size=1, max_overflow=0, pool_timeout=2)
    async with engine.begin() as conn:
        run_id = (
            await conn.execute(
                insert(ActionRun)
                .values(
                    action_key="k",
                    action_version="1",
                    parameters={},
                    parallelism=1,
                    status="running",
                )
                .returning(ActionRun.id)
            )
        ).scalar_one()
    fake = fake_redis([{"event": "output", "text": "hi"}, {"event": "status", "status": "failed"}])
    session = AsyncSession(engine, expire_on_commit=False)
    try:
        response = await stream_run_events(run_id, None, session)

        assert engine.pool.checkedout() == 0
        async with AsyncSession(engine) as other:
            assert await asyncio.wait_for(other.scalar(select(1)), timeout=5) == 1

        frames = await _drain(response)
        assert frames[0].startswith("event: output\n")
        assert frames[-1].startswith("event: status\n")
        assert '"status": "failed"' in frames[-1]
        assert fake.pubsub_obj.unsubscribed == [f"actions.run.{run_id}"]
        assert fake.closed
    finally:
        await session.close()
        async with engine.begin() as conn:
            await conn.execute(delete(ActionRun).where(ActionRun.id == run_id))
        await engine.dispose()


async def test_it_subscribes_before_it_reads_the_status(fake_redis, db):
    """Otherwise a run that publishes its final status in between is read
    as still running and never sees its terminal event."""
    fake = fake_redis([])
    seen: list[list[str]] = []

    async def _status() -> str:
        seen.append(list(fake.pubsub_obj.subscribed))
        return "succeeded"

    response = await relay_channel(
        db, "chan", _status, terminal_states={"succeeded"}, default_event="output", not_found="x"
    )

    assert seen == [["chan"]]
    assert await _drain(response) == ['event: status\ndata: {"status": "succeeded"}\n\n']


async def test_a_missing_subject_is_a_404_and_lets_go_of_redis(fake_redis, db):
    fake = fake_redis([])

    async def _status() -> None:
        return None

    with pytest.raises(HTTPException) as exc:
        await relay_channel(
            db, "chan", _status, terminal_states=set(), default_event="output", not_found="gone"
        )

    assert exc.value.status_code == 404
    assert fake.pubsub_obj.unsubscribed == ["chan"]
    assert fake.closed


async def test_messages_without_an_event_take_the_default(fake_redis, db):
    fake_redis(
        [
            {"text": "a"},
            {"event": "status", "status": "running"},
            {"event": "status", "status": "done"},
        ]
    )

    async def _status() -> str:
        return "running"

    response = await relay_channel(
        db, "chan", _status, terminal_states={"done"}, default_event="text", not_found="x"
    )

    frames = await _drain(response)
    assert [f.split("\n", 1)[0] for f in frames] == [
        "event: text",
        "event: status",
        "event: status",
    ]
