"""Make timestamp columns timezone-aware.

Converts TIMESTAMP WITHOUT TIME ZONE columns to TIMESTAMP WITH TIME ZONE
in tables where application code writes timezone-aware datetimes
(datetime.now(timezone.utc)).  asyncpg strictly rejects tz-aware Python
datetimes for naive TIMESTAMP columns, causing errors like:

    "can't subtract offset-naive and offset-aware datetimes"

Affected tables: pipeline_runs, ingestion_logs, gnn_training_runs.

Revision ID: 014
Revises: 013
Create Date: 2026-02-23
"""

from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None

# Raw SQL is used here instead of op.alter_column() to avoid rendering
# issues with existing_server_default=func.now() across Alembic versions.

_UPGRADE_COLUMNS = [
    ("pipeline_runs", "started_at"),
    ("pipeline_runs", "completed_at"),
    ("ingestion_logs", "started_at"),
    ("ingestion_logs", "completed_at"),
    ("ingestion_logs", "data_downloaded_at"),
    ("gnn_training_runs", "created_at"),
    ("gnn_training_runs", "completed_at"),
]


def upgrade() -> None:
    for table, column in _UPGRADE_COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column}"
            f" TYPE TIMESTAMP WITH TIME ZONE"
            f" USING {column} AT TIME ZONE 'UTC'"
        )


def downgrade() -> None:
    for table, column in _UPGRADE_COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column}"
            f" TYPE TIMESTAMP WITHOUT TIME ZONE"
        )
