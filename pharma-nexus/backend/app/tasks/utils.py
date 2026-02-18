"""Shared utilities for Celery task modules.

Provides async helpers that create task-local SQLAlchemy engines and
sessions, avoiding "Future attached to a different loop" errors when
Celery's fork-pool workers create new event loops per task.
"""

import asyncio
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


def run_async(coro):
    """Run an async coroutine from a sync Celery task.

    Creates a fresh event loop and sets it as the current loop so that
    all async resources created inside the coroutine are bound to it.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@asynccontextmanager
async def task_session():
    """Async context manager that yields a task-local AsyncSession.

    Creates a disposable engine + session factory scoped to the current
    event loop.  The engine is disposed when the context exits, so
    connection-pool state never leaks across Celery tasks.

    Usage inside a Celery task::

        async def _work():
            async with task_session() as session:
                ...

        result = run_async(_work())
    """
    engine = create_async_engine(
        settings.database_url,
        echo=settings.app_debug,
        pool_size=5,
        max_overflow=3,
        pool_pre_ping=True,
    )
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()
