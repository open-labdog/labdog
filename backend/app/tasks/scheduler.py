"""A RedBeat scheduler that survives its Redis restarting.

Celery beat's tick loop (``celery.beat.Service.start``) catches only
``KeyboardInterrupt`` and ``SystemExit``; anything else ends the loop and
the scheduler exits. Stock ``RedBeatScheduler.tick()`` opens with an
unguarded ``self.lock.extend()``, so a Redis restart is fatal twice over:
``ConnectionError`` while Redis is down, and ``LockNotOwnedError`` once
it is back with the lock key gone. A Redis that was down when beat
*started* is a third way — RedBeat's ``beat_init`` handler swallows the
failed acquire and leaves ``lock`` as ``None``, and the first tick dies
on ``None.extend``.

Any of the three used to take every periodic job with it — drift sweeps,
scheduled actions, retention, alert polling — until LabDog was restarted
by hand (BUG-83). Since #140 the death is at least visible; this makes
it not happen.

The recovery is deliberately small. A lost connection is retried on the
next tick. A lost lock is re-acquired without blocking (a blocking
acquire against a lock some other holder owns would hang beat, which the
supervisor cannot see) and the schedule is re-registered, because a lock
key that vanished means Redis came back empty and the entries went with
it — ``register_all`` is idempotent, so on a Redis that kept its data
this is a no-op. Publish failures inside a tick are already logged and
swallowed by ``Scheduler.apply_entry`` and need nothing here.
"""

from __future__ import annotations

import logging
from typing import Any

from redbeat import RedBeatScheduler
from redbeat.schedulers import LUA_EXTEND_TO_SCRIPT, get_redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import LockError
from redis.exceptions import TimeoutError as RedisTimeoutError

logger = logging.getLogger(__name__)

#: Seconds until the next tick after Redis went away or the lock was lost.
#: Short, because every second beat waits is a second no periodic job can
#: fire; long enough not to hammer a Redis that is still coming up.
RETRY_INTERVAL = 5.0


class ResilientRedBeatScheduler(RedBeatScheduler):
    """RedBeat, minus the three ways a Redis restart used to kill it."""

    retry_interval = RETRY_INTERVAL

    def tick(self, *args: Any, **kwargs: Any) -> float:
        if self.lock_key and self.lock is None:
            # Startup acquisition failed and RedBeat swallowed it. The
            # stock tick would now raise AttributeError on `None.extend`.
            if not self._reacquire_lock():
                return self.retry_interval
            self._reregister_schedules()

        try:
            return super().tick(*args, **kwargs)
        except LockError as exc:
            # LockNotOwnedError: the key is gone, so Redis restarted
            # without our data. The schedule went with it.
            logger.warning("beat: lost the RedBeat lock (%s); re-acquiring", exc)
            self.lock = None
            if self._reacquire_lock():
                self._reregister_schedules()
            return self.retry_interval
        except (RedisConnectionError, RedisTimeoutError) as exc:
            logger.warning(
                "beat: Redis unreachable (%s); retrying in %ss", exc, self.retry_interval
            )
            return self.retry_interval

    def _reacquire_lock(self) -> bool:
        """Take the beat lock again. False if Redis is down or someone holds it.

        Mirrors ``redbeat.schedulers.acquire_distributed_beat_lock``
        except that it does not block: RedBeat's startup acquire waits
        forever for a lock held elsewhere, which is the right thing at
        startup (it is how one beat is elected) and the wrong thing here
        (a hung beat is a dead beat the supervisor cannot see).
        """
        try:
            client = get_redis(self.app)
            lock = client.lock(self.lock_key, timeout=self.lock_timeout, sleep=self.max_interval)
            lock.lua_extend = client.register_script(LUA_EXTEND_TO_SCRIPT)
            if not lock.acquire(blocking=False):
                logger.warning("beat: lock %r is held elsewhere; will retry", self.lock_key)
                return False
        except (RedisConnectionError, RedisTimeoutError) as exc:
            logger.warning("beat: could not re-acquire lock, Redis unreachable (%s)", exc)
            return False
        self.lock = lock
        logger.info("beat: re-acquired lock %r", self.lock_key)
        return True

    def _reregister_schedules(self) -> None:
        """Put every periodic entry back. Idempotent on a Redis that kept them."""
        from app.tasks.beat_registry import register_all  # noqa: PLC0415

        results = register_all(self.app)
        failed = sorted(k for k, v in results.items() if v != "ok")
        if failed:
            logger.error("beat: schedule re-registration failed for: %s", ", ".join(failed))
        else:
            logger.info("beat: re-registered %d periodic schedule group(s)", len(results))
