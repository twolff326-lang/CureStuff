"""Shared fixtures for pharma-nexus tests.

Provides an in-memory SQLite async engine and session for fast unit tests
without requiring a real PostgreSQL instance.

Registers type compilation overrides so PostgreSQL-specific column types
(JSONB, TSVECTOR, Vector) degrade gracefully to SQLite-compatible equivalents.
Removes PostgreSQL-specific computed columns that cannot be emulated in SQLite.
"""

import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.database import Base

# ---------------------------------------------------------------------------
# Map PostgreSQL-only types to SQLite equivalents
# ---------------------------------------------------------------------------

from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"


@compiles(TSVECTOR, "sqlite")
def _compile_tsvector_sqlite(type_, compiler, **kw):
    return "TEXT"


try:
    from pgvector.sqlalchemy import Vector

    @compiles(Vector, "sqlite")
    def _compile_vector_sqlite(type_, compiler, **kw):
        return "BLOB"
except ImportError:
    pass  # pgvector not installed — not needed for tests


def _strip_pg_computed_columns(metadata):
    """Remove Computed columns and their indexes for SQLite compatibility.

    These columns use PG-only SQL in their GENERATED ALWAYS AS expression
    (e.g. to_tsvector), which causes SQLite's create_all to fail.
    Must be called after all models are imported into the metadata.
    """
    for table in metadata.tables.values():
        removed_col_names = set()
        to_remove = []
        for col in table.columns:
            if getattr(col, "computed", None) is not None:
                to_remove.append(col)
                removed_col_names.add(col.name)
        for col in to_remove:
            table._columns.remove(col)

        # Also remove indexes that reference removed columns
        if removed_col_names:
            indexes_to_remove = []
            for idx in table.indexes:
                idx_col_names = {c.name for c in idx.columns}
                if idx_col_names & removed_col_names:
                    indexes_to_remove.append(idx)
            for idx in indexes_to_remove:
                table.indexes.discard(idx)


@pytest.fixture(scope="session")
def event_loop():
    """Create a single event loop for all tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def engine():
    """In-memory SQLite engine for testing."""
    # Force-import all models so they register with Base.metadata
    import app.models  # noqa: F401

    # Strip PostgreSQL computed columns before creating tables
    _strip_pg_computed_columns(Base.metadata)

    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )

    # Enable foreign keys for SQLite
    @event.listens_for(eng.sync_engine, "connect")
    def _set_fk(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db(engine) -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional test session that rolls back after each test."""
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session
        await session.rollback()
