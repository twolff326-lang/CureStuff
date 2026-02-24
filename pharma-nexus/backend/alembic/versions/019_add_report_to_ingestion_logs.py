"""Add report JSONB column to ingestion_logs.

Stores comprehensive execution reports for every ingestion run:
phases, HTTP stats, data quality, timeline, warnings, cache stats,
and guard decisions — enabling full diagnostics regardless of whether
the run encountered formal errors.

Revision ID: 019
Revises: 018
Create Date: 2026-02-24
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ingestion_logs",
        sa.Column(
            "report",
            JSONB,
            nullable=True,
            comment="Comprehensive execution report: phases, HTTP stats, data quality, timeline, warnings",
        ),
    )


def downgrade() -> None:
    op.drop_column("ingestion_logs", "report")
