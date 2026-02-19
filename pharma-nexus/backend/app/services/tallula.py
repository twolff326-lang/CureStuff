"""The Tallula Algorithm: Stochastic Resonance Ensemble Discovery.

A novel drug repurposing discovery method that runs K parallel "discovery
lenses" — each a stochastic perturbation of the scoring configuration — and
analyzes the distribution of scores across lenses to surface three classes
of finding:

  1. ROBUST discoveries   — high-scoring across most lenses (well-supported,
                            but likely already known)
  2. RESONANT discoveries — score highly only under specific lens configurations
                            (the NOVEL, non-obvious findings that any single
                            fixed-weight pass would miss)
  3. FRAGILE discoveries  — appear strong but depend critically on one dimension
                            (flag for scrutiny)

Why this works:
  Truly novel repurposing candidates SHOULD have low literature scores (nobody's
  written about them yet) but may have strong mechanistic signals that only
  emerge when you weight evidence dimensions differently. A single deterministic
  scoring pass averages these signals away. Tallula finds the "hidden modes" in
  the evidence landscape.

Inspiration:
  - Stochastic resonance (physics): noise helps detect sub-threshold signals
  - Dropout regularization (deep learning): random masking forces robust features
  - Bootstrap aggregation: resampled models reveal consensus AND outliers
  - Multi-objective optimization: Pareto frontiers over competing objectives

Algorithm phases:
  Phase 1 — Lens Generation:   Sample K random scoring configurations
  Phase 2 — Ensemble Scoring:  Score all hypotheses under every lens
  Phase 3 — Distribution Analysis: Compute ubiquity, resonance, fragility
  Phase 4 — Discovery Classification: Robust / Resonant / Fragile
  Phase 5 — Resonance Decomposition: Explain WHY a resonant discovery emerges
"""

import logging
import math
from typing import Any

import numpy as np
from scipy import stats as scipy_stats

from app.services.scoring_config import DIMENSIONS, ScoringConfig

logger = logging.getLogger(__name__)

# Number of evidence dimensions
N_DIMS = len(DIMENSIONS)


# ===================================================================
# Phase 1 — Stochastic Lens Generation
# ===================================================================

def generate_lenses(
    n_lenses: int = 200,
    dropout_rate: float = 0.3,
    dirichlet_alpha: float = 2.0,
    seed: int | None = 42,
) -> list[dict[str, Any]]:
    """Generate K stochastic discovery lenses.

    Each lens is a random scoring configuration consisting of:
      - A weight vector sampled from a Dirichlet distribution
      - A dimension mask (dropout) that zeroes random dimensions
      - The active weights renormalized to sum to 1.0

    Args:
        n_lenses: Number of parallel lenses to generate (K).
        dropout_rate: Probability of masking each dimension (0-1).
            Higher values create more extreme lens diversity.
        dirichlet_alpha: Concentration parameter for the Dirichlet.
            Lower values (< 1) create sparser, more extreme weightings.
            Higher values (> 5) cluster near uniform weighting.
            Default of 2.0 balances diversity with reasonableness.
        seed: Random seed for reproducibility.

    Returns:
        List of K lens dicts, each with:
          - weights: dict[str, float] (renormalized after masking)
          - mask: dict[str, bool] (True = active)
          - n_active: int (number of active dimensions)
          - lens_id: int
    """
    rng = np.random.default_rng(seed)
    lenses = []

    for i in range(n_lenses):
        # Sample raw weights from Dirichlet
        alpha_vec = np.full(N_DIMS, dirichlet_alpha)
        raw_weights = rng.dirichlet(alpha_vec)

        # Apply dropout mask — each dimension has `dropout_rate` chance of
        # being zeroed out. Ensure at least 2 dimensions survive.
        mask = rng.random(N_DIMS) > dropout_rate
        if mask.sum() < 2:
            # Force at least 2 random dimensions on
            off_indices = np.where(~mask)[0]
            force_on = rng.choice(off_indices, size=2, replace=False)
            mask[force_on] = True

        # Apply mask and renormalize
        masked_weights = raw_weights * mask.astype(float)
        total = masked_weights.sum()
        if total > 0:
            masked_weights /= total

        # Build lens dict
        weights = {}
        mask_dict = {}
        for j, dim in enumerate(DIMENSIONS):
            weights[dim] = float(masked_weights[j])
            mask_dict[dim] = bool(mask[j])

        lenses.append({
            "lens_id": i,
            "weights": weights,
            "mask": mask_dict,
            "n_active": int(mask.sum()),
        })

    return lenses


