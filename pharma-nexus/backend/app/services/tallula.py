"""The Tallula Algorithm v2.0: Stochastic Resonance Ensemble Discovery.

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

v2.0 additions:
  Phase 2b — Non-linear interaction scoring (dimension synergies)
  Phase 1b — Adaptive lens generation (importance sampling in weight space)
  Phase 6  — Cross-cancer transfer (resonant patterns generalize)
"""

import logging
import math
from typing import Any

import math

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
# Phase 2b — Non-Linear Interaction Scoring (Tallula 2.0)
# ===================================================================

# Biologically-motivated dimension interaction pairs.
# These capture synergies: when BOTH dimensions are strong, the combined
# signal is greater than the sum of parts.
INTERACTION_PAIRS = [
    # Pathway overlap + expression correlation = drug hits a pathway that's
    # actually dysregulated at expression level
    ("pathway_overlap", "expression_correlation", 1.5),
    # Causal dependency + expression = essential gene is also overexpressed
    ("causal_dependency", "expression_correlation", 1.3),
    # Pathway overlap + causal dependency = pathway is both shared and essential
    ("pathway_overlap", "causal_dependency", 1.2),
    # Clinical evidence + safety = translatable candidate
    ("clinical_evidence", "safety", 1.4),
    # Novelty + causal dependency = novel but mechanistically grounded
    ("novelty", "causal_dependency", 1.3),
    # Literature + clinical = converging evidence streams
    ("literature_support", "clinical_evidence", 1.2),
]


def score_with_interactions(
    dimension_scores: dict[str, float],
    lens: dict[str, Any],
    interaction_strength: float = 0.15,
) -> float:
    """Score a hypothesis with non-linear dimension interactions.

    Replaces the simple weighted sum with:
      score = (1 - interaction_strength) * linear_score
            + interaction_strength * interaction_bonus

    The interaction bonus rewards hypotheses where synergistic dimension
    pairs are BOTH strong simultaneously. This captures the biological
    insight that, e.g., a drug targeting a pathway that's both shared
    with the cancer AND dysregulated at expression level is much more
    promising than either signal alone.

    Args:
        dimension_scores: {dim_name: score 0-100}
        lens: Lens dict with weights and mask.
        interaction_strength: Fraction of total score from interactions (0-1).

    Returns:
        Non-linear composite score (0-100).
    """
    # Linear component (same as v1)
    linear = 0.0
    for dim in DIMENSIONS:
        linear += lens["weights"].get(dim, 0.0) * dimension_scores.get(dim, 0.0)

    # Interaction component: geometric mean of synergistic pairs
    interaction_bonus = 0.0
    n_active_interactions = 0

    for dim_a, dim_b, multiplier in INTERACTION_PAIRS:
        # Only count interactions where both dims are active in this lens
        if not lens["mask"].get(dim_a, False) or not lens["mask"].get(dim_b, False):
            continue

        score_a = dimension_scores.get(dim_a, 0.0) / 100.0
        score_b = dimension_scores.get(dim_b, 0.0) / 100.0

        # Geometric mean captures "both must be strong" synergy
        synergy = math.sqrt(score_a * score_b) * multiplier * 100.0

        # Weight by the lens's emphasis on these dimensions
        weight_a = lens["weights"].get(dim_a, 0.0)
        weight_b = lens["weights"].get(dim_b, 0.0)
        pair_weight = (weight_a + weight_b) / 2.0

        interaction_bonus += synergy * pair_weight
        n_active_interactions += 1

    if n_active_interactions > 0:
        interaction_bonus /= n_active_interactions

    # Combine linear and interaction components
    combined = (1.0 - interaction_strength) * linear + interaction_strength * interaction_bonus
    return min(max(combined, 0.0), 100.0)


