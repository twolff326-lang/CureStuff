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
import sqlalchemy as sa

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pipeline_runs
    op.alter_column(
        "pipeline_runs", "started_at",
        type_=sa.DateTime(timezone=True),
        existing_type=sa.DateTime(),
        existing_server_default=sa.func.now(),
        existing_nullable=False,
    )
    op.alter_column(
        "pipeline_runs", "completed_at",
        type_=sa.DateTime(timezone=True),
        existing_type=sa.DateTime(),
        existing_nullable=True,
    )

    # ingestion_logs
    op.alter_column(
        "ingestion_logs", "started_at",
        type_=sa.DateTime(timezone=True),
        existing_type=sa.DateTime(),
        existing_server_default=sa.func.now(),
        existing_nullable=False,
    )
    op.alter_column(
        "ingestion_logs", "completed_at",
        type_=sa.DateTime(timezone=True),
        existing_type=sa.DateTime(),
        existing_nullable=True,
    )
    op.alter_column(
        "ingestion_logs", "data_downloaded_at",
        type_=sa.DateTime(timezone=True),
        existing_type=sa.DateTime(),
        existing_nullable=True,
    )

    # gnn_training_runs
    op.alter_column(
        "gnn_training_runs", "created_at",
        type_=sa.DateTime(timezone=True),
        existing_type=sa.DateTime(),
        existing_server_default=sa.func.now(),
        existing_nullable=False,
    )
    op.alter_column(
        "gnn_training_runs", "completed_at",
        type_=sa.DateTime(timezone=True),
        existing_type=sa.DateTime(),
        existing_nullable=True,
    )


def downgrade() -> None:
    # gnn_training_runs
    op.alter_column(
        "gnn_training_runs", "completed_at",
        type_=sa.DateTime(),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=True,
    )
    op.alter_column(
        "gnn_training_runs", "created_at",
        type_=sa.DateTime(),
        existing_type=sa.DateTime(timezone=True),
        existing_server_default=sa.func.now(),
        existing_nullable=False,
    )

    # ingestion_logs
    op.alter_column(
        "ingestion_logs", "data_downloaded_at",
        type_=sa.DateTime(),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=True,
    )
    op.alter_column(
        "ingestion_logs", "completed_at",
        type_=sa.DateTime(),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=True,
    )
    op.alter_column(
        "ingestion_logs", "started_at",
        type_=sa.DateTime(),
        existing_type=sa.DateTime(timezone=True),
        existing_server_default=sa.func.now(),
        existing_nullable=False,
    )

    # pipeline_runs
    op.alter_column(
        "pipeline_runs", "completed_at",
        type_=sa.DateTime(),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=True,
    )
    op.alter_column(
        "pipeline_runs", "started_at",
        type_=sa.DateTime(),
        existing_type=sa.DateTime(timezone=True),
        existing_server_default=sa.func.now(),
        existing_nullable=False,
    )
