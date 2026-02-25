"""Hypothesis generation engine.

Generates drug repurposing hypotheses using 3 strategies:
  1. Direct Target — drug's target gene is mutated in the cancer type
  2. Pathway Mediated — drug's target and cancer's mutations share a pathway
  3. Literature/Clinical — known clinical evidence (approval status, trials)

Scores each hypothesis on 3 dimensions:
  - target_binding_score: strength of drug-target binding (0-100)
  - pathway_overlap_score: pathway connectivity between drug target and cancer (0-100)
  - clinical_evidence_score: known clinical relevance (0-100)

Composite = weighted average (target: 0.40, pathway: 0.35, clinical: 0.25)
"""
import logging
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.drug import Drug
from app.models.target import Target
from app.models.drug_target import DrugTarget
from app.models.cancer_type import CancerType
from app.models.mutation import Mutation
from app.models.pathway import Pathway
from app.models.pathway_target import PathwayTarget
from app.models.hypothesis import Hypothesis
from app.models.ingestion_log import IngestionLog

logger = logging.getLogger(__name__)

# Scoring weights
W_TARGET = 0.40
W_PATHWAY = 0.35
W_CLINICAL = 0.25

# Known drug-cancer pairs with established clinical evidence
# (drug_name_lower, cancer_name_fragment) -> clinical_score_boost
KNOWN_PAIRS = {
    ("imatinib", "chronic myeloid"): 95,
    ("imatinib", "gastrointestinal stromal"): 90,
    ("tamoxifen", "breast"): 95,
    ("letrozole", "breast"): 90,
    ("anastrozole", "breast"): 85,
    ("exemestane", "breast"): 80,
    ("fulvestrant", "breast"): 85,
    ("trastuzumab", "breast"): 90,
    ("pertuzumab", "breast"): 85,
    ("erlotinib", "non-small cell lung"): 85,
    ("gefitinib", "non-small cell lung"): 85,
    ("osimertinib", "non-small cell lung"): 90,
    ("afatinib", "non-small cell lung"): 80,
    ("crizotinib", "non-small cell lung"): 85,
    ("alectinib", "non-small cell lung"): 85,
    ("vemurafenib", "melanoma"): 90,
    ("dabrafenib", "melanoma"): 85,
    ("trametinib", "melanoma"): 85,
    ("pembrolizumab", "melanoma"): 90,
    ("nivolumab", "melanoma"): 85,
    ("ipilimumab", "melanoma"): 80,
    ("enzalutamide", "prostate"): 85,
    ("abiraterone", "prostate"): 85,
    ("olaparib", "ovarian"): 85,
    ("rucaparib", "ovarian"): 80,
    ("niraparib", "ovarian"): 80,
    ("bevacizumab", "colorectal"): 80,
    ("regorafenib", "colorectal"): 75,
    ("sorafenib", "hepatocellular"): 80,
    ("lenvatinib", "hepatocellular"): 80,
    ("everolimus", "renal"): 80,
    ("sunitinib", "renal"): 85,
    ("axitinib", "renal"): 80,
    ("cabozantinib", "renal"): 80,
    ("palbociclib", "breast"): 85,
    ("ribociclib", "breast"): 80,
    ("abemaciclib", "breast"): 80,
    ("ibrutinib", "chronic lymphocytic"): 90,
    ("venetoclax", "chronic lymphocytic"): 85,
    ("acalabrutinib", "chronic lymphocytic"): 80,
    ("rituximab", "diffuse large b-cell"): 85,
    ("bortezomib", "multiple myeloma"): 90,
    ("lenalidomide", "multiple myeloma"): 85,
    ("carfilzomib", "multiple myeloma"): 80,
    ("pomalidomide", "multiple myeloma"): 75,
    ("temozolomide", "glioblastoma"): 80,
    ("gemcitabine", "pancreatic"): 80,
    ("cisplatin", "bladder"): 75,
    ("vismodegib", "basal cell"): 80,
    ("gilteritinib", "acute myeloid"): 80,
    ("midostaurin", "acute myeloid"): 75,
    ("sotorasib", "non-small cell lung"): 80,
    ("larotrectinib", "sarcoma"): 70,
    ("entrectinib", "non-small cell lung"): 75,
    ("trastuzumab", "gastric"): 75,
    ("fluorouracil", "colorectal"): 80,
    ("doxorubicin", "breast"): 75,
    ("paclitaxel", "breast"): 80,
    ("paclitaxel", "ovarian"): 80,
    ("cisplatin", "ovarian"): 80,
    ("carboplatin", "ovarian"): 80,
    ("docetaxel", "prostate"): 75,
    ("docetaxel", "breast"): 75,
    ("capecitabine", "colorectal"): 75,
    ("oxaliplatin", "colorectal"): 80,
    ("irinotecan", "colorectal"): 75,
    ("pemetrexed", "non-small cell lung"): 75,
    ("cisplatin", "non-small cell lung"): 70,
    ("carboplatin", "non-small cell lung"): 70,
    ("atezolizumab", "non-small cell lung"): 80,
    ("durvalumab", "non-small cell lung"): 75,
    ("nivolumab", "renal"): 80,
    ("pembrolizumab", "non-small cell lung"): 85,
    ("erdafitinib", "bladder"): 75,
    ("encorafenib", "colorectal"): 70,
    ("alpelisib", "breast"): 75,
    ("tazemetostat", "sarcoma"): 70,
    ("infigratinib", "cholangiocarcinoma"): 70,
    ("pemigatinib", "cholangiocarcinoma"): 70,
    ("futibatinib", "cholangiocarcinoma"): 70,
    ("ivosidenib", "cholangiocarcinoma"): 75,
    ("ivosidenib", "acute myeloid"): 75,
    ("enasidenib", "acute myeloid"): 70,
}


