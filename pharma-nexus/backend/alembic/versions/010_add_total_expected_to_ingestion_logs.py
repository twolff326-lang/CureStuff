"""Add total_expected column to ingestion_logs.

Stores the expected total record count for an ingestion run so the
frontend can display deterministic "X of Y" progress instead of just
a running count.

Revision ID: 010
Revises: 009
Create Date: 2026-02-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def _column_exists(conn, table: str, column: str) -> bool:
    if not inspect(conn).has_table(table):
        return False
    cols = [c["name"] for c in inspect(conn).get_columns(table)]
    return column in cols


def upgrade() -> None:
    conn = op.get_bind()
    if not _column_exists(conn, "ingestion_logs", "total_expected"):
        op.add_column(
            "ingestion_logs",
            sa.Column("total_expected", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("ingestion_logs", "total_expected")