# ===================================================================
# Phase 2 — Ensemble Scoring
# ===================================================================

def score_hypothesis_under_lenses(
    dimension_scores: dict[str, float],
    lenses: list[dict[str, Any]],
) -> np.ndarray:
    """Score a single hypothesis under all K lenses.

    Args:
        dimension_scores: {dimension_name: score (0-100)} for this hypothesis.
        lenses: List of lens dicts from generate_lenses().

    Returns:
        1-D numpy array of length K with the composite score under each lens.
    """
    scores = np.empty(len(lenses))
    for i, lens in enumerate(lenses):
        composite = 0.0
        for dim in DIMENSIONS:
            composite += lens["weights"].get(dim, 0.0) * dimension_scores.get(dim, 0.0)
        scores[i] = min(max(composite, 0.0), 100.0)
    return scores


def ensemble_score_all(
    hypotheses: list[dict[str, Any]],
    lenses: list[dict[str, Any]],
) -> dict[int, np.ndarray]:
    """Score every hypothesis under every lens.

    Args:
        hypotheses: List of dicts with at minimum:
            - hypothesis_id: int
            - dimension_scores: dict[str, float]
        lenses: List of lens dicts.

    Returns:
        {hypothesis_id: np.ndarray of K scores}
    """
    results = {}
    for hyp in hypotheses:
        hid = hyp["hypothesis_id"]
        dim_scores = hyp["dimension_scores"]
        results[hid] = score_hypothesis_under_lenses(dim_scores, lenses)
    return results


# ===================================================================
# Phase 3 — Distribution Analysis
# ===================================================================

def compute_ubiquity(
    lens_scores: np.ndarray,
    threshold_percentile: float = 80.0,
    global_scores: np.ndarray | None = None,
) -> float:
    """Ubiquity: fraction of lenses where this hypothesis scores well.

    A hypothesis with high ubiquity is robust — it ranks well regardless
    of how you weight the evidence.

    Args:
        lens_scores: Array of K composite scores for one hypothesis.
        threshold_percentile: Score percentile to consider "high-scoring".
        global_scores: If provided, the threshold is computed from this
            array (all scores across all hypotheses and lenses).

    Returns:
        Float 0-1. 1.0 = scores above threshold in every single lens.
    """
    if global_scores is not None:
        threshold = float(np.percentile(global_scores, threshold_percentile))
    else:
        threshold = float(np.percentile(lens_scores, threshold_percentile))
    return float(np.mean(lens_scores >= threshold))


def compute_resonance(lens_scores: np.ndarray) -> float:
    """Resonance: how peaked is the score distribution across lenses?

    High resonance means the hypothesis scores dramatically higher under
    some lens configurations than others — it has a "hidden mode" that
    only specific evidence weightings reveal.

    Computed as max / median (avoiding division by zero).

    Returns:
        Float >= 1.0. Values > 3.0 indicate strong resonance.
    """
    median = float(np.median(lens_scores))
    maximum = float(np.max(lens_scores))
    if median < 1.0:
        # Avoid instability near zero; use a floor
        return maximum / max(median, 1.0)
    return maximum / median


def compute_fragility(
    dimension_scores: dict[str, float],
    baseline_composite: float,
) -> dict[str, Any]:
    """Fragility: how much does the score depend on any single dimension?

    For each dimension, compute the composite score with that dimension
    zeroed out (and remaining weights renormalized). The fragility index
    is the maximum fractional drop across all leave-one-out ablations.

    Returns:
        Dict with:
          - fragility_index: float 0-1 (max fractional score drop)
          - critical_dimension: str (the dimension whose removal hurts most)
          - ablation_impacts: dict[str, float] (per-dimension fractional drops)
    """
    if baseline_composite <= 0:
        return {
            "fragility_index": 0.0,
            "critical_dimension": None,
            "ablation_impacts": {},
        }

    config = ScoringConfig()
    # Use uniform weights as the "fair" baseline for ablation
    uniform_weight = 1.0 / N_DIMS

    ablation_impacts = {}
    for dim in DIMENSIONS:
        # Remove this dimension, redistribute weight uniformly
        ablated_weights = {}
        for d in DIMENSIONS:
            if d == dim:
                ablated_weights[d] = 0.0
            else:
                ablated_weights[d] = 1.0 / (N_DIMS - 1)

        ablated_dim_scores = {
            d: {"score": dimension_scores.get(d, 0.0)} for d in DIMENSIONS
        }
        ablated_dim_scores[dim] = {"score": 0.0}

        ablated_composite = config.compute_composite_score(
            ablated_dim_scores, ablated_weights
        )
        fractional_drop = (baseline_composite - ablated_composite) / baseline_composite
        ablation_impacts[dim] = round(max(fractional_drop, 0.0), 4)

    max_drop = max(ablation_impacts.values()) if ablation_impacts else 0.0
    critical_dim = max(ablation_impacts, key=ablation_impacts.get) if ablation_impacts else None

    return {
        "fragility_index": round(max_drop, 4),
        "critical_dimension": critical_dim,
        "ablation_impacts": ablation_impacts,
    }


