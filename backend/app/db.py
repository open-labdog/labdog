import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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


@asynccontextmanager
async def task_session():
    """Create a fresh engine + session for use in Celery task workers.

    asyncpg connections are not fork-safe, so Celery workers must not
    reuse the module-level engine that was created in the parent process.
    This creates a disposable single-use engine per task invocation.
    """
    task_engine = create_async_engine(
        settings.database.url,
        echo=False,
        pool_size=2,
        max_overflow=0,
    )
    session_factory = async_sessionmaker(task_engine, expire_on_commit=False)
    async with session_factory() as session:
        try:
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
            await task_engine.dispose()


# Registers the after-commit session hooks wherever a session is made.
import app.db_after_commit  # noqa: E402, F401
