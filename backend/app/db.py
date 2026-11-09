import asyncio
import logging
from collections.abc import AsyncGenerator, Coroutine
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

engine = create_async_engine(
    settings.database.url,
    echo=False,
    pool_size=settings.database.pool_size,
    max_overflow=settings.database.max_overflow,
    pool_timeout=settings.database.pool_timeout,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

logger = logging.getLogger(__name__)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        # Keeps the synchronous readers current in a long-lived API process.
        # The lifespan warms the cache once at startup; without this it would
        # never see a setting the operator changed afterwards. TTL-gated, so
        # this is at most one SELECT a minute, not one a request.
        from app.settings_service import ensure_settings_cache  # noqa: PLC0415

        try:
            await ensure_settings_cache(session)
        except Exception:
            logger.warning("could not refresh the settings cache", exc_info=True)
        yield session


async def release_connection(session: AsyncSession) -> None:
    """Hand the session's pooled connection back before a long wait.

    For a request that reads what it needs and then waits on something
    else — a live stream, SSH to a host. A ``get_db`` session is closed
    only after the response has been sent, so without this its connection
    stays checked out for the whole wait, and fifteen such requests take
    every connection in the default pool (BUG-152, BUG-153).

    A commit, not a close or rollback. Nothing is pending at the call
    sites, so it changes nothing in the database, and unlike a rollback it
    keeps rows the request (or a test's savepoint-wrapped session) has
    flushed. The session stays usable: its next query checks a connection
    out again, for as long as that query takes.
    """
    await session.commit()


#: The engine ``task_session()`` takes its sessions from inside
#: :func:`with_task_engine`, with the event loop it belongs to.
_task_engine: ContextVar[tuple[AsyncEngine, asyncio.AbstractEventLoop] | None] = ContextVar(
    "labdog_task_engine", default=None
)


def _shared_task_engine() -> AsyncEngine | None:
    """The engine :func:`with_task_engine` opened for the running loop, if any.

    Checked against the loop, not just looked up: a context variable is
    copied into threads (``asyncio.to_thread``), and an asyncpg connection
    from one loop must never be used from another.
    """
    current = _task_engine.get()
    if current is None:
        return None
    engine, loop = current
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        return None
    return engine if running is loop else None


async def with_task_engine[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a Celery task's coroutine with one engine for all its sessions.

    Every task wrapper runs its body as
    ``asyncio.run(with_task_engine(_body(...)))``. Each ``task_session()``
    block inside it then takes a pooled connection from this engine
    instead of building, connecting and disposing an engine of its own
    (BUG-156: 28 ms against 0.6 ms a block, and a new Postgres backend
    each time; a sync opened five or six, the action orchestrator one
    every five seconds for the life of a run).

    One engine per ``asyncio.run``, not per process: asyncpg connections
    belong to the loop that opened them, and each task runs its own loop.
    The engine is made inside the loop and disposed before it closes, so
    nothing outlives the task, and a forked worker never inherits a
    connection. The overflow is unbounded, as it was with one engine per
    block, so blocks that nest or run concurrently never wait for a
    connection; two stay pooled for the blocks that follow one another.
    """
    if _shared_task_engine() is not None:
        return await coro
    try:
        engine = create_async_engine(
            settings.database.url,
            echo=False,
            pool_size=2,
            max_overflow=-1,
            pool_pre_ping=True,
        )
    except BaseException:
        coro.close()
        raise
    token = _task_engine.set((engine, asyncio.get_running_loop()))
    try:
        return await coro
    finally:
        _task_engine.reset(token)
        await engine.dispose()


@asynccontextmanager
async def task_session():
    """A session for use in Celery task workers.

    asyncpg connections are not fork-safe, so Celery workers must not
    reuse the module-level engine that was created in the parent process.
    Inside :func:`with_task_engine` the session comes from the engine it
    opened for this task; outside one (a test, a script) a disposable
    single-use engine is built for the block, as before.
    """
    shared = _shared_task_engine()
    engine = shared or create_async_engine(
        settings.database.url,
        echo=False,
        pool_size=2,
        max_overflow=0,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            # Warm the settings cache so the synchronous readers in this
            # worker see operator-configured values rather than hardcoded
            # defaults. Nothing else fills it in a Celery process, which is
            # why ten UI-editable settings silently did nothing here.
            #
            # TTL-gated inside ensure_settings_cache, so this costs at most
            # one SELECT per minute per process, not one per task. Imported
            # lazily: app.settings_service pulls in the model layer, and
            # app.db must stay importable from anywhere.
            from app.settings_service import ensure_settings_cache  # noqa: PLC0415

            try:
                await ensure_settings_cache(session)
            except Exception:
                # Never fail a task because the settings read failed — but
                # say so, because the consequence is silently running on
                # defaults, which is the bug this replaced.
                logger.warning(
                    "could not refresh the settings cache; this task may run on default values",
                    exc_info=True,
                )
            yield session
    finally:
        if shared is None:
            await engine.dispose()


# Registers the after-commit session hooks wherever a session is made.
import app.db_after_commit  # noqa: E402, F401
