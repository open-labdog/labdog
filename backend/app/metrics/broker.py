"""Redis broker queue depth for the Prometheus exporter.

Everything else in this exporter is derived from PostgreSQL, and a scrape
that reaches the database has already proved the database is reachable.
The broker is a *second* failure domain, and putting it in the scrape path
is the reason this was scoped out of the exporter's first release: a Redis
that hangs would hang an unauthenticated request. So every call here is
wrapped in a hard timeout, and a broker that does not answer within it is
reported as unreachable rather than waited for.

**What is emitted when the broker does not answer matters.** The queue
depth gauges are *omitted* — not zero-filled. A zero would read as "the
queues are empty", which is the opposite of what is known, and would
silence exactly the alert an operator wants ("work is piling up") at
exactly the moment it should fire. ``labdog_broker_reachable`` carries the
"we could not tell" signal on its own, and a rule that cares about depth
should join on it or use ``absent()``.

Celery *worker* introspection is deliberately out of scope:
``inspect().active()`` is a multi-second broadcast RPC over the broker and
has no place in a scrape. Operators wanting worker-level detail should run
``celery-exporter`` alongside.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from app.celery_manager import CeleryManager
from app.config import settings

logger = logging.getLogger(__name__)

#: Queue depth is read as the length of the Redis list Celery names after
#: the queue. This is the kombu/Redis transport's storage layout, not a
#: Celery API, which is why it is stated here rather than assumed: a task
#: published to queue ``q`` is ``LPUSH``ed onto a list literally called
#: ``q``. If the broker transport ever changes, this module is what breaks
#: and ``labdog_broker_reachable`` is what says so.
QUEUES: tuple[str, ...] = CeleryManager.QUEUES


@dataclass(frozen=True, slots=True)
class BrokerSnapshot:
    """Result of one broker probe.

    ``depths`` is ``None`` — not an empty or zero-filled mapping — when the
    broker could not be reached. The distinction is the whole point of the
    type; see the module docstring.
    """

    reachable: bool
    depths: dict[str, int] | None


async def probe(timeout: float | None = None) -> BrokerSnapshot:
    """Read the depth of every queue a worker consumes, under a hard timeout.

    Never raises. A broker that is down, slow, or speaking a transport this
    does not understand all produce ``BrokerSnapshot(False, None)``.
    """
    budget = settings.metrics.broker_timeout_seconds if timeout is None else timeout
    try:
        return await asyncio.wait_for(_read_depths(), timeout=budget)
    except TimeoutError:
        logger.debug("broker probe timed out after %.3fs", budget)
        return BrokerSnapshot(reachable=False, depths=None)
    except Exception:
        # Connection refused, auth failure, DNS, a non-Redis broker URL —
        # all the same answer to a scrape: we could not tell.
        logger.debug("broker probe failed", exc_info=True)
        return BrokerSnapshot(reachable=False, depths=None)


async def _read_depths() -> BrokerSnapshot:
    import redis.asyncio as redis_async  # noqa: PLC0415

    client = redis_async.from_url(settings.redis.url)
    try:
        # One pipeline, one round trip. Reading the queues one at a time
        # would multiply the timeout budget by the number of queues.
        pipe = client.pipeline()
        for queue in QUEUES:
            pipe.llen(queue)
        lengths = await pipe.execute()
    finally:
        await client.aclose()

    return BrokerSnapshot(
        reachable=True,
        depths={queue: int(length) for queue, length in zip(QUEUES, lengths, strict=True)},
    )
