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