class HypothesisEngine:
    def __init__(self, db: AsyncSession, log_id: int):
        self.db = db
        self.log_id = log_id

    async def generate_all(self):
        """Generate hypotheses for all drug-cancer combinations."""
        drugs = (await self.db.execute(select(Drug))).scalars().all()
        cancer_types = (await self.db.execute(select(CancerType))).scalars().all()

        total = len(drugs) * len(cancer_types)
        await self._update_log(total_expected=total)

        # Pre-load all DrugTargets with joined Targets in one query (fixes N+1)
        all_drug_targets = (await self.db.execute(
            select(DrugTarget).options(selectinload(DrugTarget.target))
        )).scalars().all()

        # Group by drug_id
        drug_targets_map: dict[int, list[DrugTarget]] = defaultdict(list)
        drug_target_genes_map: dict[int, set[str]] = defaultdict(set)
        for dt in all_drug_targets:
            drug_targets_map[dt.drug_id].append(dt)
            if dt.target and dt.target.gene_symbol:
                drug_target_genes_map[dt.drug_id].add(dt.target.gene_symbol)

        # Pre-load all Mutations grouped by cancer_type_id (fixes N+1)
        all_mutations = (await self.db.execute(select(Mutation))).scalars().all()
        mutations_map: dict[int, list[Mutation]] = defaultdict(list)
        for m in all_mutations:
            mutations_map[m.cancer_type_id].append(m)

        generated = 0
        processed = 0

        for drug in drugs:
            drug_targets = drug_targets_map.get(drug.id, [])
            target_genes = drug_target_genes_map.get(drug.id, set())

            for cancer in cancer_types:
                try:
                    mutations = mutations_map.get(cancer.id, [])
                    hypothesis = await self._evaluate_pair(
                        drug, cancer, drug_targets, target_genes, mutations
                    )
                    if hypothesis:
                        generated += 1
                except Exception as e:
                    logger.warning(f"Error evaluating {drug.name} + {cancer.name}: {e}")

                processed += 1
                if processed % 100 == 0:
                    await self._update_log(records_processed=generated)

        await self._update_log(
            status="completed",
            records_processed=generated,
            completed_at=datetime.now(timezone.utc),
        )
        logger.info(f"Hypothesis generation complete: {generated} hypotheses from {processed} pairs")

    async def _evaluate_pair(
        self,
        drug: Drug,
        cancer: CancerType,
        drug_targets: list[DrugTarget],
        target_genes: set[str],
        mutations: list[Mutation],
    ) -> Hypothesis | None:
        """Evaluate a drug-cancer pair and create hypothesis if score is sufficient."""
        mutated_genes = {m.gene_symbol for m in mutations}

        # Strategy 1: Direct Target
        strategy = None
        direct_overlap = target_genes & mutated_genes
        if direct_overlap:
            strategy = "direct_target"

        # Strategy 2: Pathway Mediated
        pathway_score_raw = 0.0
        if target_genes:
            pathway_score_raw = await self._score_pathway_overlap(target_genes, mutated_genes)
            if not strategy and pathway_score_raw > 20:
                strategy = "pathway"

        # Strategy 3: Clinical Evidence
        clinical_score = self._score_clinical_evidence(drug, cancer)
        if not strategy and clinical_score > 50:
            strategy = "clinical"

        # If no strategy found a link, skip this pair
        if not strategy:
            return None

        # Score dimensions
        target_score = self._score_target_binding(drug_targets, direct_overlap, mutations)
        pathway_score = min(pathway_score_raw, 100.0)

        # Composite
        composite = (
            W_TARGET * target_score +
            W_PATHWAY * pathway_score +
            W_CLINICAL * clinical_score
        )

        # Minimum threshold
        if composite < 15:
            return None

        # Build evidence summary
        evidence_parts = []
        if direct_overlap:
            evidence_parts.append(f"Direct target overlap: {', '.join(sorted(direct_overlap))}")
        if pathway_score > 20:
            evidence_parts.append(f"Pathway connectivity score: {pathway_score:.0f}")
        if clinical_score > 0:
            evidence_parts.append(f"Clinical evidence score: {clinical_score:.0f}")
        evidence_summary = ". ".join(evidence_parts) if evidence_parts else None

        # Upsert hypothesis
        result = await self.db.execute(
            select(Hypothesis).where(
                Hypothesis.drug_id == drug.id,
                Hypothesis.cancer_type_id == cancer.id,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.composite_score = round(composite, 2)
            existing.target_binding_score = round(target_score, 2)
            existing.pathway_overlap_score = round(pathway_score, 2)
            existing.clinical_evidence_score = round(clinical_score, 2)
            existing.evidence_summary = evidence_summary
            existing.strategy = strategy
            hypothesis = existing
        else:
            hypothesis = Hypothesis(
                drug_id=drug.id,
                cancer_type_id=cancer.id,
                composite_score=round(composite, 2),
                target_binding_score=round(target_score, 2),
                pathway_overlap_score=round(pathway_score, 2),
                clinical_evidence_score=round(clinical_score, 2),
                evidence_summary=evidence_summary,
                strategy=strategy,
                status="generated",
            )
            self.db.add(hypothesis)

        await self.db.commit()
        return hypothesis

    def _score_target_binding(
        self,
        drug_targets: list[DrugTarget],
        direct_overlap: set[str],
        mutations: list[Mutation],
    ) -> float:
        """Score based on drug-target binding strength and overlap with cancer mutations."""
        if not drug_targets:
            return 0.0

        score = 0.0

        # Base score from binding affinity (normalize to nM)
        best_affinity = None
        for dt in drug_targets:
            if dt.binding_affinity and dt.binding_affinity > 0:
                affinity = dt.binding_affinity
                if dt.affinity_units == "uM":
                    affinity *= 1000  # Convert uM to nM
                if best_affinity is None or affinity < best_affinity:
                    best_affinity = affinity

        if best_affinity:
            # Lower affinity (nM) = better binding = higher score
            # < 10 nM → 80-100, 10-100 nM → 60-80, 100-1000 nM → 40-60
            if best_affinity < 10:
                score = 80 + min(20, (10 - best_affinity) * 2)
            elif best_affinity < 100:
                score = 60 + (100 - best_affinity) / 100 * 20
            elif best_affinity < 1000:
                score = 40 + (1000 - best_affinity) / 1000 * 20
            else:
                score = max(10, 40 - (best_affinity - 1000) / 10000 * 30)
        else:
            score = 30  # Default if no affinity data

        # Bonus for direct target overlap with cancer mutations
        if direct_overlap:
            # Check mutation frequency for overlapping genes
            mutation_map = {m.gene_symbol: m for m in mutations}
            for gene in direct_overlap:
                if gene in mutation_map:
                    freq = mutation_map[gene].frequency or 0
                    score += freq * 25  # Up to +25 per gene

        return min(100.0, score)

    async def _score_pathway_overlap(
        self, target_genes: set[str], mutated_genes: set[str]
    ) -> float:
        """Score pathway connectivity between drug targets and cancer mutations."""
        if not target_genes or not mutated_genes:
            return 0.0

        # Find pathways containing drug target genes
        all_genes = list(target_genes | mutated_genes)
        result = await self.db.execute(
            select(PathwayTarget.pathway_id, PathwayTarget.gene_symbol).where(
                PathwayTarget.gene_symbol.in_(all_genes)
            )
        )
        memberships = result.all()

        # Group by pathway
        pathway_genes: dict[int, set[str]] = {}
        for pw_id, gene in memberships:
            pathway_genes.setdefault(pw_id, set()).add(gene)

        # Score: how many pathways connect drug targets to cancer mutations?
        score = 0.0
        connecting_pathways = 0

        for pw_id, genes_in_pathway in pathway_genes.items():
            drug_genes_in_pw = target_genes & genes_in_pathway
            cancer_genes_in_pw = mutated_genes & genes_in_pathway

            if drug_genes_in_pw and cancer_genes_in_pw:
                connecting_pathways += 1
                # Score based on number of genes shared
                overlap_size = len(drug_genes_in_pw) + len(cancer_genes_in_pw)
                score += min(25, overlap_size * 8)

        if connecting_pathways == 0:
            return 0.0

        # Bonus for multiple connecting pathways
        score += min(25, (connecting_pathways - 1) * 12)

        return min(100.0, score)

    def _score_clinical_evidence(self, drug: Drug, cancer: CancerType) -> float:
        """Score based on known clinical evidence for this drug-cancer pair."""
        drug_lower = drug.name.lower()
        cancer_lower = cancer.name.lower()

        # Check known pairs
        for (d, c), score in KNOWN_PAIRS.items():
            if d == drug_lower and c in cancer_lower:
                return float(score)

        # Base score from drug approval status
        if drug.status == "approved":
            return 15.0
        return 5.0

    async def _update_log(self, **kwargs):
        result = await self.db.execute(
            select(IngestionLog).where(IngestionLog.id == self.log_id)
        )
        log = result.scalar_one_or_none()
        if log:
            for k, v in kwargs.items():
                setattr(log, k, v)
            await self.db.commit()
