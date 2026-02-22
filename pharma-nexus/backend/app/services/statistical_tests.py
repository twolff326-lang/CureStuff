"""Statistical significance testing for drug repurposing hypothesis scoring.

Provides proper statistical foundations that replace ad-hoc heuristics:
  - Fisher's exact test for pathway overlap significance
  - Hypergeometric test for gene set enrichment
  - Benjamini-Hochberg FDR correction for multiple hypothesis testing
  - Bootstrap confidence intervals for score estimates
  - Effect size measures (odds ratio, fold enrichment)

References:
  - Fisher, R.A. (1922). On the interpretation of chi-square from contingency tables.
  - Benjamini & Hochberg (1995). Controlling the false discovery rate. JRSS-B 57:289-300.
  - Rivals et al. (2007). Enrichment or depletion of a GO category within a class of genes.
"""

import logging
import math
from typing import Any

import numpy as np
from scipy import stats as scipy_stats

logger = logging.getLogger(__name__)


def fishers_exact_pathway(
    drug_targets_in_pathway: int,
    cancer_genes_in_pathway: int,
    pathway_size: int,
    total_genome_size: int = 20000,
) -> dict[str, Any]:
    """Fisher's exact test for pathway co-enrichment of drug targets and cancer genes.

    Tests whether drug targets and cancer-altered genes co-occur in a pathway
    more than expected by chance.

    Constructs a 2x2 contingency table:
                          In pathway    Not in pathway
        Drug target           a              b
        Not drug target       c              d

    Then tests if cancer genes are enriched among pathway members that are
    also drug targets.

    Args:
        drug_targets_in_pathway: Number of drug target genes found in the pathway
        cancer_genes_in_pathway: Number of cancer-altered genes found in the pathway
        pathway_size: Total number of genes in the pathway
        total_genome_size: Background genome size (default: ~20K protein-coding genes)

    Returns:
        dict with p_value, odds_ratio, fold_enrichment, significant (at alpha=0.05)
    """
    # Contingency table for co-occurrence
    # We ask: are drug targets enriched in this pathway given that cancer genes
    # are also in this pathway?
    a = min(drug_targets_in_pathway, cancer_genes_in_pathway)  # overlap
    b = max(drug_targets_in_pathway - a, 0)  # drug targets in pathway but not cancer genes
    c = max(cancer_genes_in_pathway - a, 0)  # cancer genes in pathway but not drug targets
    d = max(pathway_size - a - b - c, 0)  # neither

    # Ensure valid contingency table
    if a + b + c + d == 0:
        return {
            "p_value": 1.0,
            "odds_ratio": 1.0,
            "fold_enrichment": 0.0,
            "significant": False,
            "test": "fishers_exact",
            "contingency_table": [[a, b], [c, d]],
        }

    try:
        result = scipy_stats.fisher_exact([[a, b], [c, d]], alternative="greater")
        odds_ratio = result.statistic if hasattr(result, 'statistic') else result[0]
        p_value = result.pvalue if hasattr(result, 'pvalue') else result[1]
    except Exception:
        odds_ratio = 1.0
        p_value = 1.0

    # Fold enrichment: observed overlap / expected overlap
    if pathway_size > 0 and total_genome_size > 0:
        expected = (drug_targets_in_pathway * cancer_genes_in_pathway) / pathway_size
        fold_enrichment = a / max(expected, 1e-10)
    else:
        fold_enrichment = 0.0

    return {
        "p_value": float(p_value),
        "odds_ratio": float(odds_ratio) if not math.isinf(odds_ratio) else 999.0,
        "fold_enrichment": round(float(fold_enrichment), 3),
        "significant": p_value < 0.05,
        "test": "fishers_exact",
        "contingency_table": [[a, b], [c, d]],
    }


