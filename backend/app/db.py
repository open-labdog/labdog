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


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
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
            yield session
        finally:
            await task_engine.dispose()
