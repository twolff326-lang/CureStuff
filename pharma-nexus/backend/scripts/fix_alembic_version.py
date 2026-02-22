"""Fix alembic_version table to match migration files on disk.

Handles three known issues:
1. Revision '001_initial' must be renamed to '001' (ID mismatch).
2. The DB may reference a revision that no longer exists on disk.
   In that case, stamp to the latest known migration.
3. The DB version may lag behind the actual schema (e.g. version says
   '008' but tables from migration 012 already exist).  Detect this by
   probing for schema objects created by later migrations and stamp
   forward so Alembic doesn't try to replay already-applied DDL.

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

# Stamp to latest known migration when the DB has an unknown revision.
LATEST_REVISION = "013"

# All valid revision IDs in the migration chain.
KNOWN_REVISIONS = {
    "001", "002", "003", "004", "005", "006", "007", "008", "009",
    "010", "011", "012", "013",
}

# Schema probes: if the DB has schema objects from later migrations,
# the alembic_version must be stamped forward.  Each entry maps a
# minimum revision to a SQL probe that returns a truthy row when the
# DDL from that migration is already present.
SCHEMA_PROBES = [
    (
        "013",
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_name = 'pipeline_runs'",
    ),
    (
        "012",
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'hypotheses' "
        "AND column_name = 'pharmacological_response_score'",
    ),
    (
        "011",
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_name = 'gnn_training_runs'",
    ),
    (
        "010",
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'ingestion_logs' "
        "AND column_name = 'total_expected'",
    ),
    (
        "009",
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_name = 'gene_dependencies'",
    ),
]

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
        row = (LATEST_REVISION,)

    # 3) Detect version-behind-schema: the version claims an older
    #    revision but the schema already has objects from later ones.
    if row:
        current = row[0]
        detected = current
        for rev, probe in SCHEMA_PROBES:
            if rev <= current:
                # Already at or past this revision; skip.
                continue
            cur.execute(probe)
            if cur.fetchone():
                detected = max(detected, rev)

        if detected != current:
            cur.execute(
                "UPDATE alembic_version SET version_num = %s",
                (detected,),
            )
            print(f"fix_alembic_version: stamped {current} -> {detected} "
                  f"(schema already contains objects from {detected})")

    cur.close()
    conn.close()
except Exception:
    # Table may not exist yet on first run — that's fine.
    pass
