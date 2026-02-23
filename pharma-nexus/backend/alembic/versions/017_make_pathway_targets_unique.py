"""Make pathway_targets (pathway_id, target_id) index unique.

The KEGG and Reactome connectors use batch_upsert_composite with
conflict_columns=["pathway_id", "target_id"], which requires a UNIQUE
index for PostgreSQL's ON CONFLICT clause.  The existing index
ix_pathway_targets_pathway_target was non-unique, so every PathwayTarget
upsert failed with "there is no unique or exclusion constraint matching
the ON CONFLICT specification".

Revision ID: 017
Revises: 016
Create Date: 2026-02-23
"""

from alembic import op
from sqlalchemy import inspect

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)

    # Drop the old non-unique index, then recreate as unique
    existing = [idx["name"] for idx in inspector.get_indexes("pathway_targets")]
    if "ix_pathway_targets_pathway_target" in existing:
        op.drop_index("ix_pathway_targets_pathway_target", "pathway_targets")

    op.create_index(
        "ix_pathway_targets_pathway_target",
        "pathway_targets",
        ["pathway_id", "target_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_pathway_targets_pathway_target", "pathway_targets")
    op.create_index(
        "ix_pathway_targets_pathway_target",
        "pathway_targets",
        ["pathway_id", "target_id"],
        unique=False,
    )
