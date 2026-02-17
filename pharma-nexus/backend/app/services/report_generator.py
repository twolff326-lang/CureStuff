"""Report generation service for producing PDF reports and data exports.

Generates professional PDF reports using WeasyPrint (HTML → PDF) and
structured data exports (CSV, JSON) for drug repurposing hypotheses.

Report types:
  1. Individual Hypothesis Report — deep dive on one drug-cancer pair
  2. Cancer Type Summary — all hypotheses for one cancer, ranked
  3. Novel Discoveries Report — top high-novelty, high-score hypotheses
  4. Executive Summary — 2-3 page overview of all findings
  5. Data Export (CSV) — bulk hypothesis data for external analysis
  6. Data Export (JSON) — complete single-hypothesis package
"""

import csv
import json
import logging
import os
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from weasyprint import HTML

from app.models.cancer_type import CancerMolecularProfile, CancerType
from app.models.clinical_trial import ClinicalTrial
from app.models.drug import Drug, DrugTarget, LiteratureDrug, TrialDrug
from app.models.hypothesis import Hypothesis, HypothesisEvidence
from app.models.literature import Literature, LiteratureCancer
from app.models.llm_analysis import HypothesisAnalysis
from app.models.report_cache import ReportCache
from app.models.target import Target
from app.services.pathway_analyzer import PathwayAnalyzer

logger = logging.getLogger(__name__)

# Maximum cache age in hours before a report is considered stale
REPORT_CACHE_MAX_AGE_HOURS = 24


