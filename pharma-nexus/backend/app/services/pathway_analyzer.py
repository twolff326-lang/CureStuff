"""Pathway enrichment analysis service.

Analyzes pathway connections between drugs and cancers to identify
mechanistic connections for drug repurposing. This is the CONNECTIVE
TISSUE that links drugs to cancers through biological pathways.

Simple methods (get_pathway_genes, get_gene_pathways) are fully implemented.
Complex methods (get_pathway_overlap, get_network_distance,
get_druggable_pathway_nodes) are stubs — full implementation in Prompt 8.
"""

import logging
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pathway import Pathway, PathwayTarget
from app.models.target import ProteinInteraction, Target

logger = logging.getLogger(__name__)


class PathwayAnalyzer:
    """Analyzes pathway connections between drugs and cancers.

    This is the core service that enables INDIRECT drug repurposing discovery.
    A drug doesn't need to directly target a mutated gene — it might target
    something upstream or downstream in the same signaling pathway.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_pathway_genes(self, pathway_id: int) -> list[str]:
        """Get all gene symbols in a pathway.

        Reads from the pathways.genes JSONB column for efficiency.
        Falls back to pathway_targets join if genes array is empty.
        """
        # Try the JSONB genes column first
        result = await self._session.execute(
            select(Pathway.genes).where(Pathway.id == pathway_id)
        )
        genes = result.scalar_one_or_none()
        if genes:
            return genes

        # Fallback: join through pathway_targets → targets
        result = await self._session.execute(
            select(Target.gene_symbol)
            .join(PathwayTarget, PathwayTarget.target_id == Target.id)
            .where(PathwayTarget.pathway_id == pathway_id)
        )
        return [row[0] for row in result.all() if row[0]]

    async def get_gene_pathways(self, gene_symbol: str) -> list[dict]:
        """Get all pathways a gene participates in, from both KEGG and Reactome.

        Returns both the leaf pathway AND parent pathways up the tree.
        A gene in "RAF/MAP kinase cascade" is also in "MAPK family
        signaling cascades" is also in "Signal Transduction."
        """
        gene_upper = gene_symbol.upper()

        # Find pathways containing this gene via pathway_targets
        result = await self._session.execute(
            select(Pathway)
            .join(PathwayTarget, PathwayTarget.pathway_id == Pathway.id)
            .join(Target, PathwayTarget.target_id == Target.id)
            .where(Target.gene_symbol == gene_upper)
        )
        direct_pathways = result.scalars().all()

        # Also check the JSONB genes array for any pathways not linked via targets
        from sqlalchemy.dialects.postgresql import array
        from sqlalchemy import cast, String, text

        json_result = await self._session.execute(
            select(Pathway).where(
                Pathway.genes.contains([gene_upper])
            )
        )
        json_pathways = json_result.scalars().all()

        # Merge and deduplicate
        seen_ids: set[int] = set()
        all_pathways: list[dict] = []

        for pw in list(direct_pathways) + list(json_pathways):
            if pw.id in seen_ids:
                continue
            seen_ids.add(pw.id)
            all_pathways.append({
                "pathway_id": pw.id,
                "source": pw.source,
                "external_id": pw.external_id,
                "name": pw.name,
                "category": pw.category,
                "parent_pathway_id": pw.parent_pathway_id,
            })

            # Walk up the hierarchy to include parent pathways
            parent_id = pw.parent_pathway_id
            while parent_id and parent_id not in seen_ids:
                seen_ids.add(parent_id)
                parent_result = await self._session.execute(
                    select(Pathway).where(Pathway.id == parent_id)
                )
                parent = parent_result.scalar_one_or_none()
                if parent:
                    all_pathways.append({
                        "pathway_id": parent.id,
                        "source": parent.source,
                        "external_id": parent.external_id,
                        "name": parent.name,
                        "category": parent.category,
                        "parent_pathway_id": parent.parent_pathway_id,
                    })
                    parent_id = parent.parent_pathway_id
                else:
                    break

        return all_pathways

    async def get_pathway_overlap(
        self, drug_id: int, cancer_type_id: int
    ) -> dict[str, Any]:
        """Find pathways where drug targets and cancer-altered genes co-occur.

        This is THE KEY FUNCTION for hypothesis generation.

        Returns:
            {
                "shared_pathways": [
                    {
                        "pathway_id": 1,
                        "pathway_name": "MAPK signaling",
                        "drug_targets_in_pathway": ["BRAF", "MEK1"],
                        "cancer_altered_genes_in_pathway": ["KRAS", "NRAS"],
                        "overlap_significance": 0.85
                    }
                ],
                "total_drug_target_pathways": 15,
                "total_cancer_altered_pathways": 22,
                "shared_count": 8,
                "overlap_score": 72  # 0-100
            }
        """
        raise NotImplementedError(
            "Full implementation in Prompt 8 (hypothesis engine). "
            "This method will systematically find pathways where a drug's "
            "targets co-occur with a cancer type's altered genes, computing "
            "overlap significance using Fisher's exact test."
        )

    async def get_network_distance(
        self, gene_a: str, gene_b: str
    ) -> dict[str, Any]:
        """Shortest path between two genes in the protein interaction network.

        Uses STRING protein interaction data to find how many hops
        separate two proteins. Fewer hops = more likely functional connection.

        Returns:
            {
                "distance": 2,  # number of hops (0 = same protein, -1 = no path)
                "path": ["BRAF", "MAP2K1", "MAPK1"],  # proteins along the path
                "min_interaction_score": 850  # weakest link in the chain
            }
        """
        raise NotImplementedError(
            "Full implementation in Prompt 8 (hypothesis engine). "
            "This method will use BFS on the protein_interactions table "
            "to find shortest paths, with interaction_score as edge weights."
        )

    async def get_druggable_pathway_nodes(
        self, cancer_type_id: int
    ) -> list[dict[str, Any]]:
        """For a cancer type, find pathway nodes that are both:
        1. In a dysregulated pathway (pathway contains altered genes)
        2. Targetable by an existing drug

        This is essentially a pre-filter for hypothesis candidates.
        """
        raise NotImplementedError(
            "Full implementation in Prompt 8 (hypothesis engine). "
            "This method will cross-reference pathway membership, cancer "
            "molecular profiles, and drug-target mappings to identify "
            "druggable nodes in cancer-altered pathways."
        )