def score_hypothesis_under_lenses_v2(
    dimension_scores: dict[str, float],
    lenses: list[dict[str, Any]],
    use_interactions: bool = True,
    interaction_strength: float = 0.15,
) -> np.ndarray:
    """Score a hypothesis under all K lenses with optional non-linear interactions.

    v2 upgrade: when use_interactions=True, uses score_with_interactions()
    instead of simple weighted sum.
    """
    scores = np.empty(len(lenses))
    for i, lens in enumerate(lenses):
        if use_interactions:
            scores[i] = score_with_interactions(
                dimension_scores, lens, interaction_strength
            )
        else:
            composite = 0.0
            for dim in DIMENSIONS:
                composite += lens["weights"].get(dim, 0.0) * dimension_scores.get(dim, 0.0)
            scores[i] = min(max(composite, 0.0), 100.0)
    return scores


# ===================================================================
# Phase 1b — Adaptive Lens Generation (Tallula 2.0)
# ===================================================================

def generate_adaptive_lenses(
    hypotheses: list[dict[str, Any]],
    initial_lenses: list[dict[str, Any]],
    n_adaptive: int = 100,
    top_fraction: float = 0.2,
    seed: int | None = 43,
) -> list[dict[str, Any]]:
    """Generate a second round of lenses focused on interesting weight regions.

    After the initial lens pass, identify which regions of the weight space
    produced the highest-variance (most interesting) score distributions.
    Oversample those regions in a second pass — analogous to importance
    sampling in Monte Carlo methods.

    This finds MORE resonant discoveries by concentrating lens diversity
    where it matters most.

    Args:
        hypotheses: List of hypothesis dicts with dimension_scores.
        initial_lenses: First-pass lenses.
        n_adaptive: Number of adaptive lenses to generate.
        top_fraction: Fraction of initial lenses to use as seeds.
        seed: Random seed.

    Returns:
        List of adaptive lens dicts (to be appended to initial lenses).
    """
    rng = np.random.default_rng(seed)

    # Score all hypotheses under initial lenses to find interesting regions
    all_variances = np.zeros(len(initial_lenses))
    for hyp in hypotheses:
        scores = score_hypothesis_under_lenses(hyp["dimension_scores"], initial_lenses)
        # Compute per-lens contribution to cross-hypothesis variance
        for i in range(len(initial_lenses)):
            all_variances[i] += scores[i]

    # Find lenses that produced the most diverse scores across hypotheses
    # These are the "interesting" weight configurations
    top_k = max(int(len(initial_lenses) * top_fraction), 5)
    top_lens_indices = np.argsort(all_variances)[-top_k:]

    # Generate new lenses by perturbing the top-performing ones
    adaptive_lenses = []
    for i in range(n_adaptive):
        # Pick a random top lens as seed
        seed_idx = rng.choice(top_lens_indices)
        seed_lens = initial_lenses[seed_idx]

        # Perturb weights with Gaussian noise
        new_weights = {}
        for dim in DIMENSIONS:
            base_w = seed_lens["weights"].get(dim, 0.0)
            noise = rng.normal(0, 0.05)
            new_weights[dim] = max(base_w + noise, 0.0)

        # Renormalize
        total = sum(new_weights.values())
        if total > 0:
            new_weights = {d: w / total for d, w in new_weights.items()}

        # Keep the same mask as the seed (preserves dimensionality focus)
        adaptive_lenses.append({
            "lens_id": len(initial_lenses) + i,
            "weights": new_weights,
            "mask": seed_lens["mask"].copy(),
            "n_active": seed_lens["n_active"],
            "adaptive": True,
            "seed_lens_id": int(seed_idx),
        })

    return adaptive_lenses


# ===================================================================
# Phase 6 — Cross-Cancer Transfer (Tallula 2.0)
# ===================================================================

