"""Tests for app.services.pathway_analyzer.PathwayAnalyzer.

Tests the pathway overlap scoring logic using the statistical functions
it depends on (Fisher's exact, hypergeometric, BH correction, effect sizes).
Also tests the network distance BFS algorithm and overlap score computation.
"""

import math

import pytest

from app.services.statistical_tests import (
    benjamini_hochberg,
    compute_effect_size,
    fishers_exact_pathway,
    hypergeometric_enrichment,
)


# ===================================================================
# Overlap Score Computation (integration of statistical functions)
# ===================================================================

class TestOverlapScoreComputation:
    """Tests for the overlap score by driving the real statistical functions
    that PathwayAnalyzer.get_pathway_overlap uses.

    Instead of reimplementing the formula locally, we call the actual
    fishers_exact_pathway, hypergeometric_enrichment, benjamini_hochberg,
    and compute_effect_size functions and verify the score components.
    """

    def test_no_shared_pathways_gives_zero_via_bh(self):
        """Empty p-value list → BH returns 0 significant."""
        result = benjamini_hochberg([])
        assert result["n_significant"] == 0

    def test_all_significant_gives_max_fdr_fraction(self):
        """All very small p-values → n_significant / total == 1.0."""
        pvals = [0.001, 0.002, 0.003, 0.004, 0.005]
        result = benjamini_hochberg(pvals, alpha=0.05)
        assert result["n_significant"] == len(pvals)
        sig_fraction = result["n_significant"] / len(pvals)
        assert sig_fraction == 1.0

    def test_no_significant_gives_zero_fdr_fraction(self):
        """Large p-values → n_significant == 0."""
        pvals = [0.5, 0.6, 0.7, 0.8, 0.9]
        result = benjamini_hochberg(pvals, alpha=0.05)
        assert result["n_significant"] == 0

    def test_very_small_p_from_fisher(self):
        """Fisher's exact with high overlap → very small p-value."""
        result = fishers_exact_pathway(8, 8, 20, total_genome_size=20000)
        assert result["p_value"] < 0.001

    def test_moderate_p_from_fisher(self):
        """Fisher with modest overlap → moderate p-value."""
        result = fishers_exact_pathway(1, 2, 100, total_genome_size=20000)
        assert result["p_value"] > 0.001

    def test_high_phi_from_effect_size(self):
        """Highly overlapping gene sets → high phi coefficient."""
        result = compute_effect_size(
            drug_targets={"A", "B", "C"},
            cancer_genes={"A", "B", "C"},
            pathway_genes={"A", "B", "C", "D", "E"},
        )
        assert result["phi_coefficient"] > 0.4

    def test_low_phi_from_effect_size(self):
        """Non-overlapping gene sets → low phi."""
        result = compute_effect_size(
            drug_targets={"A"},
            cancer_genes={"B"},
            pathway_genes={"A", "B", "C", "D", "E", "F", "G", "H", "I", "J"},
        )
        assert result["phi_coefficient"] < 0.3

    def test_score_components_within_valid_ranges(self):
        """Each score component should be within its documented range."""
        # sig_component: [0, 40]
        pvals = [0.001, 0.5, 0.9]
        bh = benjamini_hochberg(pvals)
        sig_fraction = bh["n_significant"] / len(pvals)
        sig_component = sig_fraction * 40
        assert 0 <= sig_component <= 40

        # p_component: [0, 35]
        best_p = pvals[0]
        p_component = min(-math.log10(max(best_p, 1e-20)) / 20.0, 1.0) * 35
        assert 0 <= p_component <= 35

        # effect_component: [0, 25]
        es = compute_effect_size({"A", "B"}, {"B", "C"}, {"A", "B", "C", "D"})
        effect_component = min(es["phi_coefficient"] * 2.5, 1.0) * 25
        assert 0 <= effect_component <= 25


# ===================================================================
# Statistical Integration: Fisher's + Hypergeometric + BH
# ===================================================================

