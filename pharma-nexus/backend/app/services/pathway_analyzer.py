"""Pathway enrichment analysis service.

Analyzes pathway connections between drugs and cancers to identify
mechanistic connections for drug repurposing. This is the CONNECTIVE
TISSUE that links drugs to cancers through biological pathways.

Methods:
  - get_pathway_genes: All genes in a pathway
  - get_gene_pathways: All pathways a gene participates in (with hierarchy)
  - get_pathway_overlap: Shared pathways between drug targets and cancer alterations
  - get_network_distance: Shortest path between genes in PPI network (BFS)
  - get_druggable_pathway_nodes: Druggable nodes in cancer-altered pathways
"""

import logging
from collections import deque
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cancer_type import CancerMolecularProfile
from app.models.drug import Drug, DrugTarget
from app.models.mutation import Mutation
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

        # Fallback: join through pathway_targets -> targets
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
        Uses overlap fraction and pathway size for significance scoring.

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
        # Step 1: Get drug target gene symbols
        drug_targets_result = await self._session.execute(
            select(Target.gene_symbol)
            .join(DrugTarget, DrugTarget.target_id == Target.id)
            .where(DrugTarget.drug_id == drug_id)
        )
        drug_target_genes = set(
            row[0] for row in drug_targets_result.all() if row[0]
        )

        if not drug_target_genes:
            return {
                "shared_pathways": [],
                "total_drug_target_pathways": 0,
                "total_cancer_altered_pathways": 0,
                "shared_count": 0,
                "overlap_score": 0,
            }

        # Step 2: Get cancer-altered gene symbols (mutations + expression changes)
        cancer_genes: set[str] = set()

        # From mutations
        mut_result = await self._session.execute(
            select(Mutation.gene_symbol).where(
                Mutation.cancer_type_id == cancer_type_id
            ).distinct()
        )
        cancer_genes.update(row[0] for row in mut_result.all() if row[0])

        # From molecular profiles (over/underexpression with significant z-score)
        profile_result = await self._session.execute(
            select(CancerMolecularProfile.gene_symbol).where(
                CancerMolecularProfile.cancer_type_id == cancer_type_id,
                CancerMolecularProfile.alteration_type.in_(
                    ["overexpression", "underexpression", "mutation"]
                ),
                func.abs(CancerMolecularProfile.expression_zscore) >= 1.5,
            ).distinct()
        )
        cancer_genes.update(row[0] for row in profile_result.all() if row[0])

        if not cancer_genes:
            return {
                "shared_pathways": [],
                "total_drug_target_pathways": 0,
                "total_cancer_altered_pathways": 0,
                "shared_count": 0,
                "overlap_score": 0,
            }

        # Step 3: Find pathways for drug targets
        drug_pathway_map: dict[int, dict] = {}  # pathway_id -> info
        for gene in drug_target_genes:
            gene_pathways = await self.get_gene_pathways(gene)
            for pw in gene_pathways:
                pid = pw["pathway_id"]
                if pid not in drug_pathway_map:
                    drug_pathway_map[pid] = {
                        "pathway_id": pid,
                        "pathway_name": pw["name"],
                        "source": pw["source"],
                        "drug_targets_in_pathway": [],
                        "cancer_altered_genes_in_pathway": [],
                    }
                if gene not in drug_pathway_map[pid]["drug_targets_in_pathway"]:
                    drug_pathway_map[pid]["drug_targets_in_pathway"].append(gene)

        # Step 4: Find which cancer genes are in drug-target pathways
        cancer_pathway_ids: set[int] = set()
        for gene in cancer_genes:
            gene_upper = gene.upper()
            # Check JSONB genes column
            pw_result = await self._session.execute(
                select(Pathway.id).where(
                    Pathway.genes.contains([gene_upper])
                )
            )
            for row in pw_result.all():
                cancer_pathway_ids.add(row[0])
                if row[0] in drug_pathway_map:
                    info = drug_pathway_map[row[0]]
                    if gene not in info["cancer_altered_genes_in_pathway"]:
                        info["cancer_altered_genes_in_pathway"].append(gene)

            # Also check via pathway_targets
            pt_result = await self._session.execute(
                select(PathwayTarget.pathway_id)
                .join(Target, PathwayTarget.target_id == Target.id)
                .where(Target.gene_symbol == gene_upper)
            )
            for row in pt_result.all():
                cancer_pathway_ids.add(row[0])
                if row[0] in drug_pathway_map:
                    info = drug_pathway_map[row[0]]
                    if gene not in info["cancer_altered_genes_in_pathway"]:
                        info["cancer_altered_genes_in_pathway"].append(gene)

        # Step 5: Identify shared pathways and compute significance
        shared_pathways = []

        for pid, info in drug_pathway_map.items():
            if not info["cancer_altered_genes_in_pathway"]:
                continue

            # Get pathway size
            pathway_genes = await self.get_pathway_genes(pid)
            pathway_size = max(len(pathway_genes), 1)

            # Compute significance based on overlap fraction and pathway size
            drug_in_pw = len(info["drug_targets_in_pathway"])
            cancer_in_pw = len(info["cancer_altered_genes_in_pathway"])
            overlap_genes = set(info["drug_targets_in_pathway"]) & set(
                info["cancer_altered_genes_in_pathway"]
            )

            # Smaller pathways with overlap are more significant
            fraction = (drug_in_pw + cancer_in_pw) / max(pathway_size, 1)
            size_factor = min(1.0, 50 / max(pathway_size, 1))
            direct_bonus = 0.3 if overlap_genes else 0.0
            significance = min(fraction * 0.5 + size_factor * 0.3 + direct_bonus, 1.0)

            info["overlap_significance"] = round(significance, 3)
            info["pathway_size"] = pathway_size
            info["direct_overlap_genes"] = list(overlap_genes)
            shared_pathways.append(info)

        # Sort by significance descending
        shared_pathways.sort(key=lambda x: x["overlap_significance"], reverse=True)

        # Compute overall overlap score (0-100)
        total_drug_pathways = len(drug_pathway_map)
        total_cancer_pathways = len(cancer_pathway_ids)
        shared_count = len(shared_pathways)

        if shared_count == 0:
            overlap_score = 0
        else:
            fraction_score = (shared_count / max(total_drug_pathways, total_cancer_pathways, 1)) * 40
            top_sig = sum(
                pw["overlap_significance"] for pw in shared_pathways[:5]
            ) / min(len(shared_pathways), 5)
            significance_score = top_sig * 60
            overlap_score = min(round(fraction_score + significance_score), 100)

        return {
            "shared_pathways": shared_pathways,
            "total_drug_target_pathways": total_drug_pathways,
            "total_cancer_altered_pathways": total_cancer_pathways,
            "shared_count": shared_count,
            "overlap_score": overlap_score,
        }

    async def get_network_distance(
        self, gene_a: str, gene_b: str
    ) -> dict[str, Any]:
        """Shortest path between two genes in the protein interaction network.

        Uses BFS on the protein_interactions table to find how many hops
        separate two proteins. Fewer hops = more likely functional connection.

        Returns:
            {
                "distance": 2,  # number of hops (0 = same, -1 = no path found)
                "path": ["BRAF", "MAP2K1", "MAPK1"],
                "min_interaction_score": 850
            }
        """
        gene_a_upper = gene_a.upper()
        gene_b_upper = gene_b.upper()

        if gene_a_upper == gene_b_upper:
            return {
                "distance": 0,
                "path": [gene_a_upper],
                "min_interaction_score": 1000,
            }

        # Get uniprot IDs for the genes
        result_a = await self._session.execute(
            select(Target.uniprot_id).where(Target.gene_symbol == gene_a_upper)
        )
        uniprot_a = result_a.scalar_one_or_none()

        result_b = await self._session.execute(
            select(Target.uniprot_id).where(Target.gene_symbol == gene_b_upper)
        )
        uniprot_b = result_b.scalar_one_or_none()

        if not uniprot_a or not uniprot_b:
            return {"distance": -1, "path": [], "min_interaction_score": 0}

        # BFS with max depth of 4
        max_depth = 4
        visited: dict[str, str | None] = {uniprot_a: None}
        edge_scores: dict[tuple[str, str], float] = {}
        queue: deque[tuple[str, int]] = deque([(uniprot_a, 0)])

        found = False
        while queue:
            current, depth = queue.popleft()
            if depth >= max_depth:
                continue

            neighbors_result = await self._session.execute(
                select(
                    ProteinInteraction.protein_a_uniprot,
                    ProteinInteraction.protein_b_uniprot,
                    ProteinInteraction.interaction_score,
                ).where(
                    or_(
                        ProteinInteraction.protein_a_uniprot == current,
                        ProteinInteraction.protein_b_uniprot == current,
                    ),
                    ProteinInteraction.interaction_score >= 400,
                )
            )

            for row in neighbors_result.all():
                neighbor = row[1] if row[0] == current else row[0]
                score = row[2] or 0

                if neighbor not in visited:
                    visited[neighbor] = current
                    edge_scores[(current, neighbor)] = score
                    edge_scores[(neighbor, current)] = score

                    if neighbor == uniprot_b:
                        found = True
                        break

                    queue.append((neighbor, depth + 1))

            if found:
                break

        if not found:
            return {"distance": -1, "path": [], "min_interaction_score": 0}

        # Reconstruct path
        path_uniprots = []
        current = uniprot_b
        while current is not None:
            path_uniprots.append(current)
            current = visited[current]
        path_uniprots.reverse()

        # Convert uniprot IDs to gene symbols
        uniprot_to_gene: dict[str, str] = {}
        if path_uniprots:
            gene_result = await self._session.execute(
                select(Target.uniprot_id, Target.gene_symbol).where(
                    Target.uniprot_id.in_(path_uniprots)
                )
            )
            for row in gene_result.all():
                uniprot_to_gene[row[0]] = row[1]

        path_genes = [uniprot_to_gene.get(u, u) for u in path_uniprots]

        # Find minimum interaction score along the path
        min_score = float("inf")
        for i in range(len(path_uniprots) - 1):
            pair = (path_uniprots[i], path_uniprots[i + 1])
            score = edge_scores.get(pair, 0)
            min_score = min(min_score, score)

        if min_score == float("inf"):
            min_score = 0

        return {
            "distance": len(path_uniprots) - 1,
            "path": path_genes,
            "min_interaction_score": min_score,
        }

    async def get_druggable_pathway_nodes(
        self, cancer_type_id: int
    ) -> list[dict[str, Any]]:
        """For a cancer type, find pathway nodes that are both:
        1. In a dysregulated pathway (pathway contains altered genes)
        2. Targetable by an existing drug

        Returns candidate drug-gene-pathway triples for hypothesis generation.
        """
        # Step 1: Get cancer-altered genes
        cancer_genes: set[str] = set()

        mut_result = await self._session.execute(
            select(Mutation.gene_symbol).where(
                Mutation.cancer_type_id == cancer_type_id
            ).distinct()
        )
        cancer_genes.update(row[0] for row in mut_result.all() if row[0])

        profile_result = await self._session.execute(
            select(CancerMolecularProfile.gene_symbol).where(
                CancerMolecularProfile.cancer_type_id == cancer_type_id,
                CancerMolecularProfile.alteration_type.in_(
                    ["overexpression", "underexpression", "mutation"]
                ),
            ).distinct()
        )
        cancer_genes.update(row[0] for row in profile_result.all() if row[0])

        if not cancer_genes:
            return []

        # Step 2: Find pathways containing these cancer genes
        cancer_pathway_ids: set[int] = set()
        for gene in list(cancer_genes)[:200]:
            pw_result = await self._session.execute(
                select(Pathway.id).where(
                    Pathway.genes.contains([gene.upper()])
                )
            )
            cancer_pathway_ids.update(row[0] for row in pw_result.all())

        if not cancer_pathway_ids:
            return []

        # Step 3: Find drug targets in those pathways
        druggable_nodes = []
        seen = set()

        for pid in list(cancer_pathway_ids)[:100]:
            result = await self._session.execute(
                select(
                    Drug.id,
                    Drug.name,
                    Target.gene_symbol,
                    DrugTarget.action_type,
                    Pathway.id,
                    Pathway.name,
                )
                .join(DrugTarget, DrugTarget.target_id == Target.id)
                .join(Drug, DrugTarget.drug_id == Drug.id)
                .join(PathwayTarget, PathwayTarget.target_id == Target.id)
                .join(Pathway, PathwayTarget.pathway_id == Pathway.id)
                .where(
                    PathwayTarget.pathway_id == pid,
                    Drug.status.in_(["approved", "investigational"]),
                )
            )

            for row in result.all():
                key = (row[0], row[4])  # drug_id, pathway_id
                if key in seen:
                    continue
                seen.add(key)

                is_direct = row[2] in cancer_genes
                druggable_nodes.append({
                    "drug_id": row[0],
                    "drug_name": row[1],
                    "target_gene": row[2],
                    "action_type": row[3],
                    "pathway_id": row[4],
                    "pathway_name": row[5],
                    "is_direct_target": is_direct,
                    "cancer_type_id": cancer_type_id,
                })

        # Sort: direct targets first, then by drug name
        druggable_nodes.sort(
            key=lambda x: (not x["is_direct_target"], x["drug_name"])
        )

        return druggable_nodes