def hypergeometric_enrichment(
    k: int,
    K: int,
    n: int,
    N: int,
) -> dict[str, Any]:
    """Hypergeometric test for gene set enrichment.

    Tests whether observing k successes in a sample of n drawn from
    a population of N containing K successes is significant.

    Used for: "Are drug target genes significantly enriched in cancer-altered
    pathways, given the background genome?"

    Args:
        k: Number of drug targets found in the pathway (successes in sample)
        K: Total number of drug target genes (total successes in population)
        n: Number of genes in the pathway (sample size)
        N: Total number of genes in genome (population size)

    Returns:
        dict with p_value, expected_k, fold_enrichment, significant
    """
    if N <= 0 or n <= 0 or K <= 0:
        return {
            "p_value": 1.0,
            "expected_k": 0.0,
            "fold_enrichment": 0.0,
            "significant": False,
            "test": "hypergeometric",
        }

    # P(X >= k) = 1 - P(X < k) = 1 - P(X <= k-1)
    # scipy.stats.hypergeom.sf(k-1, N, K, n) gives P(X >= k)
    p_value = float(scipy_stats.hypergeom.sf(k - 1, N, K, n))

    # Expected number of successes
    expected_k = (K * n) / N

    # Fold enrichment
    fold_enrichment = k / expected_k if expected_k > 0 else 0.0

    return {
        "p_value": p_value,
        "expected_k": round(expected_k, 3),
        "fold_enrichment": round(fold_enrichment, 3),
        "significant": p_value < 0.05,
        "test": "hypergeometric",
    }


def benjamini_hochberg(p_values: list[float], alpha: float = 0.05) -> dict[str, Any]:
    """Benjamini-Hochberg procedure for FDR control.

    Controls the expected proportion of false discoveries among rejected
    hypotheses at level alpha.

    Args:
        p_values: List of raw p-values from multiple tests
        alpha: FDR significance level (default 0.05)

    Returns:
        dict with adjusted_p_values, significant_mask, n_significant, fdr_threshold
    """
    n = len(p_values)
    if n == 0:
        return {
            "adjusted_p_values": [],
            "significant_mask": [],
            "n_significant": 0,
            "fdr_threshold": alpha,
        }

    p_arr = np.array(p_values, dtype=np.float64)

    # Sort p-values and track original indices
    sorted_indices = np.argsort(p_arr)
    sorted_p = p_arr[sorted_indices]

    # BH adjustment: p_adj[i] = min(p[i] * n / rank, 1.0)
    # Applied with cumulative minimum from the right
    ranks = np.arange(1, n + 1)
    adjusted = np.minimum(sorted_p * n / ranks, 1.0)

    # Enforce monotonicity (cummin from the right)
    for i in range(n - 2, -1, -1):
        adjusted[i] = min(adjusted[i], adjusted[i + 1])

    # Map back to original order
    adjusted_final = np.empty(n)
    adjusted_final[sorted_indices] = adjusted

    significant = adjusted_final < alpha

    # Find the BH threshold (largest p-value that is still significant)
    sig_p_values = p_arr[significant]
    fdr_threshold = float(sig_p_values.max()) if len(sig_p_values) > 0 else 0.0

    return {
        "adjusted_p_values": adjusted_final.tolist(),
        "significant_mask": significant.tolist(),
        "n_significant": int(significant.sum()),
        "fdr_threshold": fdr_threshold,
    }


def bootstrap_confidence_interval(
    scores: list[float],
    confidence: float = 0.95,
    n_bootstrap: int = 1000,
    statistic: str = "mean",
    seed: int | None = 42,
) -> dict[str, Any]:
    """Non-parametric bootstrap confidence interval for a score statistic.

    Args:
        scores: Observed score values
        confidence: Confidence level (default 0.95 for 95% CI)
        n_bootstrap: Number of bootstrap resamples
        statistic: "mean" or "median"
        seed: Random seed for reproducibility

    Returns:
        dict with point_estimate, ci_lower, ci_upper, ci_width, se
    """
    if not scores:
        return {
            "point_estimate": 0.0,
            "ci_lower": 0.0,
            "ci_upper": 0.0,
            "ci_width": 0.0,
            "standard_error": 0.0,
            "n_samples": 0,
        }

    arr = np.array(scores, dtype=np.float64)
    rng = np.random.default_rng(seed)

    stat_fn = np.mean if statistic == "mean" else np.median
    point_estimate = float(stat_fn(arr))

    if len(arr) < 2:
        return {
            "point_estimate": point_estimate,
            "ci_lower": point_estimate,
            "ci_upper": point_estimate,
            "ci_width": 0.0,
            "standard_error": 0.0,
            "n_samples": len(arr),
        }

    # Bootstrap resampling
    bootstrap_stats = np.empty(n_bootstrap)
    for i in range(n_bootstrap):
        resample = rng.choice(arr, size=len(arr), replace=True)
        bootstrap_stats[i] = stat_fn(resample)

    # Percentile method for CI
    alpha = 1 - confidence
    ci_lower = float(np.percentile(bootstrap_stats, 100 * alpha / 2))
    ci_upper = float(np.percentile(bootstrap_stats, 100 * (1 - alpha / 2)))
    se = float(np.std(bootstrap_stats, ddof=1))

    return {
        "point_estimate": round(point_estimate, 3),
        "ci_lower": round(ci_lower, 3),
        "ci_upper": round(ci_upper, 3),
        "ci_width": round(ci_upper - ci_lower, 3),
        "standard_error": round(se, 3),
        "n_samples": len(arr),
    }