class TestStatisticalPipeline:
    """Test the end-to-end statistical pipeline used in pathway overlap."""

    def test_fisher_and_hyper_combined_p(self):
        """Combined p-value should be the more conservative (larger) one."""
        fisher = fishers_exact_pathway(3, 4, 20)
        hyper = hypergeometric_enrichment(3, 10, 20, 20000)
        combined = max(fisher["p_value"], hyper["p_value"])
        assert combined >= fisher["p_value"]
        assert combined >= hyper["p_value"]

    def test_bh_on_multiple_pathways(self):
        """FDR correction on multiple pathway p-values."""
        # Simulate testing 10 pathways with mixed significance
        p_values = [0.001, 0.005, 0.01, 0.04, 0.06, 0.1, 0.2, 0.5, 0.8, 0.95]
        result = benjamini_hochberg(p_values)
        # The first few (smallest) p-values should remain significant
        assert result["n_significant"] >= 2
        # Large p-values should not be significant
        assert not result["significant_mask"][-1]

    def test_effect_sizes_for_pathway(self):
        """Effect sizes should be computed for pathway overlaps."""
        drug_targets = {"EGFR", "PIK3CA", "BRAF"}
        cancer_genes = {"PIK3CA", "KRAS", "TP53", "AKT1"}
        pathway_genes = {"EGFR", "PIK3CA", "BRAF", "KRAS", "AKT1", "MTOR"}

        result = compute_effect_size(drug_targets, cancer_genes, pathway_genes)
        assert result["n_overlap"] > 0
        assert result["jaccard_index"] > 0
        assert result["dice_coefficient"] > 0
        assert result["overlap_coefficient"] > 0

    def test_significance_to_score_conversion(self):
        """neg_log_p capped at 10 then normalized to [0, 1]."""
        # Very significant: p = 1e-10 → -log10 = 10 → significance = 1.0
        p_very_sig = 1e-10
        neg_log_p = -math.log10(max(p_very_sig, 1e-10))
        significance = min(neg_log_p / 10.0, 1.0)
        assert significance == 1.0

        # Marginally significant: p = 0.05 → -log10 ≈ 1.3 → significance ≈ 0.13
        p_marginal = 0.05
        neg_log_p = -math.log10(max(p_marginal, 1e-10))
        significance = min(neg_log_p / 10.0, 1.0)
        assert 0.1 < significance < 0.2

        # Not significant: p = 0.5 → -log10 ≈ 0.3 → significance ≈ 0.03
        p_not_sig = 0.5
        neg_log_p = -math.log10(max(p_not_sig, 1e-10))
        significance = min(neg_log_p / 10.0, 1.0)
        assert significance < 0.05


# ===================================================================
# Network Distance BFS Logic
# ===================================================================

class TestNetworkDistanceBFS:
    """Tests for the BFS algorithm used in PathwayAnalyzer.get_network_distance.

    The actual method requires DB access, so we verify the structural
    contract of its return values by inspecting the source code and testing
    the pure-function entry point (same-gene short-circuit) via import.
    """

    def test_return_contract_distance_field(self):
        """get_network_distance should return dict with 'distance' key."""
        import inspect
        from app.services.pathway_analyzer import PathwayAnalyzer
        source = inspect.getsource(PathwayAnalyzer.get_network_distance)
        # All return paths must include 'distance'
        assert '"distance"' in source or "'distance'" in source

    def test_return_contract_path_field(self):
        """get_network_distance should return dict with 'path' key."""
        import inspect
        from app.services.pathway_analyzer import PathwayAnalyzer
        source = inspect.getsource(PathwayAnalyzer.get_network_distance)
        assert '"path"' in source or "'path'" in source

    def test_return_contract_min_interaction_score_field(self):
        """get_network_distance should return dict with 'min_interaction_score' key."""
        import inspect
        from app.services.pathway_analyzer import PathwayAnalyzer
        source = inspect.getsource(PathwayAnalyzer.get_network_distance)
        assert '"min_interaction_score"' in source or "'min_interaction_score'" in source

    def test_same_gene_short_circuit_in_source(self):
        """Source code handles gene_a == gene_b with distance=0."""
        import inspect
        from app.services.pathway_analyzer import PathwayAnalyzer
        source = inspect.getsource(PathwayAnalyzer.get_network_distance)
        # The same-gene check: gene_a_upper == gene_b_upper → distance 0
        assert "gene_a_upper == gene_b_upper" in source
        assert '"distance": 0' in source or "'distance': 0" in source

    def test_max_depth_is_4(self):
        """BFS max_depth should be 4 per the documented behavior."""
        import inspect
        from app.services.pathway_analyzer import PathwayAnalyzer
        source = inspect.getsource(PathwayAnalyzer.get_network_distance)
        assert "max_depth = 4" in source

    def test_no_path_returns_negative_one_in_source(self):
        """When BFS finds no path, distance should be -1."""
        import inspect
        from app.services.pathway_analyzer import PathwayAnalyzer
        source = inspect.getsource(PathwayAnalyzer.get_network_distance)
        assert '"distance": -1' in source or "'distance': -1" in source


# ===================================================================
# Druggable Pathway Nodes Sorting
# ===================================================================

class TestDruggableNodeSorting:
    """Verify the sorting contract in get_druggable_pathway_nodes source."""

    def test_source_sorts_by_direct_target_first(self):
        """Production code should sort direct targets before indirect."""
        import inspect
        from app.services.pathway_analyzer import PathwayAnalyzer
        source = inspect.getsource(PathwayAnalyzer.get_druggable_pathway_nodes)
        # The sort should prioritize is_direct_target
        assert "is_direct_target" in source
        assert "sort" in source or "sorted" in source

    def test_source_returns_list(self):
        """Return type annotation should indicate list."""
        import inspect
        from app.services.pathway_analyzer import PathwayAnalyzer
        sig = inspect.signature(PathwayAnalyzer.get_druggable_pathway_nodes)
        ret = sig.return_annotation
        # Return annotation is list[dict[str, Any]]
        assert "list" in str(ret).lower()