def compute_score_distribution_stats(lens_scores: np.ndarray) -> dict[str, Any]:
    """Compute descriptive statistics of a hypothesis's score distribution.

    These stats characterize the "shape" of how a hypothesis behaves
    across the ensemble of lenses.
    """
    if len(lens_scores) == 0:
        return {}

    return {
        "mean": round(float(np.mean(lens_scores)), 2),
        "median": round(float(np.median(lens_scores)), 2),
        "std": round(float(np.std(lens_scores, ddof=1)), 2) if len(lens_scores) > 1 else 0.0,
        "min": round(float(np.min(lens_scores)), 2),
        "max": round(float(np.max(lens_scores)), 2),
        "iqr": round(float(np.percentile(lens_scores, 75) - np.percentile(lens_scores, 25)), 2),
        "skewness": round(float(scipy_stats.skew(lens_scores)), 3) if len(lens_scores) > 2 and np.std(lens_scores) > 1e-10 else 0.0,
        "kurtosis": round(float(scipy_stats.kurtosis(lens_scores)), 3) if len(lens_scores) > 3 and np.std(lens_scores) > 1e-10 else 0.0,
        "p90": round(float(np.percentile(lens_scores, 90)), 2),
        "p99": round(float(np.percentile(lens_scores, 99)), 2),
    }


# ===================================================================
# Phase 4 — Discovery Classification
# ===================================================================

DISCOVERY_CLASSES = {
    "robust": "Consistently high-scoring across diverse evidence weightings. "
              "Well-supported but likely already known or expected.",
    "resonant": "Scores highly only under specific evidence configurations. "
                "Represents a potentially NOVEL finding that deterministic "
                "scoring would miss — the key output of the Tallula Algorithm.",
    "fragile": "Appears strong but depends critically on a single evidence "
               "dimension. Requires scrutiny of that dimension's data quality.",
    "moderate": "Shows moderate signal across lenses without strong resonance "
                "or fragility. May warrant further investigation.",
    "weak": "Does not score well under any lens configuration. "
            "Unlikely to represent a real repurposing opportunity.",
}


def classify_discovery(
    ubiquity: float,
    resonance: float,
    fragility_index: float,
    mean_score: float,
    ubiquity_robust_threshold: float = 0.5,
    resonance_threshold: float = 2.5,
    fragility_threshold: float = 0.45,
    min_mean_score: float = 20.0,
) -> str:
    """Classify a hypothesis into a discovery class.

    Decision logic:
      1. If mean score < min_mean_score → "weak" (not enough signal anywhere)
      2. If fragility > threshold → "fragile" (over-reliance on one dimension)
      3. If ubiquity > threshold → "robust" (consistent across lenses)
      4. If resonance > threshold AND ubiquity < 0.3 → "resonant" (the gold!)
      5. Otherwise → "moderate"

    Args:
        ubiquity: Fraction of lenses where hypothesis is top-scoring (0-1).
        resonance: max/median score ratio across lenses (>= 1.0).
        fragility_index: Max fractional score drop from removing one dimension.
        mean_score: Mean composite score across all lenses.

    Returns:
        One of: "robust", "resonant", "fragile", "moderate", "weak"
    """
    if mean_score < min_mean_score:
        return "weak"
    if fragility_index > fragility_threshold:
        return "fragile"
    if ubiquity > ubiquity_robust_threshold:
        return "robust"
    if resonance > resonance_threshold and ubiquity < 0.3:
        return "resonant"
    return "moderate"