class ReportGenerator:
    """Generates professional PDF reports and data exports for hypotheses.

    Report types:
    1. Individual Hypothesis Report — deep dive on one drug-cancer pair
    2. Cancer Type Summary — all hypotheses for one cancer, ranked
    3. Novel Discoveries Report — top high-novelty, high-score hypotheses
    4. Portfolio Report — comparative analysis across top candidates
    5. Data Export — CSV/JSON of hypothesis data for external analysis
    6. Executive Summary — 2-page overview of all findings

    All PDFs use WeasyPrint (HTML → PDF rendering).
    Templates are Jinja2 HTML with embedded CSS.
    """

    def __init__(self):
        template_dir = Path(__file__).parent.parent / "templates" / "reports"
        self.env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=True,
        )
        self.output_dir = Path("/tmp/reports")
        self.output_dir.mkdir(exist_ok=True)

    def _render_pdf(self, template_name: str, context: dict, filename: str) -> str:
        """Render a Jinja2 template to PDF via WeasyPrint.

        Returns the file path to the generated PDF.
        """
        template = self.env.get_template(template_name)
        html_content = template.render(**context)

        output_path = self.output_dir / filename
        HTML(string=html_content).write_pdf(str(output_path))

        return str(output_path)

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    async def _get_cached_report(
        self,
        report_type: str,
        entity_id: int | None,
        db_session: AsyncSession,
    ) -> str | None:
        """Return cached report path if it exists and is recent enough."""
        from datetime import timedelta

        cutoff = datetime.utcnow() - timedelta(hours=REPORT_CACHE_MAX_AGE_HOURS)
        query = (
            select(ReportCache)
            .where(
                ReportCache.report_type == report_type,
                ReportCache.generated_at >= cutoff,
            )
        )
        if entity_id is not None:
            query = query.where(ReportCache.entity_id == entity_id)
        else:
            query = query.where(ReportCache.entity_id.is_(None))

        query = query.order_by(ReportCache.generated_at.desc()).limit(1)
        result = await db_session.execute(query)
        cached = result.scalar_one_or_none()

        if cached and os.path.exists(cached.file_path):
            return cached.file_path
        return None

    async def _save_cache_entry(
        self,
        report_type: str,
        entity_id: int | None,
        file_path: str,
        db_session: AsyncSession,
    ) -> None:
        """Store a cache entry for a generated report."""
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        entry = ReportCache(
            report_type=report_type,
            entity_id=entity_id,
            file_path=file_path,
            file_size_bytes=file_size,
        )
        db_session.add(entry)
        await db_session.flush()

    # ------------------------------------------------------------------
    # a) Individual Hypothesis Report
    # ------------------------------------------------------------------

    async def generate_hypothesis_report(
        self, hypothesis_id: int, db_session: AsyncSession
    ) -> str:
        """Generate a comprehensive PDF report for a single hypothesis.

        This is the PUBLICATION-QUALITY document. It contains everything
        a researcher needs to evaluate and pursue a hypothesis.

        Sections:
        1. Title page with hypothesis summary and composite score
        2. Executive summary (1 paragraph)
        3. Drug profile (mechanism, targets, pharmacology)
        4. Cancer profile (molecular landscape, key alterations)
        5. Connection analysis (pathway overlap, expression data, interaction network)
        6. Evidence summary table (all evidence records with strength ratings)
        7. Mechanistic narrative (from Claude — Prompt 9)
        8. Critical assessment / Critique (from Claude — Prompt 9)
        9. Literature review (synthesis from Claude — Prompt 9)
        10. Clinical trial summary (if any exist)
        11. Suggested experiments (from Claude — Prompt 9)
        12. Confidence assessment (from Claude — Prompt 9)
        13. Score breakdown (radar chart of 6 sub-scores)
        14. References (PMIDs cited)
        15. Methodology note (how scores were computed)
        16. Appendix: raw evidence data

        Returns: path to generated PDF file
        """
        # 1. Load all data
        hypothesis = await db_session.get(Hypothesis, hypothesis_id)
        if not hypothesis:
            raise ValueError(f"Hypothesis {hypothesis_id} not found")

        drug = await db_session.get(Drug, hypothesis.drug_id)
        cancer = await db_session.get(CancerType, hypothesis.cancer_type_id)

        evidence = (
            await db_session.execute(
                select(HypothesisEvidence)
                .where(HypothesisEvidence.hypothesis_id == hypothesis_id)
                .order_by(HypothesisEvidence.confidence.desc())
            )
        ).scalars().all()

        analyses = (
            await db_session.execute(
                select(HypothesisAnalysis)
                .where(HypothesisAnalysis.hypothesis_id == hypothesis_id)
            )
        ).scalars().all()

        # Organize analyses by type
        analysis_map = {a.analysis_type: a.content for a in analyses}

        # Drug targets with expression data
        drug_targets = await self._get_drug_targets_detail(
            hypothesis.drug_id, hypothesis.cancer_type_id, db_session
        )

        # Pathway overlap
        pathway_data = await self._get_pathway_details(
            hypothesis.drug_id, hypothesis.cancer_type_id, db_session
        )

        # Top mutations in this cancer
        top_mutations = await self._get_top_mutations(
            hypothesis.cancer_type_id, db_session, limit=20
        )

        # Relevant papers
        papers = await self._get_relevant_papers(
            hypothesis.drug_id, hypothesis.cancer_type_id, db_session, limit=20
        )

        # Clinical trials
        trials = await self._get_relevant_trials(
            hypothesis.drug_id, hypothesis.cancer_type_id, db_session
        )

        # 2. Build template context
        context = {
            "report_date": datetime.now().strftime("%B %d, %Y"),
            "hypothesis": {
                "id": hypothesis.id,
                "title": hypothesis.title,
                "composite_score": hypothesis.composite_score,
                "evidence_strength": hypothesis.evidence_strength,
                "status": hypothesis.status,
                "scores": {
                    "pathway_overlap": hypothesis.pathway_overlap_score,
                    "expression_correlation": hypothesis.expression_correlation_score,
                    "literature_support": hypothesis.literature_support_score,
                    "clinical_evidence": hypothesis.clinical_evidence_score,
                    "safety": hypothesis.safety_score,
                    "novelty": hypothesis.novelty_score,
                },
                "summary": hypothesis.summary,
            },
            "drug": {
                "name": drug.name,
                "drugbank_id": drug.drugbank_id,
                "status": drug.status,
                "mechanism": drug.mechanism_of_action or "Not described",
                "indication": drug.indication or "Not described",
                "pharmacodynamics": drug.pharmacodynamics or "Not described",
                "description": (drug.description or "")[:1000],
            },
            "cancer": {
                "name": cancer.name,
                "tcga_code": cancer.tcga_code,
                "tissue": cancer.tissue,
                "organ": cancer.organ,
                "sample_count": cancer.sample_count,
            },
            "drug_targets": drug_targets,
            "pathway_data": pathway_data,
            "top_mutations": top_mutations,
            "evidence": [
                {
                    "type": ev.evidence_type,
                    "source": ev.source_type,
                    "source_id": ev.source_id,
                    "description": ev.description,
                    "strength": ev.strength,
                    "confidence": round(ev.confidence, 2) if ev.confidence else None,
                }
                for ev in evidence
            ],
            "narrative": hypothesis.mechanism_narrative
            or analysis_map.get("narrative", {}).get("text", "Not yet generated"),
            "critique": analysis_map.get("critique", {}),
            "literature_synthesis": analysis_map.get("literature_synthesis", {}),
            "experiment_design": analysis_map.get("experiment_design", {}),
            "confidence_assessment": analysis_map.get("confidence_assessment", {}),
            "papers": papers,
            "trials": trials,
            "evidence_count_by_type": self._count_evidence_by_type(evidence),
            "evidence_count_by_strength": self._count_evidence_by_strength(evidence),
        }

        # 3. Render PDF
        filename = (
            f"hypothesis_{hypothesis_id}_{drug.drugbank_id}_{cancer.tcga_code}"
            f"_{datetime.now().strftime('%Y%m%d')}.pdf"
        )
        filepath = self._render_pdf("hypothesis_report.html", context, filename)
        await self._save_cache_entry("hypothesis", hypothesis_id, filepath, db_session)
        return filepath

    # ------------------------------------------------------------------
    # b) Cancer Type Summary Report
    # ------------------------------------------------------------------

    async def generate_cancer_summary_report(
        self, cancer_type_id: int, db_session: AsyncSession
    ) -> str:
        """Generate a summary report for all hypotheses for one cancer type.

        Sections:
        1. Cancer type overview (molecular landscape, key drivers)
        2. Ranked hypothesis table (top 50 by composite score)
        3. Score distribution charts
        4. Top 5 hypotheses — brief summaries with scores
        5. Novel discovery highlights (high novelty + high score)
        6. Comparative analysis (from Claude — Prompt 9, if generated)
        7. Pathway coverage map (which pathways are targetable?)
        8. Evidence quality overview
        9. Recommended next steps
        """
        cancer = await db_session.get(CancerType, cancer_type_id)
        if not cancer:
            raise ValueError(f"CancerType {cancer_type_id} not found")

        # Get all hypotheses for this cancer, ordered by score
        hypotheses = (
            await db_session.execute(
                select(Hypothesis)
                .where(Hypothesis.cancer_type_id == cancer_type_id)
                .order_by(Hypothesis.composite_score.desc())
                .limit(100)
            )
        ).scalars().all()

        # Load drugs for each hypothesis
        hypothesis_data = []
        for h in hypotheses:
            drug = await db_session.get(Drug, h.drug_id)
            hypothesis_data.append(
                {
                    "id": h.id,
                    "drug_name": drug.name if drug else "?",
                    "drugbank_id": drug.drugbank_id if drug else "?",
                    "composite_score": h.composite_score,
                    "evidence_strength": h.evidence_strength,
                    "pathway_overlap": h.pathway_overlap_score,
                    "expression": h.expression_correlation_score,
                    "literature": h.literature_support_score,
                    "clinical": h.clinical_evidence_score,
                    "safety": h.safety_score,
                    "novelty": h.novelty_score,
                    "summary": h.summary or "",
                    "narrative_snippet": (h.mechanism_narrative or "")[:200],
                }
            )

        # Identify novel discoveries (high novelty + moderate-high score)
        novel_discoveries = [
            h
            for h in hypothesis_data
            if (h["novelty"] or 0) >= 60 and (h["composite_score"] or 0) >= 45
        ]

        # Score distribution data
        score_distribution = self._compute_score_distribution(
            [h["composite_score"] for h in hypothesis_data if h["composite_score"]]
        )

        # Top mutations for context
        top_mutations = await self._get_top_mutations(
            cancer_type_id, db_session, limit=30
        )

        # Get comparative analysis if it exists
        comparative = await self._get_comparative_analysis(
            cancer_type_id, db_session
        )

        context = {
            "report_date": datetime.now().strftime("%B %d, %Y"),
            "cancer": {
                "name": cancer.name,
                "tcga_code": cancer.tcga_code,
                "tissue": cancer.tissue,
                "organ": cancer.organ,
                "sample_count": cancer.sample_count,
            },
            "total_hypotheses": len(hypotheses),
            "hypotheses": hypothesis_data[:50],  # top 50 for table
            "top_5": hypothesis_data[:5],
            "novel_discoveries": novel_discoveries[:10],
            "score_distribution": score_distribution,
            "top_mutations": top_mutations,
            "comparative_analysis": comparative,
            "strength_breakdown": {
                "strong": sum(
                    1
                    for h in hypothesis_data
                    if h["evidence_strength"] == "strong"
                ),
                "moderate": sum(
                    1
                    for h in hypothesis_data
                    if h["evidence_strength"] == "moderate"
                ),
                "weak": sum(
                    1
                    for h in hypothesis_data
                    if h["evidence_strength"] == "weak"
                ),
                "speculative": sum(
                    1
                    for h in hypothesis_data
                    if h["evidence_strength"] == "speculative"
                ),
            },
        }

        filename = (
            f"cancer_summary_{cancer.tcga_code}"
            f"_{datetime.now().strftime('%Y%m%d')}.pdf"
        )
        filepath = self._render_pdf("cancer_summary_report.html", context, filename)
        await self._save_cache_entry(
            "cancer_summary", cancer_type_id, filepath, db_session
        )
        return filepath

    # ------------------------------------------------------------------
    # c) Novel Discoveries Report
    # ------------------------------------------------------------------

    async def generate_novel_discoveries_report(
        self,
        db_session: AsyncSession,
        min_score: int = 45,
        min_novelty: int = 60,
        limit: int = 50,
    ) -> str:
        """Generate a report focused on the most NOVEL, well-scored hypotheses.

        THIS IS THE MONEY REPORT. These are the potential genuine discoveries.

        Sections:
        1. Executive summary — what we found
        2. Methodology overview — how hypotheses are scored
        3. Discovery table — ranked by composite_score, filtered to high novelty
        4. Top 10 detailed profiles — each with narrative, critique, key evidence
        5. Cross-cancer patterns — are any drugs showing up across multiple cancers?
        6. Cross-drug patterns — are multiple drugs targeting the same pathway
           in one cancer?
        7. Recommendations — which discoveries to pursue first
        """
        # Get high-novelty, decent-score hypotheses
        hypotheses = (
            await db_session.execute(
                select(Hypothesis)
                .where(
                    Hypothesis.novelty_score >= min_novelty,
                    Hypothesis.composite_score >= min_score,
                )
                .order_by(Hypothesis.composite_score.desc())
                .limit(limit)
            )
        ).scalars().all()

        hypothesis_data = []
        drug_cancer_map: dict[str, list[str]] = {}
        cancer_drug_map: dict[str, list[str]] = {}

        for h in hypotheses:
            drug = await db_session.get(Drug, h.drug_id)
            cancer = await db_session.get(CancerType, h.cancer_type_id)

            d_name = drug.name if drug else "?"
            c_name = cancer.name if cancer else "?"

            drug_cancer_map.setdefault(d_name, []).append(c_name)
            cancer_drug_map.setdefault(c_name, []).append(d_name)

            # Get evidence count
            ev_count = await db_session.scalar(
                select(func.count(HypothesisEvidence.id)).where(
                    HypothesisEvidence.hypothesis_id == h.id
                )
            )

            # Get analysis if exists
            analyses = (
                await db_session.execute(
                    select(HypothesisAnalysis).where(
                        HypothesisAnalysis.hypothesis_id == h.id
                    )
                )
            ).scalars().all()
            analysis_map = {a.analysis_type: a.content for a in analyses}

            hypothesis_data.append(
                {
                    "id": h.id,
                    "drug_name": d_name,
                    "drugbank_id": drug.drugbank_id if drug else "",
                    "cancer_name": c_name,
                    "tcga_code": cancer.tcga_code if cancer else "",
                    "composite_score": h.composite_score,
                    "evidence_strength": h.evidence_strength,
                    "novelty": h.novelty_score,
                    "pathway_overlap": h.pathway_overlap_score,
                    "expression": h.expression_correlation_score,
                    "literature": h.literature_support_score,
                    "clinical": h.clinical_evidence_score,
                    "safety": h.safety_score,
                    "summary": h.summary or "",
                    "narrative": h.mechanism_narrative or "",
                    "critique": analysis_map.get("critique", {}),
                    "evidence_count": ev_count,
                }
            )

        # Cross-cancer drugs (drugs appearing for 3+ cancers)
        cross_cancer_drugs = {
            d: cs for d, cs in drug_cancer_map.items() if len(cs) >= 3
        }

        # Cross-drug cancers (cancers with 5+ drug candidates)
        cross_drug_cancers = {
            c: ds for c, ds in cancer_drug_map.items() if len(ds) >= 5
        }

        context = {
            "report_date": datetime.now().strftime("%B %d, %Y"),
            "total_discoveries": len(hypothesis_data),
            "min_score": min_score,
            "min_novelty": min_novelty,
            "hypotheses": hypothesis_data,
            "top_10": hypothesis_data[:10],
            "cross_cancer_drugs": cross_cancer_drugs,
            "cross_drug_cancers": cross_drug_cancers,
            "cancer_count": len(set(h["cancer_name"] for h in hypothesis_data)),
            "drug_count": len(set(h["drug_name"] for h in hypothesis_data)),
        }

        filename = f"novel_discoveries_{datetime.now().strftime('%Y%m%d')}.pdf"
        filepath = self._render_pdf(
            "novel_discoveries_report.html", context, filename
        )
        await self._save_cache_entry(
            "novel_discoveries", None, filepath, db_session
        )
        return filepath

    # ------------------------------------------------------------------
    # d) Executive Summary Report
    # ------------------------------------------------------------------

    async def generate_executive_summary(self, db_session: AsyncSession) -> str:
        """Generate a 2-3 page executive summary of ALL findings.

        For stakeholders who need the big picture without the details.

        Sections:
        1. System overview — what Pharma Nexus does (2 sentences)
        2. Data sources — what was ingested (bullet summary)
        3. Key numbers — total hypotheses, by strength, top cancer types
        4. Top 10 discoveries — one-liner each with score
        5. Most promising cancer types — which cancers have the most actionable
           candidates
        6. Recommended immediate actions — what to do next
        """
        # Overall stats
        total_hypotheses = await db_session.scalar(
            select(func.count(Hypothesis.id))
        )
        total_drugs = await db_session.scalar(select(func.count(Drug.id)))
        total_cancers = await db_session.scalar(
            select(func.count(CancerType.id))
        )
        total_papers = await db_session.scalar(
            select(func.count(Literature.id))
        )
        total_trials = await db_session.scalar(
            select(func.count(ClinicalTrial.id))
        )

        # Strength breakdown
        strength_counts = {}
        for strength in ["strong", "moderate", "weak", "speculative"]:
            count = await db_session.scalar(
                select(func.count(Hypothesis.id)).where(
                    Hypothesis.evidence_strength == strength
                )
            )
            strength_counts[strength] = count

        # Top 10 hypotheses
        top_10 = (
            await db_session.execute(
                select(Hypothesis)
                .order_by(Hypothesis.composite_score.desc())
                .limit(10)
            )
        ).scalars().all()

        top_10_data = []
        for h in top_10:
            drug = await db_session.get(Drug, h.drug_id)
            cancer = await db_session.get(CancerType, h.cancer_type_id)
            top_10_data.append(
                {
                    "drug": drug.name if drug else "?",
                    "cancer": cancer.name if cancer else "?",
                    "score": h.composite_score,
                    "strength": h.evidence_strength,
                    "novelty": h.novelty_score,
                    "summary": h.summary or "",
                }
            )

        # Top 10 novel discoveries
        novel_10 = (
            await db_session.execute(
                select(Hypothesis)
                .where(
                    Hypothesis.novelty_score >= 60,
                    Hypothesis.composite_score >= 45,
                )
                .order_by(Hypothesis.composite_score.desc())
                .limit(10)
            )
        ).scalars().all()

        novel_10_data = []
        for h in novel_10:
            drug = await db_session.get(Drug, h.drug_id)
            cancer = await db_session.get(CancerType, h.cancer_type_id)
            novel_10_data.append(
                {
                    "drug": drug.name if drug else "?",
                    "cancer": cancer.name if cancer else "?",
                    "score": h.composite_score,
                    "novelty": h.novelty_score,
                    "summary": h.summary or "",
                }
            )

        # Cancer types with most strong/moderate hypotheses
        cancer_counts = (
            await db_session.execute(
                select(
                    CancerType.name,
                    CancerType.tcga_code,
                    func.count(Hypothesis.id).label("count"),
                )
                .join(Hypothesis, CancerType.id == Hypothesis.cancer_type_id)
                .where(
                    Hypothesis.evidence_strength.in_(["strong", "moderate"])
                )
                .group_by(CancerType.name, CancerType.tcga_code)
                .order_by(func.count(Hypothesis.id).desc())
                .limit(10)
            )
        ).all()

        context = {
            "report_date": datetime.now().strftime("%B %d, %Y"),
            "stats": {
                "total_hypotheses": total_hypotheses,
                "total_drugs": total_drugs,
                "total_cancers": total_cancers,
                "total_papers": total_papers,
                "total_trials": total_trials,
            },
            "strength_counts": strength_counts,
            "top_10": top_10_data,
            "novel_10": novel_10_data,
            "top_cancer_types": [
                {"name": c.name, "tcga": c.tcga_code, "count": c.count}
                for c in cancer_counts
            ],
        }

        filename = f"executive_summary_{datetime.now().strftime('%Y%m%d')}.pdf"
        filepath = self._render_pdf("executive_summary.html", context, filename)
        await self._save_cache_entry(
            "executive_summary", None, filepath, db_session
        )
        return filepath

    # ------------------------------------------------------------------
    # e) Data Export — CSV
    # ------------------------------------------------------------------

    async def export_hypotheses_csv(
        self,
        db_session: AsyncSession,
        cancer_type_id: int | None = None,
        min_score: int = 0,
        min_novelty: int = 0,
    ) -> str:
        """Export hypothesis data as CSV for external analysis (Excel, R, Python).

        Columns:
        hypothesis_id, drug_name, drugbank_id, cancer_name, tcga_code,
        composite_score, evidence_strength, pathway_overlap_score,
        expression_correlation_score, literature_support_score,
        clinical_evidence_score, safety_score, novelty_score,
        evidence_count, summary
        """
        query = (
            select(
                Hypothesis.id,
                Drug.name.label("drug_name"),
                Drug.drugbank_id,
                CancerType.name.label("cancer_name"),
                CancerType.tcga_code,
                Hypothesis.composite_score,
                Hypothesis.evidence_strength,
                Hypothesis.pathway_overlap_score,
                Hypothesis.expression_correlation_score,
                Hypothesis.literature_support_score,
                Hypothesis.clinical_evidence_score,
                Hypothesis.safety_score,
                Hypothesis.novelty_score,
                Hypothesis.summary,
                Hypothesis.status,
            )
            .join(Drug, Hypothesis.drug_id == Drug.id)
            .join(CancerType, Hypothesis.cancer_type_id == CancerType.id)
            .where(
                Hypothesis.composite_score >= min_score,
                Hypothesis.novelty_score >= min_novelty,
            )
        )

        if cancer_type_id:
            query = query.where(Hypothesis.cancer_type_id == cancer_type_id)

        query = query.order_by(Hypothesis.composite_score.desc())
        results = await db_session.execute(query)

        filename = (
            f"hypotheses_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        filepath = self.output_dir / filename

        with open(filepath, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "hypothesis_id",
                    "drug_name",
                    "drugbank_id",
                    "cancer_name",
                    "tcga_code",
                    "composite_score",
                    "evidence_strength",
                    "pathway_overlap_score",
                    "expression_correlation_score",
                    "literature_support_score",
                    "clinical_evidence_score",
                    "safety_score",
                    "novelty_score",
                    "status",
                    "summary",
                ]
            )
            for r in results.all():
                writer.writerow(
                    [
                        r.id,
                        r.drug_name,
                        r.drugbank_id,
                        r.cancer_name,
                        r.tcga_code,
                        r.composite_score,
                        r.evidence_strength,
                        r.pathway_overlap_score,
                        r.expression_correlation_score,
                        r.literature_support_score,
                        r.clinical_evidence_score,
                        r.safety_score,
                        r.novelty_score,
                        r.status,
                        (r.summary or "")[:500],
                    ]
                )

        return str(filepath)

    # ------------------------------------------------------------------
    # f) Data Export — JSON
    # ------------------------------------------------------------------

    async def export_hypothesis_json(
        self, hypothesis_id: int, db_session: AsyncSession
    ) -> str:
        """Export a single hypothesis with ALL data as a complete JSON package.

        This is the machine-readable equivalent of the hypothesis PDF report.
        Includes: hypothesis data, scores, all evidence records, all analyses,
        drug details, cancer details, pathway data, relevant papers, trials.

        Useful for: sharing with collaborators, importing into other tools,
        programmatic analysis, archival.
        """
        hypothesis = await db_session.get(Hypothesis, hypothesis_id)
        if not hypothesis:
            raise ValueError(f"Hypothesis {hypothesis_id} not found")

        drug = await db_session.get(Drug, hypothesis.drug_id)
        cancer = await db_session.get(CancerType, hypothesis.cancer_type_id)

        evidence = (
            await db_session.execute(
                select(HypothesisEvidence).where(
                    HypothesisEvidence.hypothesis_id == hypothesis_id
                )
            )
        ).scalars().all()

        analyses = (
            await db_session.execute(
                select(HypothesisAnalysis).where(
                    HypothesisAnalysis.hypothesis_id == hypothesis_id
                )
            )
        ).scalars().all()

        drug_targets = await self._get_drug_targets_detail(
            hypothesis.drug_id, hypothesis.cancer_type_id, db_session
        )
        pathway_data = await self._get_pathway_details(
            hypothesis.drug_id, hypothesis.cancer_type_id, db_session
        )
        papers = await self._get_relevant_papers(
            hypothesis.drug_id, hypothesis.cancer_type_id, db_session, limit=50
        )
        trials = await self._get_relevant_trials(
            hypothesis.drug_id, hypothesis.cancer_type_id, db_session
        )

        package = {
            "pharma_nexus_version": "1.0",
            "export_date": datetime.now().isoformat(),
            "hypothesis": {
                "id": hypothesis.id,
                "title": hypothesis.title,
                "composite_score": hypothesis.composite_score,
                "evidence_strength": hypothesis.evidence_strength,
                "status": hypothesis.status,
                "scores": {
                    "pathway_overlap": hypothesis.pathway_overlap_score,
                    "expression_correlation": hypothesis.expression_correlation_score,
                    "literature_support": hypothesis.literature_support_score,
                    "clinical_evidence": hypothesis.clinical_evidence_score,
                    "safety": hypothesis.safety_score,
                    "novelty": hypothesis.novelty_score,
                },
                "summary": hypothesis.summary,
                "mechanism_narrative": hypothesis.mechanism_narrative,
            },
            "drug": {
                "name": drug.name,
                "drugbank_id": drug.drugbank_id,
                "status": drug.status,
                "mechanism_of_action": drug.mechanism_of_action,
                "indication": drug.indication,
                "pharmacodynamics": drug.pharmacodynamics,
            },
            "cancer_type": {
                "name": cancer.name,
                "tcga_code": cancer.tcga_code,
                "tissue": cancer.tissue,
                "organ": cancer.organ,
            },
            "drug_targets": drug_targets,
            "pathway_overlap": pathway_data,
            "evidence": [
                {
                    "type": ev.evidence_type,
                    "source": ev.source_type,
                    "source_id": ev.source_id,
                    "description": ev.description,
                    "strength": ev.strength,
                    "confidence": ev.confidence,
                    "raw_data": ev.raw_data,
                }
                for ev in evidence
            ],
            "analyses": {
                a.analysis_type: {
                    "content": a.content,
                    "model_used": a.model_used,
                    "generated_at": (
                        a.created_at.isoformat() if a.created_at else None
                    ),
                }
                for a in analyses
            },
            "papers": papers,
            "clinical_trials": trials,
        }

        filename = (
            f"hypothesis_{hypothesis_id}_full"
            f"_{datetime.now().strftime('%Y%m%d')}.json"
        )
        filepath = self.output_dir / filename

        with open(filepath, "w") as f:
            json.dump(package, f, indent=2, default=str)

        return str(filepath)

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _count_evidence_by_type(self, evidence) -> dict:
        counts: dict[str, int] = {}
        for ev in evidence:
            counts[ev.evidence_type] = counts.get(ev.evidence_type, 0) + 1
        return counts

    def _count_evidence_by_strength(self, evidence) -> dict:
        counts = {"strong": 0, "moderate": 0, "weak": 0}
        for ev in evidence:
            s = ev.strength or "weak"
            counts[s] = counts.get(s, 0) + 1
        return counts

    def _compute_score_distribution(self, scores: list) -> dict:
        return {
            "0-25": sum(1 for s in scores if s is not None and s <= 25),
            "26-50": sum(1 for s in scores if s is not None and 26 <= s <= 50),
            "51-75": sum(1 for s in scores if s is not None and 51 <= s <= 75),
            "76-100": sum(1 for s in scores if s is not None and s >= 76),
        }

    async def _get_top_mutations(
        self,
        cancer_type_id: int,
        db_session: AsyncSession,
        limit: int = 20,
    ) -> list[dict]:
        results = await db_session.execute(
            select(CancerMolecularProfile)
            .where(
                CancerMolecularProfile.cancer_type_id == cancer_type_id,
                CancerMolecularProfile.alteration_type.in_(
                    ["mutation", "amplification", "overexpression"]
                ),
            )
            .order_by(CancerMolecularProfile.frequency_percent.desc())
            .limit(limit)
        )
        return [
            {
                "gene_symbol": r.gene_symbol,
                "alteration_type": r.alteration_type,
                "frequency_percent": float(r.frequency_percent)
                if r.frequency_percent
                else 0.0,
            }
            for r in results.scalars()
        ]

    async def _get_relevant_papers(
        self,
        drug_id: int,
        cancer_type_id: int,
        db_session: AsyncSession,
        limit: int = 20,
    ) -> list[dict]:
        # Papers that mention both drug and cancer
        drug_paper_ids = select(LiteratureDrug.literature_id).where(
            LiteratureDrug.drug_id == drug_id
        )
        cancer_paper_ids = select(LiteratureCancer.literature_id).where(
            LiteratureCancer.cancer_type_id == cancer_type_id
        )
        result = await db_session.execute(
            select(Literature)
            .where(
                Literature.id.in_(drug_paper_ids),
                Literature.id.in_(cancer_paper_ids),
            )
            .order_by(Literature.pub_date.desc().nulls_last())
            .limit(limit)
        )
        papers = result.scalars().all()

        if not papers:
            # Fallback: papers mentioning just the drug
            result = await db_session.execute(
                select(Literature)
                .where(Literature.id.in_(drug_paper_ids))
                .order_by(Literature.pub_date.desc().nulls_last())
                .limit(limit)
            )
            papers = result.scalars().all()

        return [
            {
                "pmid": p.pmid,
                "title": p.title,
                "journal": p.journal,
                "pub_year": p.pub_date.year if p.pub_date else None,
            }
            for p in papers
        ]

    async def _get_relevant_trials(
        self,
        drug_id: int,
        cancer_type_id: int,
        db_session: AsyncSession,
    ) -> list[dict]:
        """Get clinical trials related to a drug, optionally filtered by cancer."""
        result = await db_session.execute(
            select(ClinicalTrial)
            .join(TrialDrug, ClinicalTrial.id == TrialDrug.trial_id)
            .where(TrialDrug.drug_id == drug_id)
            .order_by(ClinicalTrial.start_date.desc().nulls_last())
            .limit(20)
        )
        trials = result.scalars().all()
        return [
            {
                "nct_id": t.nct_id,
                "title": t.title,
                "phase": t.phase,
                "status": t.status,
                "enrollment": t.enrollment,
            }
            for t in trials
        ]

    async def _get_comparative_analysis(
        self,
        cancer_type_id: int,
        db_session: AsyncSession,
    ) -> dict | None:
        """Get a comparative analysis for hypotheses targeting this cancer type."""
        # Find any hypothesis for this cancer that has a comparative analysis
        result = await db_session.execute(
            select(HypothesisAnalysis)
            .join(
                Hypothesis,
                HypothesisAnalysis.hypothesis_id == Hypothesis.id,
            )
            .where(
                Hypothesis.cancer_type_id == cancer_type_id,
                HypothesisAnalysis.analysis_type == "comparative",
            )
            .order_by(HypothesisAnalysis.created_at.desc())
            .limit(1)
        )
        analysis = result.scalar_one_or_none()
        if analysis:
            return analysis.content
        return None

    async def _get_drug_targets_detail(
        self,
        drug_id: int,
        cancer_type_id: int,
        db_session: AsyncSession,
    ) -> list[dict]:
        """Get detailed drug target info with expression data for this cancer."""
        result = await db_session.execute(
            select(DrugTarget, Target)
            .join(Target, DrugTarget.target_id == Target.id)
            .where(DrugTarget.drug_id == drug_id)
        )
        rows = result.all()

        targets = []
        for dt, t in rows:
            # Try to get expression data for this target in this cancer
            expr_row = await db_session.execute(
                select(CancerMolecularProfile)
                .where(
                    CancerMolecularProfile.cancer_type_id == cancer_type_id,
                    CancerMolecularProfile.gene_symbol == t.gene_symbol,
                )
                .limit(1)
            )
            expr = expr_row.scalar_one_or_none()

            targets.append(
                {
                    "gene_symbol": t.gene_symbol,
                    "gene_name": t.gene_name,
                    "uniprot_id": t.uniprot_id,
                    "protein_class": t.protein_class,
                    "action_type": dt.action_type,
                    "known_action": dt.known_action,
                    "binding_affinity_nm": dt.binding_affinity_nm,
                    "function": (t.function_description or "")[:300],
                    "expression_zscore": expr.expression_zscore if expr else None,
                    "overexpression_freq": expr.frequency_percent if expr else None,
                }
            )
        return targets

    async def _get_pathway_details(
        self,
        drug_id: int,
        cancer_type_id: int,
        db_session: AsyncSession,
    ) -> dict:
        """Get shared pathway information between drug targets and cancer genes."""
        analyzer = PathwayAnalyzer(db_session)

        # Get drug target gene symbols
        target_result = await db_session.execute(
            select(Target.gene_symbol)
            .join(DrugTarget, DrugTarget.target_id == Target.id)
            .where(DrugTarget.drug_id == drug_id)
        )
        drug_genes = [r[0] for r in target_result.all()]

        # Get cancer-altered genes
        cancer_genes_result = await db_session.execute(
            select(CancerMolecularProfile.gene_symbol)
            .where(CancerMolecularProfile.cancer_type_id == cancer_type_id)
            .distinct()
        )
        cancer_genes = [r[0] for r in cancer_genes_result.all()]

        # Collect pathways for drug targets
        drug_pathways = []
        for gene in drug_genes[:5]:
            gene_pathways = await analyzer.get_gene_pathways(gene)
            for pw in gene_pathways[:3]:
                drug_pathways.append(
                    {
                        "pathway_name": pw.get("name", ""),
                        "pathway_source": pw.get("source", ""),
                        "drug_targets_in_pathway": [gene],
                        "cancer_genes_in_pathway": [
                            cg
                            for cg in cancer_genes
                            if cg in pw.get("genes", [])
                        ][:5],
                        "p_value": None,
                    }
                )

        # Deduplicate by pathway name
        seen = set()
        shared_pathways = []
        for pw in drug_pathways:
            if pw["pathway_name"] not in seen:
                seen.add(pw["pathway_name"])
                shared_pathways.append(pw)

        return {
            "shared_count": len(shared_pathways),
            "shared_pathways": shared_pathways[:10],
            "drug_genes": drug_genes,
            "cancer_gene_count": len(cancer_genes),
        }
