"""Relay a Redis pub/sub channel to the browser as Server-Sent Events.

Shared by the live action-run output (``GET /api/actions/runs/{id}/stream``)
and the assistant's session stream (``GET /api/ai/sessions/{id}/stream``):
the worker publishes events to a channel, the endpoint forwards them until
the publisher says the run has ended.

BUG-152: both endpoints used to keep their request's database connection
for as long as the stream stayed open, which is as long as the run lasts.
Fifteen open streams took every connection in the pool and every other
request timed out. :func:`relay_channel` reads the status and releases
the connection before the first byte is streamed.

It also subscribes *before* reading the status. The other way round, a
run that published its final status in between was read as still running
and its terminal event was never received, so that stream stayed open
until the page closed.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Collection

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import release_connection

logger = logging.getLogger(__name__)

_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


async def relay_channel(
    db: AsyncSession,
    channel: str,
    read_status: Callable[[], Awaitable[str | None]],
    *,
    terminal_states: Collection[str],
    default_event: str,
    not_found: str,
) -> StreamingResponse:
    """Subscribe to ``channel``, read the subject's status, and stream.

    ``read_status`` returns the subject's current status, or ``None`` when
    it does not exist (a 404 with ``not_found``). A subject already in one
    of ``terminal_states`` gets that status as its only event. Otherwise
    every published message is forwarded, as the event its ``event`` key
    names (``default_event`` when it has none), until a ``status`` event
    with a terminal status.
    """
    import redis.asyncio as aioredis  # noqa: PLC0415

    from app.config import settings  # noqa: PLC0415

    client = aioredis.from_url(settings.redis.url)
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(channel)
        status = await read_status()
        if status is None:
            raise HTTPException(status_code=404, detail=not_found)
        await release_connection(db)
    except BaseException:
        await _close(client, pubsub, channel)
        raise

    return StreamingResponse(
        _events(client, pubsub, channel, status, terminal_states, default_event),
        media_type="text/event-stream",
        headers=_HEADERS,
    )


async def _events(
    client,
    pubsub,
    channel: str,
    status: str,
    terminal_states: Collection[str],
    default_event: str,
) -> AsyncIterator[str]:
    try:
        if status in terminal_states:
            yield _frame("status", {"status": status})
            return
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            data = json.loads(message["data"])
            event_type = data.get("event", default_event)
            yield _frame(event_type, data)
            if event_type == "status" and data.get("status") in terminal_states:
                return
    finally:
        await _close(client, pubsub, channel)


def _frame(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


async def _close(client, pubsub, channel: str) -> None:
    try:
        await pubsub.unsubscribe(channel)
    except Exception:
        logger.debug("could not unsubscribe from %s", channel, exc_info=True)
    await client.aclose()