# ===================================================================
# Phase 5 — Resonance Decomposition
# ===================================================================

def decompose_resonance(
    dimension_scores: dict[str, float],
    lenses: list[dict[str, Any]],
    lens_scores: np.ndarray,
    top_fraction: float = 0.1,
) -> dict[str, Any]:
    """Identify which evidence dimensions drive a resonant discovery.

    Compares the lens configurations that produce the TOP scores to those
    that produce BOTTOM scores. The dimensions whose weights differ most
    between these two groups are the "activation dimensions" — the specific
    evidence types that, when amplified, make this hypothesis shine.

    This is the explanatory core of Tallula: it doesn't just find novel
    candidates, it tells you WHY they're novel and what kind of evidence
    supports them.

    Args:
        dimension_scores: The hypothesis's per-dimension scores.
        lenses: Full list of lens dicts.
        lens_scores: Array of composite scores under each lens.
        top_fraction: Fraction of lenses to consider as "top" vs "bottom".

    Returns:
        Dict with:
          - activation_dimensions: ranked list of dims that drive resonance
          - top_lens_profile: mean weight profile of high-scoring lenses
          - bottom_lens_profile: mean weight profile of low-scoring lenses
          - narrative: human-readable explanation
    """
    n = len(lens_scores)
    top_k = max(int(n * top_fraction), 1)
    bottom_k = max(int(n * top_fraction), 1)

    sorted_indices = np.argsort(lens_scores)
    top_indices = sorted_indices[-top_k:]
    bottom_indices = sorted_indices[:bottom_k]

    # Compute mean weight profiles for top and bottom lenses
    top_profile = {dim: 0.0 for dim in DIMENSIONS}
    bottom_profile = {dim: 0.0 for dim in DIMENSIONS}

    for idx in top_indices:
        for dim in DIMENSIONS:
            top_profile[dim] += lenses[idx]["weights"].get(dim, 0.0)
    for dim in DIMENSIONS:
        top_profile[dim] /= top_k

    for idx in bottom_indices:
        for dim in DIMENSIONS:
            bottom_profile[dim] += lenses[idx]["weights"].get(dim, 0.0)
    for dim in DIMENSIONS:
        bottom_profile[dim] /= bottom_k

    # Activation = weight difference * dimension score
    # (a dimension only "activates" if it has weight AND score)
    activation = {}
    for dim in DIMENSIONS:
        weight_diff = top_profile[dim] - bottom_profile[dim]
        dim_score = dimension_scores.get(dim, 0.0)
        # Activation strength: how much does amplifying this dimension
        # contribute to the score spike?
        activation[dim] = round(weight_diff * dim_score / 100.0, 4)

    # Rank by activation strength (descending)
    ranked = sorted(activation.items(), key=lambda x: x[1], reverse=True)

    # Build narrative
    narrative = _build_resonance_narrative(
        ranked, dimension_scores, top_profile, bottom_profile
    )

    return {
        "activation_dimensions": [
            {"dimension": dim, "activation_strength": strength}
            for dim, strength in ranked
        ],
        "top_lens_profile": {dim: round(v, 4) for dim, v in top_profile.items()},
        "bottom_lens_profile": {dim: round(v, 4) for dim, v in bottom_profile.items()},
        "narrative": narrative,
    }


def _build_resonance_narrative(
    ranked_activations: list[tuple[str, float]],
    dimension_scores: dict[str, float],
    top_profile: dict[str, float],
    bottom_profile: dict[str, float],
) -> str:
    """Generate a human-readable explanation of why a discovery is resonant."""
    dim_labels = {
        "pathway_overlap": "pathway overlap",
        "expression_correlation": "expression correlation",
        "literature_support": "literature support",
        "clinical_evidence": "clinical evidence",
        "safety": "safety profile",
        "novelty": "novelty",
        "causal_dependency": "causal dependency (DepMap)",
    }

    # Top activating dimensions (positive activation)
    activators = [
        (dim, strength) for dim, strength in ranked_activations
        if strength > 0.01
    ]
    # Suppressing dimensions (negative activation — high weight hurts)
    suppressors = [
        (dim, abs(strength)) for dim, strength in ranked_activations
        if strength < -0.01
    ]

    parts = []
    if activators:
        top_dim, top_strength = activators[0]
        label = dim_labels.get(top_dim, top_dim)
        score = dimension_scores.get(top_dim, 0)
        parts.append(
            f"This candidate emerges when {label} evidence is amplified "
            f"(dimension score: {score:.0f}/100)."
        )
        if len(activators) > 1:
            secondary = [dim_labels.get(d, d) for d, _ in activators[1:3]]
            parts.append(
                f"Secondary activating dimensions: {', '.join(secondary)}."
            )

    if suppressors:
        sup_dim, _ = suppressors[0]
        label = dim_labels.get(sup_dim, sup_dim)
        score = dimension_scores.get(sup_dim, 0)
        parts.append(
            f"The signal is masked when {label} is weighted heavily "
            f"(dimension score: {score:.0f}/100), which explains why "
            f"deterministic scoring misses it."
        )

    if not parts:
        parts.append("No clear activation pattern detected.")

    return " ".join(parts)


