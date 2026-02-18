"""Novelty and plausibility checks that run before declaring a discovery.

Two critical pre-publication checks:

1. PubMed Novelty Check — Query PubMed in real-time to see if the
   drug-cancer combination is already well-studied. A "discovery"
   with 50 existing papers is not a discovery.

2. Dosing Plausibility — Cross-reference the drug's binding affinity
   (IC50/Ki from ChEMBL bioassays) against known pharmacokinetic
   limits. If the drug can't reach therapeutic concentration at the
   target, the hypothesis is dead on arrival.
"""

import asyncio
import logging
from typing import Any

from Bio import Entrez
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.drug import Drug, DrugTarget
from app.models.evidence import Bioassay
from app.models.target import Target

logger = logging.getLogger(__name__)

# Configure Entrez for PubMed queries
Entrez.email = settings.ncbi_email or "pharma-nexus@example.com"
if settings.ncbi_api_key:
    Entrez.api_key = settings.ncbi_api_key

_RATE_DELAY = 0.1 if settings.ncbi_api_key else 0.34


class NoveltyChecker:
    """Check whether a proposed drug-cancer pair is actually novel."""

    async def check_pubmed_novelty(
        self,
        drug_name: str,
        cancer_name: str,
    ) -> dict[str, Any]:
        """Query PubMed for existing papers about this drug-cancer combination.

        Returns:
            paper_count: How many papers already exist
            is_novel: True if fewer than 5 direct co-mention papers
            novelty_tier: "novel" / "understudied" / "known" / "well_studied"
            top_papers: Titles/PMIDs of most relevant existing papers
            search_query: The exact PubMed query used (for reproducibility)
        """
        # Build a targeted PubMed query
        # Use quotes for exact drug name, combine with cancer terms
        query = f'"{drug_name}"[Title/Abstract] AND "{cancer_name}"[Title/Abstract]'

        try:
            # Run in thread to avoid blocking async loop
            result = await asyncio.to_thread(
                self._search_pubmed, query
            )
        except Exception as e:
            logger.warning("PubMed novelty check failed for %s/%s: %s", drug_name, cancer_name, e)
            return {
                "paper_count": -1,
                "is_novel": None,
                "novelty_tier": "check_failed",
                "search_query": query,
                "error": str(e),
                "top_papers": [],
            }

        paper_count = result["count"]
        top_papers = result["papers"]

        # Also try a broader query with MeSH terms for more coverage
        broad_query = f'"{drug_name}"[MeSH Terms] AND "{cancer_name}"[MeSH Terms]'
        try:
            broad_result = await asyncio.to_thread(
                self._search_pubmed, broad_query, max_results=0
            )
            mesh_count = broad_result["count"]
        except Exception:
            mesh_count = 0

        # Use the higher of the two counts
        total_evidence = max(paper_count, mesh_count)

        # Classify novelty
        if total_evidence == 0:
            tier = "novel"
            is_novel = True
        elif total_evidence <= 5:
            tier = "understudied"
            is_novel = True
        elif total_evidence <= 20:
            tier = "known"
            is_novel = False
        else:
            tier = "well_studied"
            is_novel = False

        return {
            "paper_count": paper_count,
            "mesh_count": mesh_count,
            "total_evidence": total_evidence,
            "is_novel": is_novel,
            "novelty_tier": tier,
            "search_query": query,
            "top_papers": top_papers,
            "interpretation": (
                f"No existing papers found — genuinely novel connection"
                if total_evidence == 0
                else f"{total_evidence} existing papers — "
                + (
                    "understudied, worth investigating"
                    if total_evidence <= 5
                    else "already known in literature"
                    if total_evidence <= 20
                    else "well-studied combination, not a novel discovery"
                )
            ),
        }

    def _search_pubmed(
        self, query: str, max_results: int = 5
    ) -> dict[str, Any]:
        """Synchronous PubMed search via BioPython Entrez."""
        handle = Entrez.esearch(
            db="pubmed",
            term=query,
            retmax=max_results,
            sort="relevance",
        )
        search_results = Entrez.read(handle)
        handle.close()

        count = int(search_results.get("Count", 0))
        id_list = search_results.get("IdList", [])

        papers = []
        if id_list and max_results > 0:
            # Fetch titles for the top results
            fetch_handle = Entrez.efetch(
                db="pubmed",
                id=",".join(id_list[:max_results]),
                rettype="medline",
                retmode="text",
            )
            raw = fetch_handle.read()
            fetch_handle.close()

            # Parse MEDLINE format for titles and PMIDs
            current_pmid = None
            current_title = None
            current_year = None
            for line in raw.split("\n"):
                if line.startswith("PMID- "):
                    if current_pmid and current_title:
                        papers.append({
                            "pmid": current_pmid,
                            "title": current_title.strip(),
                            "year": current_year,
                        })
                    current_pmid = line[6:].strip()
                    current_title = None
                    current_year = None
                elif line.startswith("TI  - "):
                    current_title = line[6:]
                elif line.startswith("      ") and current_title and not line.startswith("AB  -"):
                    # Continuation of title
                    current_title += " " + line.strip()
                elif line.startswith("DP  - "):
                    current_year = line[6:10]

            if current_pmid and current_title:
                papers.append({
                    "pmid": current_pmid,
                    "title": current_title.strip(),
                    "year": current_year,
                })

        return {"count": count, "papers": papers}