def score_to_p_value(
    observed_score: float,
    null_distribution: list[float],
) -> dict[str, Any]:
    """Convert a raw score to a p-value against an empirical null distribution.

    Used for: "How likely is this composite score under random drug-cancer pairing?"

    Args:
        observed_score: The score to evaluate
        null_distribution: Scores from random permutations

    Returns:
        dict with p_value, z_score, percentile
    """
    if not null_distribution:
        return {
            "p_value": 1.0,
            "z_score": 0.0,
            "percentile": 0.0,
        }

    null = np.array(null_distribution, dtype=np.float64)
    n = len(null)

    # Empirical p-value: fraction of null >= observed
    p_value = float(np.sum(null >= observed_score) / n)

    # Ensure minimum p-value based on number of permutations
    p_value = max(p_value, 1.0 / (n + 1))

    # Z-score relative to null
    null_mean = float(np.mean(null))
    null_std = float(np.std(null, ddof=1))
    z_score = (observed_score - null_mean) / null_std if null_std > 0 else 0.0

    # Percentile
    percentile = float(scipy_stats.percentileofscore(null, observed_score))

    return {
        "p_value": round(p_value, 6),
        "z_score": round(z_score, 3),
        "percentile": round(percentile, 2),
    }


def compute_effect_size(
    drug_targets: set[str],
    cancer_genes: set[str],
    pathway_genes: set[str],
    genome_size: int = 20000,
) -> dict[str, Any]:
    """Compute multiple effect size measures for a pathway overlap.

    Args:
        drug_targets: Set of drug target gene symbols
        cancer_genes: Set of cancer-altered gene symbols
        pathway_genes: Set of genes in the pathway
        genome_size: Background genome size

    Returns:
        dict with jaccard_index, dice_coefficient, overlap_coefficient,
             phi_coefficient, relative_risk
    """
    dt_in_pw = drug_targets & pathway_genes
    cg_in_pw = cancer_genes & pathway_genes
    overlap = dt_in_pw & cg_in_pw

    a = len(overlap)  # both drug target and cancer gene in pathway
    b = len(dt_in_pw) - a  # drug target in pathway, not cancer gene
    c = len(cg_in_pw) - a  # cancer gene in pathway, not drug target
    d = max(len(pathway_genes) - a - b - c, 0)  # neither in pathway

    # Jaccard index: |A ∩ B| / |A ∪ B|
    union = len(dt_in_pw | cg_in_pw)
    jaccard = a / union if union > 0 else 0.0

    # Dice coefficient: 2|A ∩ B| / (|A| + |B|)
    sum_sizes = len(dt_in_pw) + len(cg_in_pw)
    dice = (2 * a) / sum_sizes if sum_sizes > 0 else 0.0

    # Overlap coefficient: |A ∩ B| / min(|A|, |B|)
    min_size = min(len(dt_in_pw), len(cg_in_pw))
    overlap_coeff = a / min_size if min_size > 0 else 0.0

    # Phi coefficient (Matthews correlation coefficient for 2x2)
    n = a + b + c + d
    if n > 0:
        numer = (a * d) - (b * c)
        denom = math.sqrt((a + b) * (c + d) * (a + c) * (b + d))
        phi = numer / denom if denom > 0 else 0.0
    else:
        phi = 0.0

    return {
        "jaccard_index": round(jaccard, 4),
        "dice_coefficient": round(dice, 4),
        "overlap_coefficient": round(overlap_coeff, 4),
        "phi_coefficient": round(phi, 4),
        "n_overlap": a,
        "n_drug_targets_in_pathway": len(dt_in_pw),
        "n_cancer_genes_in_pathway": len(cg_in_pw),
        "pathway_size": len(pathway_genes),
    }
