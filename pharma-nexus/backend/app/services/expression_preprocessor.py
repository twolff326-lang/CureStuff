"""Expression data preprocessor for gene expression analysis.

Handles normalization, filtering, variance computation, and efficient
matrix construction from the gene_expression table for downstream
statistical analysis (differential expression, co-expression, etc.).
"""

import logging
from typing import Any

import numpy as np
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evidence import GeneExpression

logger = logging.getLogger(__name__)


class ExpressionPreprocessor:
    """Preprocesses raw expression data for analysis.

    Handles normalization, filtering, and caching of expression matrices.
    """

    async def preprocess_cancer_type(
        self, cancer_type_id: int, db_session: AsyncSession
    ) -> dict[str, Any]:
        """Preprocess expression data for a cancer type.

        1. Filter out low-expression genes (median < 1 across all samples)
        2. Compute variance per gene (for selecting top variable genes)
        3. Compute summary statistics

        Returns summary: n_genes, n_samples, n_tumor, n_normal, n_genes_filtered
        """
        # Count samples
        sample_counts = await db_session.execute(
            select(
                GeneExpression.is_tumor,
                func.count(func.distinct(GeneExpression.sample_id)),
            )
            .where(GeneExpression.cancer_type_id == cancer_type_id)
            .group_by(GeneExpression.is_tumor)
        )
        counts = {row[0]: row[1] for row in sample_counts}
        n_tumor = counts.get(True, 0)
        n_normal = counts.get(False, 0)

        # Count total distinct genes
        gene_count_result = await db_session.execute(
            select(func.count(func.distinct(GeneExpression.gene_symbol))).where(
                GeneExpression.cancer_type_id == cancer_type_id
            )
        )
        n_genes_total = gene_count_result.scalar() or 0

        # Compute per-gene median expression to identify low-expression genes
        gene_stats_result = await db_session.execute(
            text("""
                SELECT gene_symbol,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY expression_log2) AS median_expr,
                       variance(expression_log2) AS var_expr,
                       count(*) AS n_samples
                FROM gene_expression
                WHERE cancer_type_id = :cancer_type_id
                  AND expression_log2 IS NOT NULL
                GROUP BY gene_symbol
            """),
            {"cancer_type_id": cancer_type_id},
        )
        rows = gene_stats_result.fetchall()

        n_genes_with_data = len(rows)
        n_genes_low_expr = sum(1 for r in rows if r[1] is not None and r[1] < 1.0)
        n_genes_passed = n_genes_with_data - n_genes_low_expr

        return {
            "cancer_type_id": cancer_type_id,
            "n_genes_total": n_genes_total,
            "n_genes_with_data": n_genes_with_data,
            "n_genes_passed_filter": n_genes_passed,
            "n_genes_low_expression": n_genes_low_expr,
            "n_tumor_samples": n_tumor,
            "n_normal_samples": n_normal,
            "has_matched_normals": n_normal >= 3,
        }

    async def get_expression_matrix(
        self,
        cancer_type_id: int,
        gene_symbols: list[str],
        db_session: AsyncSession,
        tumor_only: bool = True,
    ) -> tuple[np.ndarray, list[str], list[str]]:
        """Get expression matrix as numpy array for analysis.

        Returns:
            (matrix, sample_ids, gene_symbols_ordered)
            matrix shape: (n_samples, n_genes)

        For large gene sets, fetches in chunks and assembles.
        """
        is_tumor_filter = [True] if tumor_only else [True, False]

        # Fetch all expression data for these genes in this cancer
        CHUNK_SIZE = 500
        all_data: dict[str, dict[str, float]] = {}  # gene -> {sample_id: value}

        for i in range(0, len(gene_symbols), CHUNK_SIZE):
            chunk = gene_symbols[i : i + CHUNK_SIZE]
            result = await db_session.execute(
                select(
                    GeneExpression.gene_symbol,
                    GeneExpression.sample_id,
                    GeneExpression.expression_log2,
                )
                .where(
                    GeneExpression.cancer_type_id == cancer_type_id,
                    GeneExpression.gene_symbol.in_(chunk),
                    GeneExpression.is_tumor.in_(is_tumor_filter),
                    GeneExpression.expression_log2.isnot(None),
                )
            )
            for row in result:
                gene = row.gene_symbol
                if gene not in all_data:
                    all_data[gene] = {}
                all_data[gene][row.sample_id] = row.expression_log2

        if not all_data:
            return np.empty((0, 0)), [], []

        # Find common sample set across all genes
        sample_sets = [set(v.keys()) for v in all_data.values()]
        common_samples = sorted(set.intersection(*sample_sets)) if sample_sets else []

        if not common_samples:
            # Fall back to union with NaN fill
            all_samples = sorted(set.union(*sample_sets))
            common_samples = all_samples

        genes_ordered = [g for g in gene_symbols if g in all_data]
        matrix = np.full((len(common_samples), len(genes_ordered)), np.nan)

        for j, gene in enumerate(genes_ordered):
            gene_vals = all_data[gene]
            for i, sample in enumerate(common_samples):
                if sample in gene_vals:
                    matrix[i, j] = gene_vals[sample]

        return matrix, common_samples, genes_ordered

    async def compute_gene_variance(
        self, cancer_type_id: int, db_session: AsyncSession, min_samples: int = 20
    ) -> list[dict[str, Any]]:
        """Compute expression variance for all genes in a cancer type.

        Returns list sorted by variance descending.
        Used to select top N most variable genes for co-expression analysis.
        """
        result = await db_session.execute(
            text("""
                SELECT gene_symbol,
                       variance(expression_log2) AS var_expr,
                       avg(expression_log2) AS mean_expr,
                       count(*) AS n_samples
                FROM gene_expression
                WHERE cancer_type_id = :cancer_type_id
                  AND is_tumor = true
                  AND expression_log2 IS NOT NULL
                GROUP BY gene_symbol
                HAVING count(*) >= :min_samples
                ORDER BY variance(expression_log2) DESC NULLS LAST
            """),
            {"cancer_type_id": cancer_type_id, "min_samples": min_samples},
        )
        return [
            {
                "gene_symbol": row[0],
                "variance": float(row[1]) if row[1] is not None else 0.0,
                "mean_expression": float(row[2]) if row[2] is not None else 0.0,
                "n_samples": row[3],
            }
            for row in result.fetchall()
        ]

    async def get_top_variable_genes(
        self,
        cancer_type_id: int,
        db_session: AsyncSession,
        top_n: int = 2000,
    ) -> list[str]:
        """Get the top N most variable genes for this cancer type.

        Used to limit co-expression analysis to informative genes.
        """
        variances = await self.compute_gene_variance(cancer_type_id, db_session)
        return [v["gene_symbol"] for v in variances[:top_n]]

    async def get_gene_sample_counts(
        self,
        cancer_type_id: int,
        db_session: AsyncSession,
        min_tumor_samples: int = 3,
    ) -> list[dict[str, Any]]:
        """Pass 1: Get per-gene sample counts and basic stats from the database.

        Returns lightweight summary rows (~2 MB for 20k genes) instead of
        loading all sample-level expression values (~100-500 MB).

        Used to filter genes before the heavier Pass 2 fetch.
        """
        result = await db_session.execute(
            text("""
                SELECT gene_symbol,
                       is_tumor,
                       count(*) AS n_samples,
                       avg(expression_log2) AS mean_expr,
                       variance(expression_log2) AS var_expr
                FROM gene_expression
                WHERE cancer_type_id = :cancer_type_id
                  AND expression_log2 IS NOT NULL
                GROUP BY gene_symbol, is_tumor
            """),
            {"cancer_type_id": cancer_type_id},
        )

        gene_stats: dict[str, dict[str, Any]] = {}
        for row in result.fetchall():
            gene = row[0]
            if gene not in gene_stats:
                gene_stats[gene] = {
                    "gene_symbol": gene,
                    "n_tumor": 0,
                    "n_normal": 0,
                    "tumor_mean": None,
                    "normal_mean": None,
                    "tumor_var": None,
                    "normal_var": None,
                }
            if row[1]:  # is_tumor
                gene_stats[gene]["n_tumor"] = row[2]
                gene_stats[gene]["tumor_mean"] = float(row[3]) if row[3] is not None else None
                gene_stats[gene]["tumor_var"] = float(row[4]) if row[4] is not None else None
            else:
                gene_stats[gene]["n_normal"] = row[2]
                gene_stats[gene]["normal_mean"] = float(row[3]) if row[3] is not None else None
                gene_stats[gene]["normal_var"] = float(row[4]) if row[4] is not None else None

        # Filter: only genes with enough tumor samples
        return [
            stats for stats in gene_stats.values()
            if stats["n_tumor"] >= min_tumor_samples
        ]

    async def get_grouped_expression_for_genes(
        self,
        cancer_type_id: int,
        gene_symbols: list[str],
        db_session: AsyncSession,
    ) -> dict[str, dict[str, list[float]]]:
        """Pass 2: Get expression values for a specific set of genes.

        Fetches in chunks of 500 genes to control memory usage.
        Used after Pass 1 filtering to only load data for genes that
        will actually be tested.
        """
        CHUNK_SIZE = 500
        gene_data: dict[str, dict[str, list[float]]] = {}

        for i in range(0, len(gene_symbols), CHUNK_SIZE):
            chunk = gene_symbols[i : i + CHUNK_SIZE]
            result = await db_session.execute(
                text("""
                    SELECT gene_symbol,
                           is_tumor,
                           array_agg(expression_log2) AS values
                    FROM gene_expression
                    WHERE cancer_type_id = :cancer_type_id
                      AND expression_log2 IS NOT NULL
                      AND gene_symbol = ANY(:genes)
                    GROUP BY gene_symbol, is_tumor
                """),
                {"cancer_type_id": cancer_type_id, "genes": chunk},
            )

            for row in result.fetchall():
                gene = row[0]
                if gene not in gene_data:
                    gene_data[gene] = {"tumor": [], "normal": []}
                key = "tumor" if row[1] else "normal"
                gene_data[gene][key] = [v for v in row[2] if v is not None]

        return gene_data

    async def get_grouped_expression(
        self,
        cancer_type_id: int,
        db_session: AsyncSession,
    ) -> dict[str, dict[str, list[float]]]:
        """Get expression values grouped by gene and tumor/normal status.

        Returns: {gene_symbol: {'tumor': [values], 'normal': [values]}}

        Uses a two-pass approach to reduce peak memory:
        1. Fetch lightweight per-gene stats to identify testable genes
        2. Only load full sample arrays for genes with enough data

        This typically reduces the number of genes fetched by 20-50%
        compared to loading everything upfront.
        """
        # Pass 1: Get gene-level stats (~2 MB)
        gene_stats = await self.get_gene_sample_counts(
            cancer_type_id, db_session, min_tumor_samples=3
        )

        if not gene_stats:
            return {}

        # Only fetch genes with enough samples for statistical testing
        testable_genes = [g["gene_symbol"] for g in gene_stats]

        # Pass 2: Fetch expression arrays in chunks (~50-200 MB vs 100-500 MB)
        return await self.get_grouped_expression_for_genes(
            cancer_type_id, testable_genes, db_session
        )