class DosingPlausibilityChecker:
    """Check whether a drug can plausibly reach therapeutic concentration."""

    # Rough achievable plasma concentrations for approved drugs by class.
    # These are order-of-magnitude estimates from clinical pharmacology.
    # Source: typical Cmax values from FDA labels.
    # Units: nanomolar (nM)
    TYPICAL_CMAX_NM: dict[str, float] = {
        "approved": 5000.0,       # ~5 µM — typical for oral small molecules
        "investigational": 2000.0, # More conservative for investigational
        "experimental": 1000.0,    # Very conservative
    }

    async def check_dosing_plausibility(
        self,
        drug_id: int,
        cancer_type_id: int,
        session: AsyncSession,
    ) -> dict[str, Any]:
        """Check if drug's binding affinity is achievable at clinical doses.

        Compares:
          - Drug's IC50/Ki against its targets (from bioassays)
          - Drug's binding_affinity_nm (from DrugTarget)
          - Estimated achievable plasma concentration (from drug status)

        A drug with IC50 = 50,000 nM but Cmax = 5,000 nM cannot work.
        """
        # Get drug info
        drug_result = await session.execute(
            select(Drug.name, Drug.status).where(Drug.id == drug_id)
        )
        drug_row = drug_result.one_or_none()
        if not drug_row:
            return {
                "plausible": None,
                "verdict": "drug_not_found",
                "details": [],
            }

        drug_name, drug_status = drug_row
        estimated_cmax = self.TYPICAL_CMAX_NM.get(drug_status, 2000.0)

        # Get binding affinities from DrugTarget table
        dt_result = await session.execute(
            select(
                Target.gene_symbol,
                DrugTarget.action_type,
                DrugTarget.binding_affinity_nm,
            )
            .join(Target, Target.id == DrugTarget.target_id)
            .where(DrugTarget.drug_id == drug_id)
        )
        drug_targets = dt_result.all()

        # Get bioassay data (IC50, Ki, Kd, EC50)
        assay_result = await session.execute(
            select(
                Target.gene_symbol,
                Bioassay.activity_type,
                Bioassay.activity_value,
                Bioassay.activity_unit,
            )
            .join(Target, Target.id == Bioassay.target_id)
            .where(
                Bioassay.drug_id == drug_id,
                Bioassay.activity_type.in_(["IC50", "Ki", "Kd", "EC50"]),
                Bioassay.activity_value.isnot(None),
                Bioassay.activity_value > 0,
            )
            .order_by(Bioassay.activity_value)
            .limit(20)
        )
        assays = assay_result.all()

        # Collect all affinity values (normalize to nM)
        affinities: list[dict] = []

        for gene, action, affinity_nm in drug_targets:
            if affinity_nm and affinity_nm > 0:
                affinities.append({
                    "gene": gene,
                    "source": "drug_target",
                    "type": "binding_affinity",
                    "value_nm": affinity_nm,
                    "action": action,
                })

        for gene, act_type, act_value, act_unit in assays:
            value_nm = self._normalize_to_nm(act_value, act_unit)
            if value_nm:
                affinities.append({
                    "gene": gene,
                    "source": "bioassay",
                    "type": act_type,
                    "value_nm": value_nm,
                })

        if not affinities:
            return {
                "plausible": None,
                "verdict": "no_affinity_data",
                "drug_name": drug_name,
                "drug_status": drug_status,
                "estimated_cmax_nm": estimated_cmax,
                "details": [],
                "interpretation": "No binding affinity data available — "
                "cannot assess dosing plausibility. Consider this a gap.",
            }

        # Assess: can the drug reach its targets?
        best_affinity = min(a["value_nm"] for a in affinities)
        worst_affinity = max(a["value_nm"] for a in affinities)
        median_affinity = sorted(a["value_nm"] for a in affinities)[
            len(affinities) // 2
        ]

        # Therapeutic window: Cmax should be at least 3x the IC50/Ki
        # (rough pharmacological rule of thumb for adequate target coverage)
        coverage_ratio = estimated_cmax / median_affinity if median_affinity > 0 else 0

        if coverage_ratio >= 10:
            plausible = True
            verdict = "highly_plausible"
            interpretation = (
                f"Drug achieves ~{coverage_ratio:.0f}x target coverage at typical "
                f"clinical concentrations. Strong pharmacological basis."
            )
        elif coverage_ratio >= 3:
            plausible = True
            verdict = "plausible"
            interpretation = (
                f"Drug achieves ~{coverage_ratio:.0f}x target coverage. "
                f"Adequate for therapeutic effect."
            )
        elif coverage_ratio >= 1:
            plausible = None  # Marginal
            verdict = "marginal"
            interpretation = (
                f"Drug barely reaches target affinity ({coverage_ratio:.1f}x). "
                f"May require high doses or novel delivery. Proceed with caution."
            )
        else:
            plausible = False
            verdict = "implausible"
            interpretation = (
                f"Drug cannot reach therapeutic concentration. "
                f"Median IC50/Ki = {median_affinity:.0f} nM but estimated "
                f"Cmax = {estimated_cmax:.0f} nM ({coverage_ratio:.2f}x coverage). "
                f"This hypothesis is pharmacologically implausible without "
                f"a novel delivery mechanism."
            )

        return {
            "plausible": plausible,
            "verdict": verdict,
            "drug_name": drug_name,
            "drug_status": drug_status,
            "estimated_cmax_nm": estimated_cmax,
            "best_affinity_nm": round(best_affinity, 1),
            "median_affinity_nm": round(median_affinity, 1),
            "worst_affinity_nm": round(worst_affinity, 1),
            "coverage_ratio": round(coverage_ratio, 2),
            "affinity_data_points": len(affinities),
            "details": affinities[:10],
            "interpretation": interpretation,
        }

    @staticmethod
    def _normalize_to_nm(value: float, unit: str | None) -> float | None:
        """Convert activity values to nanomolar."""
        if not value or not unit:
            return None
        unit = unit.lower().strip()
        if unit in ("nm", "nanomolar"):
            return value
        elif unit in ("um", "µm", "micromolar"):
            return value * 1000
        elif unit in ("mm", "millimolar"):
            return value * 1_000_000
        elif unit in ("pm", "picomolar"):
            return value / 1000
        elif unit in ("m", "molar"):
            return value * 1_000_000_000
        return None
