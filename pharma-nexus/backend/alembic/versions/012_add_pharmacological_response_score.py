"""Add pharmacological_response_score column to hypotheses table.

Stores the PRISM/GDSC drug sensitivity screen score (0-100) for each
drug-cancer hypothesis. This dimension measures direct pharmacological
response evidence from cancer cell line viability screens.

Revision ID: 012
Revises: 011
Create Date: 2026-02-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def _column_exists(conn, table: str, column: str) -> bool:
    if not inspect(conn).has_table(table):
        return False
    cols = [c["name"] for c in inspect(conn).get_columns(table)]
    return column in cols


def upgrade() -> None:
    conn = op.get_bind()

    if not _column_exists(conn, "hypotheses", "pharmacological_response_score"):
        op.add_column(
            "hypotheses",
            sa.Column(
                "pharmacological_response_score",
                sa.Float(),
                server_default="0.0",
                comment="PRISM/GDSC drug sensitivity screen score (0-100)",
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, "hypotheses", "pharmacological_response_score"):
        op.drop_column("hypotheses", "pharmacological_response_score")
