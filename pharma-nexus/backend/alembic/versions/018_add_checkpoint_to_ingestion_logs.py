"""Add checkpoint JSONB column to ingestion_logs.

Long-running connectors (e.g. ChEMBL) save resume checkpoints here so
that Celery retries can continue from the last successful position
instead of restarting from scratch.

Revision ID: 018
Revises: 017
Create Date: 2026-02-24
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ingestion_logs",
        sa.Column(
            "checkpoint",
            JSONB,
            nullable=True,
            comment="Resume checkpoint for long-running connectors (phase, offset, etc.)",
        ),
    )


def downgrade() -> None:
    op.drop_column("ingestion_logs", "checkpoint")
