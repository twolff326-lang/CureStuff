"""Add LLM discovery proposals table.

Strategy 7: LLM Literature Synthesis Discovery. The LLM reads batches
of papers and reasons about implicit drug-cancer connections that no
structured database captures.

Revision ID: 011
"""

import sqlalchemy as sa
from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_discovery_proposals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "drug_id",
            sa.Integer(),
            sa.ForeignKey("drugs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cancer_type_id",
            sa.Integer(),
            sa.ForeignKey("cancer_types.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mechanism_rationale", sa.Text(), nullable=False),
        sa.Column("key_papers", sa.JSON(), server_default="[]"),
        sa.Column("inferred_pathways", sa.JSON(), server_default="[]"),
        sa.Column("transitive_chain", sa.JSON(), server_default="[]"),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("novelty_reasoning", sa.Text()),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="proposed",
        ),
        sa.Column(
            "hypothesis_id",
            sa.Integer(),
            sa.ForeignKey("hypotheses.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("model_used", sa.String(100), nullable=False),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("batch_id", sa.String(50)),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_discovery_proposals_drug_id", "llm_discovery_proposals", ["drug_id"])
    op.create_index("ix_discovery_proposals_cancer_type_id", "llm_discovery_proposals", ["cancer_type_id"])
    op.create_index("ix_discovery_proposals_status", "llm_discovery_proposals", ["status"])
    op.create_index("ix_discovery_proposals_batch_id", "llm_discovery_proposals", ["batch_id"])
    op.create_index(
        "ix_discovery_proposals_confidence_desc",
        "llm_discovery_proposals",
        [sa.text("confidence DESC")],
    )
    op.create_unique_constraint(
        "uq_discovery_proposals_drug_cancer_batch",
        "llm_discovery_proposals",
        ["drug_id", "cancer_type_id", "batch_id"],
    )


def downgrade() -> None:
    op.drop_table("llm_discovery_proposals")