# ===================================================================
# Full Pipeline — The Tallula Engine
# ===================================================================

class TallulaEngine:
    """Orchestrates the full Tallula Algorithm pipeline.

    Usage:
        engine = TallulaEngine()
        results = engine.run(hypotheses, n_lenses=200)
    """

    def __init__(self):
        self.config = ScoringConfig()

    def run(
        self,
        hypotheses: list[dict[str, Any]],
        n_lenses: int = 200,
        dropout_rate: float = 0.3,
        dirichlet_alpha: float = 2.0,
        seed: int | None = 42,
        top_n: int | None = None,
    ) -> dict[str, Any]:
        """Execute the full Tallula Algorithm.

        Args:
            hypotheses: List of dicts with:
                - hypothesis_id: int
                - drug_id: int
                - cancer_type_id: int
                - title: str
                - composite_score: float (deterministic baseline)
                - dimension_scores: dict[str, float] (7 dimensions, each 0-100)
            n_lenses: Number of stochastic lenses (K). More = more
                thorough but slower. 200 is a good default.
            dropout_rate: Per-dimension dropout probability (0-1).
            dirichlet_alpha: Dirichlet concentration. Lower = wilder
                weight distributions. 2.0 is balanced.
            seed: Random seed for reproducibility.
            top_n: If set, only return the top N discoveries of each class.

        Returns:
            Full Tallula results dict (see below).
        """
        if not hypotheses:
            return {"error": "No hypotheses provided", "discoveries": []}

        logger.info(
            "Tallula Algorithm: scoring %d hypotheses across %d lenses "
            "(dropout=%.2f, alpha=%.1f, seed=%s)",
            len(hypotheses), n_lenses, dropout_rate, dirichlet_alpha, seed,
        )

        # Phase 1: Generate lenses
        lenses = generate_lenses(
            n_lenses=n_lenses,
            dropout_rate=dropout_rate,
            dirichlet_alpha=dirichlet_alpha,
            seed=seed,
        )

        # Phase 2: Ensemble scoring
        ensemble = ensemble_score_all(hypotheses, lenses)

        # Compute global score threshold from ALL scores across ALL hypotheses
        all_scores = np.concatenate(list(ensemble.values()))
        global_threshold = float(np.percentile(all_scores, 80))

        # Phase 3 + 4 + 5: Analyze each hypothesis
        discoveries = []
        for hyp in hypotheses:
            hid = hyp["hypothesis_id"]
            lens_scores = ensemble[hid]
            dim_scores = hyp["dimension_scores"]
            baseline = hyp.get("composite_score", float(np.mean(lens_scores)))

            # Phase 3: Distribution analysis
            ubiquity = compute_ubiquity(
                lens_scores, global_scores=all_scores
            )
            resonance = compute_resonance(lens_scores)
            fragility = compute_fragility(dim_scores, baseline)
            stats = compute_score_distribution_stats(lens_scores)

            # Phase 4: Classification
            discovery_class = classify_discovery(
                ubiquity=ubiquity,
                resonance=resonance,
                fragility_index=fragility["fragility_index"],
                mean_score=stats.get("mean", 0.0),
            )

            # Phase 5: Resonance decomposition (for non-weak discoveries)
            resonance_profile = None
            if discovery_class != "weak":
                resonance_profile = decompose_resonance(
                    dim_scores, lenses, lens_scores
                )

            discoveries.append({
                "hypothesis_id": hid,
                "drug_id": hyp.get("drug_id"),
                "cancer_type_id": hyp.get("cancer_type_id"),
                "title": hyp.get("title", ""),
                "deterministic_score": baseline,
                "discovery_class": discovery_class,
                "tallula_metrics": {
                    "ubiquity": round(ubiquity, 4),
                    "resonance": round(resonance, 4),
                    "fragility_index": fragility["fragility_index"],
                    "critical_dimension": fragility["critical_dimension"],
                },
                "score_distribution": stats,
                "resonance_profile": resonance_profile,
                "ablation_impacts": fragility["ablation_impacts"],
            })

        # Sort: resonant first (the gold), then robust, moderate, fragile, weak
        class_order = {"resonant": 0, "robust": 1, "moderate": 2, "fragile": 3, "weak": 4}
        discoveries.sort(
            key=lambda d: (
                class_order.get(d["discovery_class"], 5),
                -d["score_distribution"].get("max", 0),
            )
        )

        # Summary statistics
        class_counts = {}
        for d in discoveries:
            cls = d["discovery_class"]
            class_counts[cls] = class_counts.get(cls, 0) + 1

        # Apply top_n filter per class
        if top_n:
            filtered = []
            per_class_count = {}
            for d in discoveries:
                cls = d["discovery_class"]
                per_class_count.setdefault(cls, 0)
                if per_class_count[cls] < top_n:
                    filtered.append(d)
                    per_class_count[cls] += 1
            discoveries = filtered

        result = {
            "algorithm": "tallula",
            "version": "1.0",
            "parameters": {
                "n_lenses": n_lenses,
                "dropout_rate": dropout_rate,
                "dirichlet_alpha": dirichlet_alpha,
                "seed": seed,
                "n_hypotheses_input": len(hypotheses),
            },
            "summary": {
                "class_counts": class_counts,
                "n_resonant": class_counts.get("resonant", 0),
                "n_robust": class_counts.get("robust", 0),
                "n_fragile": class_counts.get("fragile", 0),
                "global_score_threshold_p80": round(global_threshold, 2),
            },
            "discoveries": discoveries,
        }

        logger.info(
            "Tallula complete: %d resonant, %d robust, %d fragile, %d moderate, %d weak",
            class_counts.get("resonant", 0),
            class_counts.get("robust", 0),
            class_counts.get("fragile", 0),
            class_counts.get("moderate", 0),
            class_counts.get("weak", 0),
        )

        return result

    async def run_from_db(
        self,
        db,
        n_lenses: int = 200,
        cancer_type_id: int | None = None,
        min_deterministic_score: float = 5.0,
        **kwargs,
    ) -> dict[str, Any]:
        """Run Tallula against hypotheses stored in the database.

        Loads existing hypotheses (optionally filtered by cancer type),
        extracts their dimension scores, and runs the full algorithm.

        Args:
            db: AsyncSession
            n_lenses: Number of stochastic lenses.
            cancer_type_id: Optional filter — only analyze hypotheses
                for this cancer type.
            min_deterministic_score: Minimum baseline composite score
                to include in the analysis (filters noise).
            **kwargs: Passed through to self.run().

        Returns:
            Full Tallula results dict.
        """
        from sqlalchemy import select
        from app.models.hypothesis import Hypothesis

        query = select(Hypothesis).where(
            Hypothesis.composite_score >= min_deterministic_score
        )
        if cancer_type_id is not None:
            query = query.where(Hypothesis.cancer_type_id == cancer_type_id)
        query = query.order_by(Hypothesis.composite_score.desc())

        result = await db.execute(query)
        all_hyps = result.scalars().all()

        if not all_hyps:
            return {"error": "No hypotheses found matching criteria", "discoveries": []}

        hypotheses = []
        for h in all_hyps:
            hypotheses.append({
                "hypothesis_id": h.id,
                "drug_id": h.drug_id,
                "cancer_type_id": h.cancer_type_id,
                "title": h.title,
                "composite_score": h.composite_score,
                "dimension_scores": {
                    "pathway_overlap": h.pathway_overlap_score or 0.0,
                    "expression_correlation": h.expression_correlation_score or 0.0,
                    "literature_support": h.literature_support_score or 0.0,
                    "clinical_evidence": h.clinical_evidence_score or 0.0,
                    "safety": h.safety_score or 0.0,
                    "novelty": h.novelty_score or 0.0,
                    "causal_dependency": h.causal_dependency_score or 0.0,
                },
            })

        return self.run(hypotheses, n_lenses=n_lenses, **kwargs)
