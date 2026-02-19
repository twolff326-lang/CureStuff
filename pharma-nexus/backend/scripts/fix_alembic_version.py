"""Fix alembic_version table to match migration files on disk.

Handles two known issues:
1. Revision '001_initial' must be renamed to '001' (ID mismatch).
2. The DB may reference a revision that no longer exists on disk
   (e.g. '011'). In that case, stamp back to the latest known
   migration so `alembic upgrade head` can proceed.

Safe to run repeatedly.
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

# Stamp to one before our new migration so it runs on upgrade.
LATEST_REVISION = "008"

# All valid revision IDs in the migration chain.
KNOWN_REVISIONS = {
    "001", "002", "003", "004", "005", "006", "007", "008", "009",
}

try:
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    cur = conn.cursor()

    # 1) Fix the 001_initial -> 001 rename
    cur.execute(
        "UPDATE alembic_version SET version_num = '001' "
        "WHERE version_num = '001_initial'"
    )
    if cur.rowcount:
        print("fix_alembic_version: patched 001_initial -> 001")

    # 2) If the DB points to a revision that doesn't exist on disk,
    #    stamp it to the latest known revision.
    cur.execute("SELECT version_num FROM alembic_version")
    row = cur.fetchone()
    if row and row[0] not in KNOWN_REVISIONS:
        old = row[0]
        cur.execute(
            "UPDATE alembic_version SET version_num = %s",
            (LATEST_REVISION,),
        )
        print(f"fix_alembic_version: stamped {old} -> {LATEST_REVISION} "
              f"(revision {old} not found on disk)")

    cur.close()
    conn.close()
except Exception:
    # Table may not exist yet on first run — that's fine.
    pass
