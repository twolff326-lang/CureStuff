"""Add scoring_weights table and unique constraint on hypotheses(drug_id, cancer_type_id).

Revision ID: 005
Revises: 004
Create Date: 2026-02-17

Adds:
  - scoring_weights table for configurable hypothesis scoring presets
  - Unique constraint on hypotheses(drug_id, cancer_type_id) to prevent duplicates
  - Default scoring presets: balanced, novelty_focused, evidence_heavy, clinical_ready
"""

import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None

DEFAULT_PRESETS = [
    {
        "name": "balanced",
        "description": "Equal emphasis across all evidence dimensions",
        "weights": {
            "pathway_overlap": 0.20,
            "expression_correlation": 0.20,
            "literature_support": 0.20,
            "clinical_evidence": 0.15,
            "safety": 0.10,
            "novelty": 0.15,
        },
        "is_default": 1,
    },
    {
        "name": "novelty_focused",
        "description": "Prioritizes novel, unexplored drug-cancer combinations",
        "weights": {
            "pathway_overlap": 0.15,
            "expression_correlation": 0.15,
            "literature_support": 0.10,
            "clinical_evidence": 0.10,
            "safety": 0.10,
            "novelty": 0.40,
        },
        "is_default": 0,
    },
    {
        "name": "evidence_heavy",
        "description": "Emphasizes strong existing evidence from literature and expression",
        "weights": {
            "pathway_overlap": 0.20,
            "expression_correlation": 0.25,
            "literature_support": 0.30,
            "clinical_evidence": 0.10,
            "safety": 0.05,
            "novelty": 0.10,
        },
        "is_default": 0,
    },
    {
        "name": "clinical_ready",
        "description": "Focuses on candidates with clinical evidence and safety profiles",
        "weights": {
            "pathway_overlap": 0.10,
            "expression_correlation": 0.10,
            "literature_support": 0.15,
            "clinical_evidence": 0.30,
            "safety": 0.25,
            "novelty": 0.10,
        },
        "is_default": 0,
    },
]


def upgrade() -> None:
    # Create scoring_weights table
    op.create_table(
        "scoring_weights",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("weights", JSONB, nullable=False),
        sa.Column("is_default", sa.Integer, default=0, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime,
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime,
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_scoring_weights_name", "scoring_weights", ["name"], unique=True
    )
    op.create_index(
        "ix_scoring_weights_is_default", "scoring_weights", ["is_default"]
    )

    # Seed default presets
    scoring_weights = sa.table(
        "scoring_weights",
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("weights", JSONB),
        sa.column("is_default", sa.Integer),
    )
    op.bulk_insert(
        scoring_weights,
        [
            {
                "name": p["name"],
                "description": p["description"],
                "weights": json.dumps(p["weights"]),
                "is_default": p["is_default"],
            }
            for p in DEFAULT_PRESETS
        ],
    )

    # Add unique constraint on hypotheses(drug_id, cancer_type_id)
    op.create_unique_constraint(
        "uq_hypotheses_drug_cancer",
        "hypotheses",
        ["drug_id", "cancer_type_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_hypotheses_drug_cancer", "hypotheses")
    op.drop_index("ix_scoring_weights_is_default", table_name="scoring_weights")
    op.drop_index("ix_scoring_weights_name", table_name="scoring_weights")
    op.drop_table("scoring_weights")
