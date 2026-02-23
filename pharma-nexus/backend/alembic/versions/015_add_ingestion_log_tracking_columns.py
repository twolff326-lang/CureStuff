"""Add data provenance and tracking columns to ingestion_logs.

Adds columns for data source versioning, filtering stats, integrity
checksums, and duration tracking. These columns were added to the
IngestionLog model but never had a corresponding migration.

Revision ID: 015
Revises: 014
Create Date: 2026-02-23
"""

import sqlalchemy as sa
from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ingestion_logs",
        sa.Column("data_source_version", sa.String(200), nullable=True),
    )
    op.add_column(
        "ingestion_logs",
        sa.Column("data_downloaded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ingestion_logs",
        sa.Column("api_url", sa.String(500), nullable=True),
    )
    op.add_column(
        "ingestion_logs",
        sa.Column("records_filtered", sa.Integer(), nullable=True),
    )
    op.add_column(
        "ingestion_logs",
        sa.Column("checksum", sa.String(128), nullable=True),
    )
    op.add_column(
        "ingestion_logs",
        sa.Column("duration_seconds", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ingestion_logs", "duration_seconds")
    op.drop_column("ingestion_logs", "checksum")
    op.drop_column("ingestion_logs", "records_filtered")
    op.drop_column("ingestion_logs", "api_url")
    op.drop_column("ingestion_logs", "data_downloaded_at")
    op.drop_column("ingestion_logs", "data_source_version")
