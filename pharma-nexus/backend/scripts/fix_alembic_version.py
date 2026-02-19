"""One-time fix: rename alembic revision '001_initial' -> '001'.

Migration 001 was originally stamped with revision ID '001_initial' but
all downstream migrations reference it as '001'. This script patches the
alembic_version table so `alembic upgrade head` can resolve the chain.

Safe to run repeatedly — it's a no-op once the value is already '001'.
"""

import os
import sys

try:
    import psycopg2
except ImportError:
    sys.exit(0)

dsn = os.environ.get("DATABASE_URL_SYNC", "")
if not dsn:
    sys.exit(0)

# psycopg2 wants postgresql:// not postgresql+psycopg2://
dsn = dsn.replace("postgresql+psycopg2://", "postgresql://")

try:
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "UPDATE alembic_version SET version_num = '001' "
        "WHERE version_num = '001_initial'"
    )
    if cur.rowcount:
        print("fix_alembic_version: patched 001_initial -> 001")
    cur.close()
    conn.close()
except Exception:
    # Table may not exist yet on first run — that's fine.
    pass
