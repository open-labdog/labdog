"""Beat survives Redis restarting (BUG-83).

Three ways the stock scheduler died, each reproduced against a real
Redis rather than a mock, because the failure is in how redis-py's lock
behaves when its key disappears and a mock would only assert what we
assumed about that:

1. Redis restarted without our data — the lock key and the schedule are
   gone. Stock ``tick()`` raises ``LockNotOwnedError`` and beat exits.
2. Redis is unreachable mid-run. Stock ``tick()`` raises
   ``ConnectionError`` and beat exits.
3. Redis was unreachable when beat *started*. RedBeat swallows the failed
   acquire, leaves ``lock`` as ``None``, and the first tick dies on
   ``None.extend``.

Case 1 is the one that matters most, and it is also where the schedule
has to come back: a Redis that lost the lock lost the entries too, and
the entries are registered once at ``beat_init``.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
import redis
from redbeat import RedBeatScheduler
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import LockNotOwnedError

from app.config import settings
from app.tasks import celery_app
from app.tasks.scheduler import RETRY_INTERVAL, ResilientRedBeatScheduler


@pytest.fixture
def redis_client():
    client = redis.from_url(settings.redis.url)
    try:
        client.ping()
    except RedisConnectionError:
        pytest.skip("no Redis at settings.redis.url; this file needs the real thing")
    yield client
    client.close()


@pytest.fixture
def scheduler(redis_client):
    """A scheduler holding a lock of its own, as it would after a clean start.

    A unique lock key so a parallel or leftover beat cannot interfere,
    and ``lazy=True`` so constructing it does not run ``setup_schedule``
    against the shared keyspace.
    """
    lock_key = f"redbeat-test:lock:{uuid.uuid4().hex}"
    sched = ResilientRedBeatScheduler(app=celery_app, lock_key=lock_key, lazy=True)
    assert sched._reacquire_lock(), "could not take the test lock on a live Redis"
    yield sched
    try:
        if sched.lock is not None and sched.lock.owned():
            sched.lock.release()
    finally:
        redis_client.delete(lock_key)


def _schedule_key() -> str:
    return celery_app.redbeat_conf.schedule_key


def _wipe_redbeat_keyspace(client: redis.Redis) -> None:
    """What a Redis restart without persistence does to RedBeat's keys.

    Every entry is a hash plus a member of the schedule zset, and a real
    flush takes both. Deleting only the zset would leave `ensure_entry`
    finding each hash "already current" and writing nothing — a state no
    restart produces, and one this test must not accidentally assert
    recovery from.
    """
    prefix = celery_app.redbeat_conf.key_prefix
    keys = list(client.scan_iter(f"{prefix}*"))
    if keys:
        client.delete(*keys)


class TestRedisRestartedWithoutOurData:
    """The lock key is gone. That is what ``LockNotOwnedError`` means, and
    it is also the signal that the schedule went with it."""

    def test_tick_survives_and_takes_the_lock_back(self, scheduler, redis_client):
        redis_client.delete(scheduler.lock_key)

        interval = scheduler.tick()

        assert interval == RETRY_INTERVAL
        assert redis_client.exists(scheduler.lock_key), "the lock was not re-acquired"
        assert scheduler.lock is not None and scheduler.lock.owned()

    def test_the_schedule_comes_back(self, scheduler, redis_client):
        """The half a bare re-acquire would miss: a beat that owns its lock
        again but has an empty schedule fires nothing, forever."""
        redis_client.delete(scheduler.lock_key)
        _wipe_redbeat_keyspace(redis_client)
        assert redis_client.zcard(_schedule_key()) == 0

        scheduler.tick()

        assert redis_client.zcard(_schedule_key()) > 0, "no periodic entries were re-registered"

    def test_the_stock_scheduler_really_does_die_here(self, redis_client):
        """Pins the premise. If a RedBeat release starts handling this
        itself, the subclass can shrink."""
        lock_key = f"redbeat-test:lock:{uuid.uuid4().hex}"
        stock = RedBeatScheduler(app=celery_app, lock_key=lock_key, lazy=True)
        lock = redis_client.lock(lock_key, timeout=60)
        assert lock.acquire(blocking=False)
        stock.lock = lock
        try:
            redis_client.delete(lock_key)
            with pytest.raises(LockNotOwnedError):
                stock.tick()
        finally:
            redis_client.delete(lock_key)


class TestRedisUnreachableMidRun:
    def test_tick_returns_a_retry_interval_instead_of_raising(self, scheduler):
        with patch.object(
            RedBeatScheduler,
            "tick",
            side_effect=RedisConnectionError("Connection refused"),
        ):
            interval = scheduler.tick()

        assert interval == RETRY_INTERVAL

    def test_the_lock_object_is_kept_for_when_redis_returns(self, scheduler):
        """If Redis comes back *with* its data the lock is still ours and
        extend() will succeed; dropping it would force a needless
        re-acquire and re-register on every blip."""
        before = scheduler.lock
        with patch.object(
            RedBeatScheduler,
            "tick",
            side_effect=RedisConnectionError("Connection refused"),
        ):
            scheduler.tick()

        assert scheduler.lock is before


class TestRedisUnreachableAtStartup:
    """RedBeat's beat_init handler swallows a failed acquire and leaves
    ``lock`` as None; the stock tick then dies on ``None.extend``."""

    def test_tick_acquires_the_lock_it_never_got(self, redis_client):
        lock_key = f"redbeat-test:lock:{uuid.uuid4().hex}"
        sched = ResilientRedBeatScheduler(app=celery_app, lock_key=lock_key, lazy=True)
        assert sched.lock is None
        try:
            sched.tick()

            assert sched.lock is not None and sched.lock.owned()
            assert redis_client.exists(lock_key)
        finally:
            if sched.lock is not None and sched.lock.owned():
                sched.lock.release()
            redis_client.delete(lock_key)

    def test_a_lock_held_elsewhere_is_not_stolen(self, redis_client):
        """Non-blocking on purpose: a blocking acquire against another
        holder would hang beat, which the supervisor cannot see. Waiting a
        tick and trying again is the behaviour that converges."""
        lock_key = f"redbeat-test:lock:{uuid.uuid4().hex}"
        other = redis_client.lock(lock_key, timeout=60)
        assert other.acquire(blocking=False)
        sched = ResilientRedBeatScheduler(app=celery_app, lock_key=lock_key, lazy=True)
        try:
            interval = sched.tick()

            assert interval == RETRY_INTERVAL
            assert sched.lock is None
            assert other.owned(), "the running scheduler's lock was taken from it"
        finally:
            other.release()
