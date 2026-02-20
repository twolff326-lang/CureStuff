"""Scoring configuration service for hypothesis composite scoring.

Manages configurable scoring weights stored in the scoring_weights table.
Provides 4 built-in presets and allows custom weight configurations.

Presets:
  - balanced:        Equal emphasis across all dimensions (default)
  - novelty_focused: Prioritizes novel, unexplored combinations
  - evidence_heavy:  Emphasizes strong existing evidence
  - clinical_ready:  Focuses on candidates with clinical evidence + safety
"""

import logging
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scoring_config import ScoringWeight

logger = logging.getLogger(__name__)

DIMENSIONS = [
    "pathway_overlap",
    "expression_correlation",
    "literature_support",
    "clinical_evidence",
    "safety",
    "novelty",
    "causal_dependency",
    "gnn_link",
    "mutation_context",
    "polypharmacology",
]

# In-memory fallback if database is not seeded yet.
# gnn_link starts at 0.0 — activates once a GNN model is trained.
# mutation_context and polypharmacology redistribute weight from other dims.
DEFAULT_WEIGHTS = {
    "pathway_overlap": 0.14,
    "expression_correlation": 0.14,
    "literature_support": 0.12,
    "clinical_evidence": 0.10,
    "safety": 0.07,
    "novelty": 0.10,
    "causal_dependency": 0.13,
    "gnn_link": 0.0,
    "mutation_context": 0.12,
    "polypharmacology": 0.08,
}


class ScoringConfig:
    """Manages scoring weight configurations for hypothesis composite scoring.

    The composite score for each hypothesis is:
        composite = sum(weight_i * dimension_score_i for i in 7 dimensions)

    Weights must sum to 1.0 and each must be between 0.0 and 1.0.
    """

    async def get_active_weights(self, db: AsyncSession) -> dict[str, float]:
        """Get the currently active scoring weights.

        Returns the weights marked as is_default=1, or falls back to
        hardcoded defaults if no database entry exists.
        """
        result = await db.execute(
            select(ScoringWeight).where(ScoringWeight.is_default == 1).limit(1)
        )
        active = result.scalar_one_or_none()

        if active and active.weights:
            return active.weights

        return DEFAULT_WEIGHTS.copy()

    async def get_preset(
        self, name: str, db: AsyncSession
    ) -> dict[str, Any] | None:
        """Get a specific scoring preset by name."""
        result = await db.execute(
            select(ScoringWeight).where(ScoringWeight.name == name)
        )
        preset = result.scalar_one_or_none()
        if not preset:
            return None

        return {
            "id": preset.id,
            "name": preset.name,
            "description": preset.description,
            "weights": preset.weights,
            "is_default": preset.is_default == 1,
        }

    async def list_presets(self, db: AsyncSession) -> list[dict[str, Any]]:
        """List all available scoring presets."""
        result = await db.execute(
            select(ScoringWeight).order_by(ScoringWeight.name)
        )
        presets = result.scalars().all()

        return [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "weights": p.weights,
                "is_default": p.is_default == 1,
            }
            for p in presets
        ]

    async def set_active_preset(
        self, name: str, db: AsyncSession
    ) -> dict[str, Any] | None:
        """Set a preset as the active default.

        Clears is_default on all other presets and sets it on the named one.
        """
        # Verify the preset exists
        result = await db.execute(
            select(ScoringWeight).where(ScoringWeight.name == name)
        )
        preset = result.scalar_one_or_none()
        if not preset:
            return None

        # Clear all defaults
        await db.execute(
            update(ScoringWeight).values(is_default=0)
        )

        # Set new default
        preset.is_default = 1
        await db.flush()

        return {
            "id": preset.id,
            "name": preset.name,
            "description": preset.description,
            "weights": preset.weights,
            "is_default": True,
        }

    async def create_preset(
        self,
        name: str,
        weights: dict[str, float],
        description: str | None = None,
        set_default: bool = False,
        db: AsyncSession | None = None,
    ) -> dict[str, Any]:
        """Create a custom scoring preset.

        Validates that weights sum to 1.0 and all 6 dimensions are present.
        """
        self._validate_weights(weights)

        if set_default and db:
            await db.execute(update(ScoringWeight).values(is_default=0))

        preset = ScoringWeight(
            name=name,
            description=description,
            weights=weights,
            is_default=1 if set_default else 0,
        )
        db.add(preset)
        await db.flush()

        return {
            "id": preset.id,
            "name": preset.name,
            "description": preset.description,
            "weights": preset.weights,
            "is_default": set_default,
        }

    async def update_preset(
        self,
        name: str,
        weights: dict[str, float],
        description: str | None = None,
        db: AsyncSession | None = None,
    ) -> dict[str, Any] | None:
        """Update an existing preset's weights."""
        self._validate_weights(weights)

        result = await db.execute(
            select(ScoringWeight).where(ScoringWeight.name == name)
        )
        preset = result.scalar_one_or_none()
        if not preset:
            return None

        preset.weights = weights
        if description is not None:
            preset.description = description
        await db.flush()

        return {
            "id": preset.id,
            "name": preset.name,
            "description": preset.description,
            "weights": preset.weights,
            "is_default": preset.is_default == 1,
        }

    def compute_composite_score(
        self,
        dimension_scores: dict[str, dict[str, Any]],
        weights: dict[str, float],
    ) -> float:
        """Compute weighted composite score from individual dimension scores.

        Args:
            dimension_scores: Dict of {dimension_name: {"score": 0-100, ...}}
            weights: Dict of {dimension_name: weight (0-1)}

        Returns:
            Composite score 0-100
        """
        composite = 0.0
        for dim, weight in weights.items():
            dim_result = dimension_scores.get(dim, {})
            dim_score = dim_result.get("score", 0)
            composite += weight * dim_score

        return round(min(max(composite, 0), 100), 1)

    def determine_evidence_strength(self, composite_score: float) -> str:
        """Map composite score to evidence strength category."""
        if composite_score >= 75:
            return "strong"
        if composite_score >= 50:
            return "moderate"
        if composite_score >= 25:
            return "suggestive"
        return "speculative"

    @staticmethod
    def _validate_weights(weights: dict[str, float]) -> None:
        """Validate scoring weights sum to 1.0 and have all dimensions.

        Accepts legacy weight sets (6 or 7 dimensions).
        Missing dimensions are auto-added with weight 0.
        """
        # Auto-add missing dimensions for legacy presets
        for dim in ("causal_dependency", "gnn_link", "mutation_context", "polypharmacology"):
            if dim not in weights:
                weights[dim] = 0.0

        missing = set(DIMENSIONS) - set(weights.keys())
        if missing:
            raise ValueError(f"Missing weight dimensions: {missing}")

        extra = set(weights.keys()) - set(DIMENSIONS)
        if extra:
            raise ValueError(f"Unknown weight dimensions: {extra}")

        for dim, val in weights.items():
            if not 0.0 <= val <= 1.0:
                raise ValueError(
                    f"Weight for '{dim}' must be between 0.0 and 1.0, got {val}"
                )

        total = sum(weights.values())
        if abs(total - 1.0) > 0.01:
            raise ValueError(
                f"Weights must sum to 1.0, got {total:.3f}"
            )