def find_cross_cancer_candidates(
    discoveries: list[dict[str, Any]],
    hypotheses: list[dict[str, Any]],
    min_resonance: float = 2.5,
) -> list[dict[str, Any]]:
    """Find cross-cancer transfer candidates from resonant discoveries.

    If a drug is resonant for cancer A with activation dimensions
    [pathway_overlap, causal_dependency], check if any hypotheses
    for cancer B share similar activation patterns.

    This propagates resonant discoveries across cancer types with
    similar pathway profiles.

    Returns:
        List of transfer candidate dicts with source and target info.
    """
    # Get resonant discoveries
    resonant = [d for d in discoveries if d["discovery_class"] == "resonant" and d.get("resonance_profile")]

    if not resonant:
        return []

    # Group hypotheses by drug
    drug_hypotheses: dict[int, list[dict]] = {}
    for h in hypotheses:
        did = h.get("drug_id")
        if did:
            drug_hypotheses.setdefault(did, []).append(h)

    transfers = []
    for disc in resonant:
        drug_id = disc.get("drug_id")
        source_cancer = disc.get("cancer_type_id")
        profile = disc.get("resonance_profile", {})
        activation_dims = profile.get("activation_dimensions", [])

        if not drug_id or not activation_dims:
            continue

        # Get the top 2 activation dimensions
        top_activations = [
            ad["dimension"] for ad in activation_dims[:2]
            if ad.get("activation_strength", 0) > 0.01
        ]

        if not top_activations:
            continue

        # Check other cancer types for this same drug
        other_hyps = [
            h for h in drug_hypotheses.get(drug_id, [])
            if h.get("cancer_type_id") != source_cancer
        ]

        for other_hyp in other_hyps:
            # Check if the activation dimensions are also strong in the other cancer
            dim_scores = other_hyp.get("dimension_scores", {})
            activation_strength = sum(
                dim_scores.get(dim, 0.0) for dim in top_activations
            ) / (len(top_activations) * 100.0)

            if activation_strength > 0.3:  # At least 30% of max possible
                transfers.append({
                    "source_hypothesis_id": disc["hypothesis_id"],
                    "source_cancer_type_id": source_cancer,
                    "target_hypothesis_id": other_hyp.get("hypothesis_id"),
                    "target_cancer_type_id": other_hyp.get("cancer_type_id"),
                    "drug_id": drug_id,
                    "shared_activation_dimensions": top_activations,
                    "activation_strength_in_target": round(activation_strength, 3),
                    "source_resonance": disc.get("tallula_metrics", {}).get("resonance", 0),
                })

    # Sort by activation strength in target
    transfers.sort(key=lambda t: t["activation_strength_in_target"], reverse=True)
    return transfers


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
        use_interactions: bool = True,
        interaction_strength: float = 0.15,
        adaptive_lenses: bool = True,
        n_adaptive: int = 100,
        cross_cancer_transfer: bool = True,
    ) -> dict[str, Any]:
        """Execute the Tallula Algorithm v2.0.

        v2.0 additions over v1:
          - Non-linear dimension interaction scoring (use_interactions)
          - Adaptive lens generation via importance sampling (adaptive_lenses)
          - Cross-cancer transfer discovery (cross_cancer_transfer)

        Args:
            hypotheses: List of dicts with hypothesis_id, drug_id,
                cancer_type_id, title, composite_score, dimension_scores.
            n_lenses: Number of initial stochastic lenses (K).
            dropout_rate: Per-dimension dropout probability (0-1).
            dirichlet_alpha: Dirichlet concentration parameter.
            seed: Random seed for reproducibility.
            top_n: If set, only return top N per discovery class.
            use_interactions: v2.0: enable non-linear dimension interactions.
            interaction_strength: v2.0: fraction of score from interactions (0-1).
            adaptive_lenses: v2.0: enable adaptive lens generation.
            n_adaptive: v2.0: number of adaptive lenses to generate.
            cross_cancer_transfer: v2.0: discover cross-cancer transfer candidates.

        Returns:
            Full Tallula results dict.
        """
        if not hypotheses:
            return {"error": "No hypotheses provided", "discoveries": []}

        logger.info(
            "Tallula v2.0: scoring %d hypotheses across %d lenses "
            "(dropout=%.2f, alpha=%.1f, interactions=%s, adaptive=%s)",
            len(hypotheses), n_lenses, dropout_rate, dirichlet_alpha,
            use_interactions, adaptive_lenses,
        )

        # Phase 1: Generate initial lenses
        lenses = generate_lenses(
            n_lenses=n_lenses,
            dropout_rate=dropout_rate,
            dirichlet_alpha=dirichlet_alpha,
            seed=seed,
        )

        # Phase 1b (v2.0): Adaptive lens generation
        if adaptive_lenses and len(hypotheses) >= 5:
            adaptive = generate_adaptive_lenses(
                hypotheses, lenses, n_adaptive=n_adaptive,
                seed=(seed + 1) if seed else None,
            )
            lenses.extend(adaptive)
            logger.info("Added %d adaptive lenses (total: %d)", len(adaptive), len(lenses))

        # Phase 2 + 2b: Ensemble scoring with optional interactions
        ensemble: dict[int, np.ndarray] = {}
        for hyp in hypotheses:
            hid = hyp["hypothesis_id"]
            ensemble[hid] = score_hypothesis_under_lenses_v2(
                hyp["dimension_scores"], lenses,
                use_interactions=use_interactions,
                interaction_strength=interaction_strength,
            )

        # Compute global score threshold from ALL scores
        all_scores = np.concatenate(list(ensemble.values()))
        global_threshold = float(np.percentile(all_scores, 80))

        # Phase 3 + 4 + 5: Analyze each hypothesis
        discoveries = []
        for hyp in hypotheses:
            hid = hyp["hypothesis_id"]
            lens_scores = ensemble[hid]
            dim_scores = hyp["dimension_scores"]
            baseline = hyp.get("composite_score", float(np.mean(lens_scores)))

            ubiquity = compute_ubiquity(lens_scores, global_scores=all_scores)
            resonance = compute_resonance(lens_scores)
            fragility = compute_fragility(dim_scores, baseline)
            stats = compute_score_distribution_stats(lens_scores)

            discovery_class = classify_discovery(
                ubiquity=ubiquity,
                resonance=resonance,
                fragility_index=fragility["fragility_index"],
                mean_score=stats.get("mean", 0.0),
            )

            resonance_profile = None
            if discovery_class != "weak":
                resonance_profile = decompose_resonance(
                    dim_scores, lenses, lens_scores
                )

            disc_entry = {
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
            }
            discoveries.append(disc_entry)

        # Sort: resonant first, then robust, moderate, fragile, weak
        class_order = {"resonant": 0, "robust": 1, "moderate": 2, "fragile": 3, "weak": 4}
        discoveries.sort(
            key=lambda d: (
                class_order.get(d["discovery_class"], 5),
                -d["score_distribution"].get("max", 0),
            )
        )

        # Phase 6 (v2.0): Cross-cancer transfer
        transfer_candidates = []
        if cross_cancer_transfer:
            transfer_candidates = find_cross_cancer_candidates(
                discoveries, hypotheses
            )
            if transfer_candidates:
                logger.info("Found %d cross-cancer transfer candidates", len(transfer_candidates))

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
            "version": "2.0",
            "parameters": {
                "n_lenses": len(lenses),
                "n_initial_lenses": n_lenses,
                "n_adaptive_lenses": len(lenses) - n_lenses if adaptive_lenses else 0,
                "dropout_rate": dropout_rate,
                "dirichlet_alpha": dirichlet_alpha,
                "seed": seed,
                "n_hypotheses_input": len(hypotheses),
                "use_interactions": use_interactions,
                "interaction_strength": interaction_strength,
                "adaptive_lenses": adaptive_lenses,
                "cross_cancer_transfer": cross_cancer_transfer,
            },
            "summary": {
                "class_counts": class_counts,
                "n_resonant": class_counts.get("resonant", 0),
                "n_robust": class_counts.get("robust", 0),
                "n_fragile": class_counts.get("fragile", 0),
                "global_score_threshold_p80": round(global_threshold, 2),
                "n_cross_cancer_transfers": len(transfer_candidates),
            },
            "discoveries": discoveries,
            "cross_cancer_transfers": transfer_candidates[:50],
        }

        logger.info(
            "Tallula v2.0 complete: %d resonant, %d robust, %d fragile, "
            "%d moderate, %d weak, %d transfers",
            class_counts.get("resonant", 0),
            class_counts.get("robust", 0),
            class_counts.get("fragile", 0),
            class_counts.get("moderate", 0),
            class_counts.get("weak", 0),
            len(transfer_candidates),
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
                    "gnn_link": h.gnn_link_score or 0.0,
                },
            })

        return self.run(hypotheses, n_lenses=n_lenses, **kwargs)
