"""Add pipeline_runs and software_versions tables.

These tables support the unified repurposing pipeline feature:
- pipeline_runs tracks each full pipeline execution with per-phase progress
- software_versions captures package version snapshots for reproducibility

Revision ID: 013
Revises: 012
Create Date: 2026-02-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import JSONB

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)

    if not inspector.has_table("pipeline_runs"):
        op.create_table(
            "pipeline_runs",
            sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
            sa.Column(
                "status",
                sa.String(50),
                nullable=False,
                server_default="running",
                index=True,
            ),
            sa.Column("current_phase", sa.String(100), nullable=True),
            sa.Column("phases", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("config", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column(
                "started_at",
                sa.DateTime(),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
        )

    if not inspector.has_table("software_versions"):
        op.create_table(
            "software_versions",
            sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
            sa.Column(
                "pipeline_run_id",
                sa.Integer(),
                sa.ForeignKey("pipeline_runs.id", ondelete="CASCADE"),
                nullable=True,
                comment="Pipeline run this snapshot belongs to (NULL for manual captures)",
            ),
            sa.Column(
                "snapshot_label",
                sa.String(200),
                nullable=False,
                comment="Human label, e.g. 'pipeline_run_42' or 'manual_2025-02-22'",
            ),
            sa.Column("python_version", sa.String(50), nullable=False),
            sa.Column(
                "packages",
                JSONB(),
                nullable=False,
                comment="Dict of package_name -> version for all key dependencies",
            ),
            sa.Column(
                "platform_info",
                JSONB(),
                nullable=True,
                comment="OS, architecture, CPU info",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_software_versions_run",
            "software_versions",
            ["pipeline_run_id"],
        )
        op.create_index(
            "ix_software_versions_created",
            "software_versions",
            ["created_at"],
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)

    if inspector.has_table("software_versions"):
        op.drop_table("software_versions")
    if inspector.has_table("pipeline_runs"):
        op.drop_table("pipeline_runs")
