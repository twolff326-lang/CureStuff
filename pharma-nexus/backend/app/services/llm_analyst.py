"""LLM-powered hypothesis analysis service using Claude.

Generates 6 types of expert analysis for drug repurposing hypotheses:
  1. narrative          — Mechanistic narrative explaining the biology
  2. critique           — Devil's advocate critique identifying weaknesses
  3. comparative        — Comparative analysis against similar hypotheses
  4. literature_synthesis — Synthesis of supporting literature
  5. experiment_design  — Suggested experiments to validate the hypothesis
  6. confidence         — Confidence assessment with precedent analysis

Rate limiting:
  - Max 5 concurrent Claude API calls (asyncio.Semaphore)
  - Max 50 calls per minute (token bucket)

Model selection:
  - claude-sonnet-4-20250514 for bulk analysis
  - claude-opus-4-20250514 for top hypotheses (composite_score >= 70)
"""

import asyncio
import json
import logging
import re
import time
from typing import Any

import anthropic
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.cancer_type import CancerMolecularProfile, CancerType
from app.models.clinical_trial import ClinicalTrial
from app.models.drug import Drug, DrugTarget, LiteratureDrug, TrialDrug
from app.models.hypothesis import Hypothesis, HypothesisEvidence
from app.models.literature import Literature, LiteratureCancer, LiteratureTarget
from app.models.llm_analysis import HypothesisAnalysis, LLMUsageLog
from app.models.mutation import Mutation
from app.models.pathway import PathwayTarget
from app.models.target import Target
from app.services.pathway_analyzer import PathwayAnalyzer

logger = logging.getLogger(__name__)

MODEL_HAIKU = "claude-haiku-4-5-20251001"
MODEL_SONNET = "claude-sonnet-4-20250514"
MODEL_OPUS = "claude-opus-4-20250514"
TOP_HYPOTHESIS_THRESHOLD = 70

# Pricing per 1M tokens (USD)
PRICING = {
    MODEL_HAIKU: {"input": 1.0, "output": 5.0},
    MODEL_SONNET: {"input": 3.0, "output": 15.0},
    MODEL_OPUS: {"input": 15.0, "output": 75.0},
}

# Cost mode → model selection strategy
# economy:  Haiku everywhere (cheapest — ~$0.01/hypothesis for confidence-only)
# standard: Sonnet bulk + Opus for top hypotheses (original default)
# premium:  Opus everywhere (highest quality)
COST_MODE_MODELS = {
    "economy": {"default": MODEL_HAIKU, "top": MODEL_HAIKU},
    "standard": {"default": MODEL_SONNET, "top": MODEL_OPUS},
    "premium": {"default": MODEL_OPUS, "top": MODEL_OPUS},
}

ANALYSIS_TYPES = [
    "narrative",
    "critique",
    "comparative",
    "literature_synthesis",
    "experiment_design",
    "confidence",
]


class RateLimiter:
    """Token-bucket rate limiter for per-minute call caps."""

    def __init__(self, max_per_minute: int = 50):
        self._max = max_per_minute
        self._timestamps: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                # Remove timestamps older than 60 seconds
                self._timestamps = [
                    t for t in self._timestamps if now - t < 60
                ]
                if len(self._timestamps) < self._max:
                    self._timestamps.append(now)
                    return
            # Wait a bit before retrying
            await asyncio.sleep(1.0)


class LLMAnalyst:
    """Claude-powered analysis engine for drug repurposing hypotheses.

    Generates structured, expert-level analyses by assembling data packages
    from the database and sending them to Claude with specialized prompts.
    """

    def __init__(self, cost_mode: str | None = None) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._concurrent_sem = asyncio.Semaphore(5)
        self._rate_limiter = RateLimiter(max_per_minute=50)
        self._cost_mode = cost_mode or settings.llm_cost_mode

    def _select_model(self, hypothesis: Hypothesis) -> str:
        """Select model based on cost mode and hypothesis score."""
        mode = self._cost_mode
        models = COST_MODE_MODELS.get(mode, COST_MODE_MODELS["standard"])
        if hypothesis.composite_score >= TOP_HYPOTHESIS_THRESHOLD:
            return models["top"]
        return models["default"]

    # ------------------------------------------------------------------
    # Core Claude API call with rate limiting and retry
    # ------------------------------------------------------------------

    async def _call_claude(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        """Call Claude API with rate limiting, concurrency control, and retry.

        Returns dict with keys: content, input_tokens, output_tokens, model.
        Retries up to 3 times on rate limit errors with exponential backoff.
        """
        await self._rate_limiter.acquire()

        last_error = None
        for attempt in range(3):
            try:
                async with self._concurrent_sem:
                    start_time = time.monotonic()
                    response = await self._client.messages.create(
                        model=model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        system=system_prompt,
                        messages=[{"role": "user", "content": user_prompt}],
                    )
                    elapsed = time.monotonic() - start_time

                raw_text = response.content[0].text
                parsed = self._parse_json_response(raw_text)

                return {
                    "content": parsed if parsed is not None else {"text": raw_text},
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "model": model,
                    "generation_time": elapsed,
                }
            except anthropic.RateLimitError as e:
                last_error = e
                wait_time = 2 ** (attempt + 1)  # 2, 4, 8 seconds
                logger.warning(
                    "Rate limited by Claude API, retrying in %ds (attempt %d/3)",
                    wait_time,
                    attempt + 1,
                )
                await asyncio.sleep(wait_time)
            except anthropic.APIError as e:
                logger.error("Claude API error: %s", e)
                raise

        raise last_error  # type: ignore[misc]

    @staticmethod
    def _parse_json_response(text: str) -> dict | list | None:
        """Parse JSON from Claude's response, stripping markdown fences."""
        # Strip markdown code fences
        cleaned = re.sub(r"```(?:json)?\s*\n?", "", text)
        cleaned = cleaned.strip()
        # Try direct parse
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        # Try to find JSON object or array in the text
        for pattern in [r"\{[\s\S]*\}", r"\[[\s\S]*\]"]:
            match = re.search(pattern, text)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    continue
        return None

    def _estimate_cost(
        self, model: str, input_tokens: int, output_tokens: int
    ) -> float:
        """Estimate cost in USD based on model pricing."""
        prices = PRICING.get(model, PRICING[MODEL_SONNET])
        return (input_tokens * prices["input"] + output_tokens * prices["output"]) / 1_000_000

    # ------------------------------------------------------------------
    # Data package helpers — assemble context for Claude prompts
    # ------------------------------------------------------------------

    async def _get_drug_targets_detail(
        self, drug: Drug, session: AsyncSession
    ) -> list[dict]:
        """Get detailed info about a drug's targets."""
        result = await session.execute(
            select(DrugTarget, Target)
            .join(Target, DrugTarget.target_id == Target.id)
            .where(DrugTarget.drug_id == drug.id)
        )
        rows = result.all()
        targets = []
        for dt, t in rows:
            targets.append({
                "gene_symbol": t.gene_symbol,
                "gene_name": t.gene_name,
                "protein_class": t.protein_class,
                "action_type": dt.action_type,
                "known_action": dt.known_action,
                "binding_affinity_nm": dt.binding_affinity_nm,
                "function": (t.function_description or "")[:300],
            })
        return targets

    async def _get_pathway_details(
        self, drug: Drug, cancer_type: CancerType, session: AsyncSession
    ) -> list[dict]:
        """Get pathways relevant to the drug-cancer pair."""
        analyzer = PathwayAnalyzer(session)
        # Get drug target gene symbols
        target_result = await session.execute(
            select(Target.gene_symbol)
            .join(DrugTarget, DrugTarget.target_id == Target.id)
            .where(DrugTarget.drug_id == drug.id)
        )
        drug_genes = [r[0] for r in target_result.all()]

        pathways = []
        for gene in drug_genes[:5]:  # Limit to avoid excessive queries
            gene_pathways = await analyzer.get_gene_pathways(gene)
            for pw in gene_pathways[:3]:
                pathways.append({
                    "name": pw.get("name", ""),
                    "source": pw.get("source", ""),
                    "gene": gene,
                    "category": pw.get("category", ""),
                })

        # Deduplicate by name
        seen = set()
        unique_pathways = []
        for pw in pathways:
            if pw["name"] not in seen:
                seen.add(pw["name"])
                unique_pathways.append(pw)
        return unique_pathways[:10]

    async def _get_top_papers(
        self, drug: Drug, cancer_type: CancerType, session: AsyncSession, limit: int = 10
    ) -> list[dict]:
        """Get top relevant papers mentioning both drug and cancer."""
        # Papers that mention the drug
        drug_paper_ids = select(LiteratureDrug.literature_id).where(
            LiteratureDrug.drug_id == drug.id
        )
        # Papers that mention the cancer
        cancer_paper_ids = select(LiteratureCancer.literature_id).where(
            LiteratureCancer.cancer_type_id == cancer_type.id
        )
        # Papers mentioning both
        result = await session.execute(
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
            result = await session.execute(
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
                "abstract": (p.abstract or "")[:500],
                "journal": p.journal,
                "pub_date": str(p.pub_date) if p.pub_date else None,
                "findings": p.extracted_findings,
            }
            for p in papers
        ]

    async def _get_trials(
        self, drug: Drug, session: AsyncSession, limit: int = 10
    ) -> list[dict]:
        """Get clinical trials involving this drug."""
        result = await session.execute(
            select(ClinicalTrial)
            .join(TrialDrug, TrialDrug.trial_id == ClinicalTrial.id)
            .where(TrialDrug.drug_id == drug.id)
            .order_by(ClinicalTrial.start_date.desc().nulls_last())
            .limit(limit)
        )
        trials = result.scalars().all()
        return [
            {
                "nct_id": t.nct_id,
                "title": t.title,
                "phase": t.phase,
                "status": t.status,
                "conditions": t.conditions,
                "enrollment": t.enrollment,
            }
            for t in trials
        ]

    async def _get_papers_for_pair(
        self, drug: Drug, cancer_type: CancerType, session: AsyncSession
    ) -> list[dict]:
        """Get all papers mentioning both drug and cancer (for literature synthesis)."""
        return await self._get_top_papers(drug, cancer_type, session, limit=20)

    async def _get_target_papers(
        self, drug: Drug, session: AsyncSession, limit: int = 10
    ) -> list[dict]:
        """Get papers mentioning the drug's targets."""
        target_ids = select(DrugTarget.target_id).where(DrugTarget.drug_id == drug.id)
        paper_ids = select(LiteratureTarget.literature_id).where(
            LiteratureTarget.target_id.in_(target_ids)
        )
        result = await session.execute(
            select(Literature)
            .where(Literature.id.in_(paper_ids))
            .order_by(Literature.pub_date.desc().nulls_last())
            .limit(limit)
        )
        papers = result.scalars().all()
        return [
            {
                "pmid": p.pmid,
                "title": p.title,
                "abstract": (p.abstract or "")[:500],
                "journal": p.journal,
                "pub_date": str(p.pub_date) if p.pub_date else None,
                "findings": p.extracted_findings,
            }
            for p in papers
        ]

    async def _get_pathway_papers(
        self, drug: Drug, cancer_type: CancerType, session: AsyncSession
    ) -> list[dict]:
        """Get papers about pathways relevant to the pair."""
        # Get drug target IDs
        target_ids_query = select(DrugTarget.target_id).where(
            DrugTarget.drug_id == drug.id
        )
        # Get pathway IDs for those targets
        pathway_ids_query = select(PathwayTarget.pathway_id).where(
            PathwayTarget.target_id.in_(target_ids_query)
        )
        # Get target IDs in those pathways
        all_target_ids_query = select(PathwayTarget.target_id).where(
            PathwayTarget.pathway_id.in_(pathway_ids_query)
        )
        # Get papers mentioning those targets
        paper_ids = select(LiteratureTarget.literature_id).where(
            LiteratureTarget.target_id.in_(all_target_ids_query)
        )
        result = await session.execute(
            select(Literature)
            .where(Literature.id.in_(paper_ids))
            .order_by(Literature.pub_date.desc().nulls_last())
            .limit(10)
        )
        papers = result.scalars().all()
        return [
            {
                "pmid": p.pmid,
                "title": p.title,
                "abstract": (p.abstract or "")[:400],
                "journal": p.journal,
                "findings": p.extracted_findings,
            }
            for p in papers
        ]

    async def _compose_narrative_data_package(
        self, hypothesis: Hypothesis, session: AsyncSession
    ) -> dict:
        """Assemble a comprehensive data package for narrative generation."""
        drug_result = await session.execute(
            select(Drug).where(Drug.id == hypothesis.drug_id)
        )
        drug = drug_result.scalar_one()

        cancer_result = await session.execute(
            select(CancerType).where(CancerType.id == hypothesis.cancer_type_id)
        )
        cancer = cancer_result.scalar_one()

        # Get mutations for this cancer
        mut_result = await session.execute(
            select(Mutation)
            .where(Mutation.cancer_type_id == cancer.id)
            .order_by(Mutation.frequency_percent.desc().nulls_last())
            .limit(20)
        )
        mutations = mut_result.scalars().all()

        # Get molecular profiles
        profile_result = await session.execute(
            select(CancerMolecularProfile)
            .where(CancerMolecularProfile.cancer_type_id == cancer.id)
            .order_by(
                func.abs(CancerMolecularProfile.expression_zscore).desc().nulls_last()
            )
            .limit(20)
        )
        profiles = profile_result.scalars().all()

        # Get evidence records
        evidence_result = await session.execute(
            select(HypothesisEvidence).where(
                HypothesisEvidence.hypothesis_id == hypothesis.id
            )
        )
        evidence_records = evidence_result.scalars().all()

        # Parallel data fetches
        targets, pathways, papers, trials = await asyncio.gather(
            self._get_drug_targets_detail(drug, session),
            self._get_pathway_details(drug, cancer, session),
            self._get_top_papers(drug, cancer, session),
            self._get_trials(drug, session),
        )

        return {
            "drug": {
                "name": drug.name,
                "drugbank_id": drug.drugbank_id,
                "status": drug.status,
                "mechanism_of_action": (drug.mechanism_of_action or "")[:500],
                "pharmacodynamics": (drug.pharmacodynamics or "")[:300],
                "indication": (drug.indication or "")[:300],
                "categories": drug.categories,
            },
            "cancer": {
                "name": cancer.name,
                "tcga_code": cancer.tcga_code,
                "tissue": cancer.tissue,
                "organ": cancer.organ,
            },
            "targets": targets,
            "pathways": pathways,
            "mutations": [
                {
                    "gene_symbol": m.gene_symbol,
                    "mutation_type": m.mutation_type,
                    "protein_change": m.protein_change,
                    "frequency_percent": m.frequency_percent,
                }
                for m in mutations
            ],
            "molecular_profiles": [
                {
                    "gene_symbol": p.gene_symbol,
                    "alteration_type": p.alteration_type,
                    "expression_zscore": p.expression_zscore,
                    "frequency_percent": p.frequency_percent,
                }
                for p in profiles
            ],
            "scores": {
                "composite": hypothesis.composite_score,
                "pathway_overlap": hypothesis.pathway_overlap_score,
                "expression_correlation": hypothesis.expression_correlation_score,
                "literature_support": hypothesis.literature_support_score,
                "clinical_evidence": hypothesis.clinical_evidence_score,
                "safety": hypothesis.safety_score,
                "novelty": hypothesis.novelty_score,
            },
            "evidence": [
                {
                    "type": e.evidence_type,
                    "source_type": e.source_type,
                    "description": e.description,
                    "strength": e.strength,
                    "confidence": e.confidence,
                }
                for e in evidence_records
            ],
            "papers": papers,
            "trials": trials,
        }

    # ------------------------------------------------------------------
    # Storage helper
    # ------------------------------------------------------------------

    async def _store_analysis(
        self,
        hypothesis_id: int,
        analysis_type: str,
        result: dict[str, Any],
        session: AsyncSession,
    ) -> HypothesisAnalysis:
        """Store or update an analysis result (upsert on hypothesis_id + analysis_type)."""
        # Check for existing
        existing_result = await session.execute(
            select(HypothesisAnalysis).where(
                HypothesisAnalysis.hypothesis_id == hypothesis_id,
                HypothesisAnalysis.analysis_type == analysis_type,
            )
        )
        existing = existing_result.scalar_one_or_none()

        if existing:
            existing.content = result["content"]
            existing.model_used = result["model"]
            existing.input_tokens = result.get("input_tokens")
            existing.output_tokens = result.get("output_tokens")
            existing.generation_time_seconds = result.get("generation_time")
            analysis = existing
        else:
            analysis = HypothesisAnalysis(
                hypothesis_id=hypothesis_id,
                analysis_type=analysis_type,
                model_used=result["model"],
                content=result["content"],
                input_tokens=result.get("input_tokens"),
                output_tokens=result.get("output_tokens"),
                generation_time_seconds=result.get("generation_time"),
            )
            session.add(analysis)

        # Log usage
        cost = self._estimate_cost(
            result["model"],
            result.get("input_tokens", 0),
            result.get("output_tokens", 0),
        )
        usage_log = LLMUsageLog(
            hypothesis_id=hypothesis_id,
            analysis_type=analysis_type,
            model=result["model"],
            input_tokens=result.get("input_tokens", 0),
            output_tokens=result.get("output_tokens", 0),
            estimated_cost_usd=cost,
        )
        session.add(usage_log)

        await session.flush()
        return analysis

    # ------------------------------------------------------------------
    # 1. Mechanistic Narrative
    # ------------------------------------------------------------------

    async def generate_narrative(
        self, hypothesis_id: int, session: AsyncSession
    ) -> dict:
        """Generate a mechanistic narrative explaining the drug repurposing biology."""
        hyp_result = await session.execute(
            select(Hypothesis).where(Hypothesis.id == hypothesis_id)
        )
        hypothesis = hyp_result.scalar_one()
        model = self._select_model(hypothesis)
        data = await self._compose_narrative_data_package(hypothesis, session)

        system_prompt = (
            "You are a world-class pharmacologist and molecular biologist specializing "
            "in drug repurposing and cancer therapeutics. Generate a comprehensive, "
            "publication-quality mechanistic narrative explaining how a drug could be "
            "repurposed for a new cancer indication.\n\n"
            "Respond ONLY with a JSON object containing these keys:\n"
            '- "title": A concise, descriptive title for the narrative\n'
            '- "summary": A 2-3 sentence summary of the key mechanism\n'
            '- "molecular_mechanism": Detailed explanation of the molecular mechanism '
            "(3-5 paragraphs)\n"
            '- "pathway_analysis": How the drug\'s targets connect to cancer-relevant '
            "pathways (2-3 paragraphs)\n"
            '- "expression_context": How gene expression patterns support the hypothesis '
            "(1-2 paragraphs)\n"
            '- "clinical_relevance": Clinical implications and translational potential '
            "(1-2 paragraphs)\n"
            '- "key_genes": List of critical genes involved (array of strings)\n'
            '- "key_pathways": List of critical pathways (array of strings)\n'
            '- "confidence_note": Brief note on confidence level\n'
        )

        user_prompt = (
            f"Generate a mechanistic narrative for repurposing {data['drug']['name']} "
            f"({data['drug']['drugbank_id']}) for {data['cancer']['name']} "
            f"({data['cancer']['tcga_code']}).\n\n"
            f"Drug details:\n{json.dumps(data['drug'], indent=2)}\n\n"
            f"Drug targets:\n{json.dumps(data['targets'], indent=2)}\n\n"
            f"Relevant pathways:\n{json.dumps(data['pathways'], indent=2)}\n\n"
            f"Cancer mutations (top by frequency):\n{json.dumps(data['mutations'][:10], indent=2)}\n\n"
            f"Cancer molecular profiles (top dysregulated):\n{json.dumps(data['molecular_profiles'][:10], indent=2)}\n\n"
            f"Scoring breakdown:\n{json.dumps(data['scores'], indent=2)}\n\n"
            f"Supporting evidence records:\n{json.dumps(data['evidence'][:15], indent=2)}\n\n"
            f"Key publications:\n{json.dumps(data['papers'][:5], indent=2)}\n\n"
            f"Clinical trials:\n{json.dumps(data['trials'][:5], indent=2)}\n"
        )

        result = await self._call_claude(system_prompt, user_prompt, model)
        analysis = await self._store_analysis(hypothesis_id, "narrative", result, session)

        # Update hypothesis mechanism_narrative with summary
        content = result["content"]
        if isinstance(content, dict):
            narrative_text = content.get("summary", "")
            if content.get("molecular_mechanism"):
                narrative_text += "\n\n" + content["molecular_mechanism"]
            hypothesis.mechanism_narrative = narrative_text[:5000]

        await session.flush()

        return {
            "hypothesis_id": hypothesis_id,
            "analysis_type": "narrative",
            "model": result["model"],
            "content": result["content"],
            "tokens": {
                "input": result.get("input_tokens"),
                "output": result.get("output_tokens"),
            },
            "generation_time": result.get("generation_time"),
        }

    # ------------------------------------------------------------------
    # 2. Devil's Advocate Critique
    # ------------------------------------------------------------------

    async def generate_critique(
        self, hypothesis_id: int, session: AsyncSession
    ) -> dict:
        """Generate a critical analysis identifying weaknesses and counter-evidence."""
        hyp_result = await session.execute(
            select(Hypothesis).where(Hypothesis.id == hypothesis_id)
        )
        hypothesis = hyp_result.scalar_one()
        model = self._select_model(hypothesis)
        data = await self._compose_narrative_data_package(hypothesis, session)

        system_prompt = (
            "You are a rigorous, skeptical peer reviewer with deep expertise in "
            "pharmacology, oncology, and clinical trials. Your role is to play devil's "
            "advocate and identify every potential weakness, flaw, and counter-argument "
            "against the proposed drug repurposing hypothesis.\n\n"
            "Be thorough but fair. Identify genuine scientific concerns, not strawmen.\n\n"
            "Respond ONLY with a JSON object containing:\n"
            '- "overall_assessment": One of "promising", "cautious", "skeptical", "reject"\n'
            '- "summary": 2-3 sentence summary of major concerns\n'
            '- "mechanistic_weaknesses": Array of objects with "concern" and "severity" '
            '(high/medium/low) and "explanation"\n'
            '- "evidence_gaps": Array of objects with "gap" and "importance" '
            '(critical/important/minor) and "suggestion"\n'
            '- "safety_concerns": Array of objects with "concern" and "severity" '
            'and "mitigation"\n'
            '- "alternative_explanations": Array of strings describing alternative '
            "mechanisms that could explain the observed data\n"
            '- "counter_evidence": Array of objects with "finding" and "source"\n'
            '- "feasibility_issues": Array of objects with "issue" and "category" '
            '(technical/regulatory/commercial)\n'
            '- "recommended_actions": Array of strings with specific next steps\n'
            '- "kill_criteria": Array of strings — findings that would invalidate '
            "the hypothesis\n"
        )

        user_prompt = (
            f"Critically evaluate this drug repurposing hypothesis:\n\n"
            f"Hypothesis: Repurposing {data['drug']['name']} for {data['cancer']['name']}\n"
            f"Composite score: {data['scores']['composite']}/100\n\n"
            f"Drug: {json.dumps(data['drug'], indent=2)}\n\n"
            f"Targets: {json.dumps(data['targets'], indent=2)}\n\n"
            f"Pathways: {json.dumps(data['pathways'], indent=2)}\n\n"
            f"Cancer mutations: {json.dumps(data['mutations'][:10], indent=2)}\n\n"
            f"Expression profiles: {json.dumps(data['molecular_profiles'][:10], indent=2)}\n\n"
            f"Scores: {json.dumps(data['scores'], indent=2)}\n\n"
            f"Evidence: {json.dumps(data['evidence'][:15], indent=2)}\n\n"
            f"Papers: {json.dumps(data['papers'][:5], indent=2)}\n\n"
            f"Trials: {json.dumps(data['trials'][:5], indent=2)}\n"
        )

        result = await self._call_claude(system_prompt, user_prompt, model)
        await self._store_analysis(hypothesis_id, "critique", result, session)

        # Store critique summary on the hypothesis itself
        content = result["content"]
        if isinstance(content, dict):
            hypothesis.critique = {
                "overall_assessment": content.get("overall_assessment"),
                "summary": content.get("summary"),
                "weakness_count": len(content.get("mechanistic_weaknesses", [])),
                "gap_count": len(content.get("evidence_gaps", [])),
                "kill_criteria": content.get("kill_criteria", []),
            }

        await session.flush()

        return {
            "hypothesis_id": hypothesis_id,
            "analysis_type": "critique",
            "model": result["model"],
            "content": result["content"],
            "tokens": {
                "input": result.get("input_tokens"),
                "output": result.get("output_tokens"),
            },
            "generation_time": result.get("generation_time"),
        }

    # ------------------------------------------------------------------
    # 3. Comparative Analysis
    # ------------------------------------------------------------------

    async def generate_comparative_analysis(
        self,
        hypothesis_id: int,
        session: AsyncSession,
        compare_ids: list[int] | None = None,
    ) -> dict:
        """Compare a hypothesis against similar ones to identify unique strengths."""
        hyp_result = await session.execute(
            select(Hypothesis).where(Hypothesis.id == hypothesis_id)
        )
        hypothesis = hyp_result.scalar_one()
        model = self._select_model(hypothesis)

        # Get comparison hypotheses: same cancer or same drug
        if compare_ids:
            comp_result = await session.execute(
                select(Hypothesis).where(Hypothesis.id.in_(compare_ids))
            )
        else:
            # Auto-select: same cancer type, ordered by composite score
            comp_result = await session.execute(
                select(Hypothesis)
                .where(
                    Hypothesis.cancer_type_id == hypothesis.cancer_type_id,
                    Hypothesis.id != hypothesis.id,
                )
                .order_by(Hypothesis.composite_score.desc())
                .limit(5)
            )
        comparisons = comp_result.scalars().all()

        # Get drug and cancer names for the primary hypothesis
        drug_result = await session.execute(
            select(Drug).where(Drug.id == hypothesis.drug_id)
        )
        drug = drug_result.scalar_one()
        cancer_result = await session.execute(
            select(CancerType).where(CancerType.id == hypothesis.cancer_type_id)
        )
        cancer = cancer_result.scalar_one()

        # Build comparison data
        comp_data = []
        for comp in comparisons:
            comp_drug_result = await session.execute(
                select(Drug).where(Drug.id == comp.drug_id)
            )
            comp_drug = comp_drug_result.scalar_one()
            comp_data.append({
                "hypothesis_id": comp.id,
                "drug_name": comp_drug.name,
                "title": comp.title,
                "composite_score": comp.composite_score,
                "evidence_strength": comp.evidence_strength,
                "pathway_overlap_score": comp.pathway_overlap_score,
                "expression_correlation_score": comp.expression_correlation_score,
                "literature_support_score": comp.literature_support_score,
                "clinical_evidence_score": comp.clinical_evidence_score,
                "safety_score": comp.safety_score,
                "novelty_score": comp.novelty_score,
                "mechanism_of_action": (comp_drug.mechanism_of_action or "")[:200],
            })

        system_prompt = (
            "You are a pharmaceutical research strategist with expertise in drug "
            "repurposing portfolio prioritization. Compare the primary hypothesis "
            "against alternatives targeting the same cancer type.\n\n"
            "Respond ONLY with a JSON object containing:\n"
            '- "ranking_rationale": Why the primary hypothesis ranks where it does\n'
            '- "unique_advantages": Array of advantages over alternatives\n'
            '- "unique_disadvantages": Array of disadvantages vs alternatives\n'
            '- "complementary_hypotheses": Array of hypothesis IDs that could '
            "work synergistically\n"
            '- "recommended_priority": One of "high", "medium", "low"\n'
            '- "differentiation_factors": Array of key factors that differentiate '
            "this hypothesis\n"
            '- "portfolio_recommendation": Strategic recommendation for the portfolio\n'
        )

        user_prompt = (
            f"Primary hypothesis: {drug.name} for {cancer.name}\n"
            f"Score: {hypothesis.composite_score}/100\n"
            f"Strength: {hypothesis.evidence_strength}\n"
            f"Summary: {hypothesis.summary}\n\n"
            f"Score breakdown:\n"
            f"  Pathway overlap: {hypothesis.pathway_overlap_score}\n"
            f"  Expression correlation: {hypothesis.expression_correlation_score}\n"
            f"  Literature support: {hypothesis.literature_support_score}\n"
            f"  Clinical evidence: {hypothesis.clinical_evidence_score}\n"
            f"  Safety: {hypothesis.safety_score}\n"
            f"  Novelty: {hypothesis.novelty_score}\n\n"
            f"Comparison hypotheses (same cancer type):\n"
            f"{json.dumps(comp_data, indent=2)}\n"
        )

        result = await self._call_claude(system_prompt, user_prompt, model)
        await self._store_analysis(hypothesis_id, "comparative", result, session)

        return {
            "hypothesis_id": hypothesis_id,
            "analysis_type": "comparative",
            "model": result["model"],
            "content": result["content"],
            "compared_with": [c["hypothesis_id"] for c in comp_data],
            "tokens": {
                "input": result.get("input_tokens"),
                "output": result.get("output_tokens"),
            },
            "generation_time": result.get("generation_time"),
        }

    # ------------------------------------------------------------------
    # 4. Literature Synthesis
    # ------------------------------------------------------------------

    async def synthesize_literature(
        self, hypothesis_id: int, session: AsyncSession
    ) -> dict:
        """Synthesize relevant literature into a coherent evidence narrative."""
        hyp_result = await session.execute(
            select(Hypothesis).where(Hypothesis.id == hypothesis_id)
        )
        hypothesis = hyp_result.scalar_one()
        model = self._select_model(hypothesis)

        drug_result = await session.execute(
            select(Drug).where(Drug.id == hypothesis.drug_id)
        )
        drug = drug_result.scalar_one()
        cancer_result = await session.execute(
            select(CancerType).where(CancerType.id == hypothesis.cancer_type_id)
        )
        cancer = cancer_result.scalar_one()

        # Gather papers from multiple angles
        pair_papers, target_papers, pathway_papers = await asyncio.gather(
            self._get_papers_for_pair(drug, cancer, session),
            self._get_target_papers(drug, session),
            self._get_pathway_papers(drug, cancer, session),
        )

        system_prompt = (
            "You are a systematic review specialist with expertise in drug repurposing "
            "literature. Synthesize the provided publications into a coherent evidence "
            "narrative that supports or refutes the repurposing hypothesis.\n\n"
            "Respond ONLY with a JSON object containing:\n"
            '- "synthesis_summary": 3-5 sentence overview of the literature landscape\n'
            '- "supporting_evidence": Array of objects with "finding", "source_pmid", '
            '"strength" (strong/moderate/weak)\n'
            '- "contradicting_evidence": Array of objects with "finding", "source_pmid", '
            '"concern"\n'
            '- "knowledge_gaps": Array of strings describing what the literature does '
            "NOT address\n"
            '- "emerging_trends": Array of strings describing recent research directions\n'
            '- "key_findings": Array of the 3-5 most important findings with PMIDs\n'
            '- "evidence_quality": One of "strong", "moderate", "limited", "insufficient"\n'
            '- "recommended_reading": Array of PMIDs most critical for understanding '
            "this hypothesis\n"
        )

        user_prompt = (
            f"Synthesize literature for: {drug.name} repurposed for {cancer.name}\n\n"
            f"Drug mechanism: {(drug.mechanism_of_action or '')[:300]}\n"
            f"Current indication: {(drug.indication or '')[:200]}\n\n"
            f"Papers mentioning both drug and cancer:\n"
            f"{json.dumps(pair_papers, indent=2)}\n\n"
            f"Papers about drug targets:\n"
            f"{json.dumps(target_papers[:10], indent=2)}\n\n"
            f"Papers about relevant pathways:\n"
            f"{json.dumps(pathway_papers[:10], indent=2)}\n"
        )

        result = await self._call_claude(system_prompt, user_prompt, model)
        await self._store_analysis(
            hypothesis_id, "literature_synthesis", result, session
        )

        return {
            "hypothesis_id": hypothesis_id,
            "analysis_type": "literature_synthesis",
            "model": result["model"],
            "content": result["content"],
            "papers_analyzed": len(pair_papers) + len(target_papers) + len(pathway_papers),
            "tokens": {
                "input": result.get("input_tokens"),
                "output": result.get("output_tokens"),
            },
            "generation_time": result.get("generation_time"),
        }

    # ------------------------------------------------------------------
    # 5. Experiment Design
    # ------------------------------------------------------------------

    async def design_experiments(
        self, hypothesis_id: int, session: AsyncSession
    ) -> dict:
        """Design validation experiments for the hypothesis."""
        hyp_result = await session.execute(
            select(Hypothesis).where(Hypothesis.id == hypothesis_id)
        )
        hypothesis = hyp_result.scalar_one()
        model = self._select_model(hypothesis)
        data = await self._compose_narrative_data_package(hypothesis, session)

        system_prompt = (
            "You are a translational research expert who designs experiments to "
            "validate drug repurposing hypotheses. Create a practical, staged "
            "experimental plan that progresses from in vitro to in vivo to clinical.\n\n"
            "Respond ONLY with a JSON object containing:\n"
            '- "overview": Brief description of the experimental strategy\n'
            '- "in_vitro_experiments": Array of objects with "experiment", "rationale", '
            '"cell_lines" (suggested), "readouts", "timeline" (weeks), "priority" '
            "(high/medium/low)\n"
            '- "in_vivo_experiments": Array of objects with "experiment", "model_system", '
            '"endpoints", "timeline" (weeks), "ethical_considerations"\n'
            '- "biomarker_strategy": Object with "predictive_biomarkers", '
            '"pharmacodynamic_biomarkers", "response_biomarkers" (each an array)\n'
            '- "clinical_path": Object with "suggested_trial_design", "patient_population", '
            '"primary_endpoint", "secondary_endpoints", "estimated_sample_size"\n'
            '- "go_no_go_criteria": Array of specific criteria for advancing to next stage\n'
            '- "estimated_total_timeline": String describing overall timeline\n'
            '- "key_risks": Array of strings describing main experimental risks\n'
        )

        user_prompt = (
            f"Design validation experiments for repurposing {data['drug']['name']} "
            f"for {data['cancer']['name']}.\n\n"
            f"Drug details:\n{json.dumps(data['drug'], indent=2)}\n\n"
            f"Drug targets:\n{json.dumps(data['targets'], indent=2)}\n\n"
            f"Cancer mutations:\n{json.dumps(data['mutations'][:10], indent=2)}\n\n"
            f"Expression profiles:\n{json.dumps(data['molecular_profiles'][:10], indent=2)}\n\n"
            f"Relevant pathways:\n{json.dumps(data['pathways'], indent=2)}\n\n"
            f"Scores: {json.dumps(data['scores'], indent=2)}\n\n"
            f"Evidence: {json.dumps(data['evidence'][:10], indent=2)}\n"
        )

        # Use temperature=0.1 for experiment design (slightly more creative)
        result = await self._call_claude(
            system_prompt, user_prompt, model, temperature=0.1
        )
        await self._store_analysis(
            hypothesis_id, "experiment_design", result, session
        )

        return {
            "hypothesis_id": hypothesis_id,
            "analysis_type": "experiment_design",
            "model": result["model"],
            "content": result["content"],
            "tokens": {
                "input": result.get("input_tokens"),
                "output": result.get("output_tokens"),
            },
            "generation_time": result.get("generation_time"),
        }

    # ------------------------------------------------------------------
    # 6. Confidence Assessment
    # ------------------------------------------------------------------

    async def assess_confidence(
        self, hypothesis_id: int, session: AsyncSession
    ) -> dict:
        """Assess confidence level with precedent analysis."""
        hyp_result = await session.execute(
            select(Hypothesis).where(Hypothesis.id == hypothesis_id)
        )
        hypothesis = hyp_result.scalar_one()
        model = self._select_model(hypothesis)
        data = await self._compose_narrative_data_package(hypothesis, session)

        # Get any existing analyses for additional context
        existing_result = await session.execute(
            select(HypothesisAnalysis).where(
                HypothesisAnalysis.hypothesis_id == hypothesis_id,
                HypothesisAnalysis.analysis_type.in_(["narrative", "critique"]),
            )
        )
        existing_analyses = existing_result.scalars().all()
        prior_context = {}
        for a in existing_analyses:
            if isinstance(a.content, dict):
                prior_context[a.analysis_type] = {
                    k: v
                    for k, v in a.content.items()
                    if k in ("summary", "overall_assessment", "key_genes")
                }

        system_prompt = (
            "You are a drug repurposing assessment specialist. Evaluate the overall "
            "confidence in this hypothesis by analyzing the strength and coherence "
            "of evidence across multiple dimensions. Compare with known successful "
            "drug repurposing cases.\n\n"
            "Respond ONLY with a JSON object containing:\n"
            '- "overall_confidence": A number 0-100\n'
            '- "confidence_level": One of "very_high", "high", "moderate", "low", "very_low"\n'
            '- "summary": 2-3 sentence confidence summary\n'
            '- "dimension_assessments": Object with keys for each scoring dimension '
            "(pathway_overlap, expression_correlation, literature_support, "
            "clinical_evidence, safety, novelty), each containing "
            '"score_interpretation" and "confidence_contribution"\n'
            '- "precedent_analysis": Object with "similar_successes" (array of known '
            'repurposed drugs with similar profiles), "similar_failures" (array), '
            '"key_differences"\n'
            '- "probability_of_success": Object with "preclinical" (0-1), '
            '"phase1" (0-1), "phase2" (0-1), "overall" (0-1)\n'
            '- "strength_of_evidence": Object with "strongest_dimension", '
            '"weakest_dimension", "most_novel_aspect"\n'
            '- "recommendation": One of "proceed_immediately", "proceed_with_caution", '
            '"additional_data_needed", "deprioritize"\n'
        )

        user_prompt = (
            f"Assess confidence for: {data['drug']['name']} repurposed for "
            f"{data['cancer']['name']}\n\n"
            f"Scores: {json.dumps(data['scores'], indent=2)}\n\n"
            f"Drug: {json.dumps(data['drug'], indent=2)}\n\n"
            f"Targets: {json.dumps(data['targets'], indent=2)}\n\n"
            f"Evidence: {json.dumps(data['evidence'][:15], indent=2)}\n\n"
            f"Papers: {json.dumps(data['papers'][:5], indent=2)}\n\n"
            f"Trials: {json.dumps(data['trials'][:5], indent=2)}\n\n"
        )
        if prior_context:
            user_prompt += (
                f"Prior analyses:\n{json.dumps(prior_context, indent=2)}\n"
            )

        result = await self._call_claude(system_prompt, user_prompt, model)
        await self._store_analysis(hypothesis_id, "confidence", result, session)

        # === FEEDBACK LOOP: write confidence back into the hypothesis ===
        content = result["content"]
        if isinstance(content, dict):
            await self._apply_confidence_to_hypothesis(
                hypothesis, content, session
            )

        return {
            "hypothesis_id": hypothesis_id,
            "analysis_type": "confidence",
            "model": result["model"],
            "content": result["content"],
            "tokens": {
                "input": result.get("input_tokens"),
                "output": result.get("output_tokens"),
            },
            "generation_time": result.get("generation_time"),
        }

    async def _apply_confidence_to_hypothesis(
        self,
        hypothesis: "Hypothesis",
        confidence_content: dict,
        session: AsyncSession,
    ) -> None:
        """Extract LLM confidence and apply it to the hypothesis scoring.

        This is THE feedback loop: the LLM's biological reasoning now flows
        back into the ranking that determines which hypotheses scientists see.
        """
        from app.services.scoring_config import ScoringConfig

        # Extract overall_confidence (0-100)
        llm_confidence = confidence_content.get("overall_confidence")
        if llm_confidence is not None:
            try:
                llm_confidence = float(llm_confidence)
                llm_confidence = min(max(llm_confidence, 0), 100)
            except (TypeError, ValueError):
                llm_confidence = None

        # Extract recommendation
        recommendation = confidence_content.get("recommendation")
        valid_recommendations = {
            "proceed_immediately", "proceed_with_caution",
            "additional_data_needed", "deprioritize",
        }
        if recommendation not in valid_recommendations:
            recommendation = None

        # Write to hypothesis
        hypothesis.llm_confidence_score = llm_confidence
        hypothesis.llm_recommendation = recommendation

        # Compute adjusted score: composite * confidence gate
        config = ScoringConfig()
        adjusted = config.compute_adjusted_score(
            hypothesis.composite_score, llm_confidence
        )
        hypothesis.adjusted_score = adjusted

        # Update evidence strength based on adjusted score (the real ranking)
        hypothesis.evidence_strength = config.determine_evidence_strength(adjusted)

        await session.flush()

        logger.info(
            "Hypothesis %d: composite=%.1f, llm_confidence=%.1f, "
            "adjusted=%.1f, recommendation=%s",
            hypothesis.id,
            hypothesis.composite_score,
            llm_confidence or 0,
            adjusted,
            recommendation,
        )

    # ------------------------------------------------------------------
    # Batch / orchestration methods
    # ------------------------------------------------------------------

    async def generate_full_analysis(
        self, hypothesis_id: int, session: AsyncSession
    ) -> dict:
        """Generate all 6 analysis types for a single hypothesis.

        Runs narrative and critique first (needed for confidence context),
        then the remaining 4 in parallel.
        """
        # Phase 1: narrative + critique (needed for confidence context)
        narrative_result = await self.generate_narrative(hypothesis_id, session)
        critique_result = await self.generate_critique(hypothesis_id, session)

        # Phase 2: remaining analyses in parallel
        comparative_result, lit_result, experiment_result, confidence_result = (
            await asyncio.gather(
                self.generate_comparative_analysis(hypothesis_id, session),
                self.synthesize_literature(hypothesis_id, session),
                self.design_experiments(hypothesis_id, session),
                self.assess_confidence(hypothesis_id, session),
            )
        )

        return {
            "hypothesis_id": hypothesis_id,
            "analyses": {
                "narrative": narrative_result,
                "critique": critique_result,
                "comparative": comparative_result,
                "literature_synthesis": lit_result,
                "experiment_design": experiment_result,
                "confidence": confidence_result,
            },
        }

    async def assess_confidence_batch(
        self,
        session: AsyncSession,
        min_score: float = 0.0,
        limit: int = 200,
    ) -> dict:
        """Run ONLY the confidence assessment for hypotheses — the cheapest
        way to get the feedback loop working.

        In economy mode with Haiku, this costs ~$0.01 per hypothesis vs
        ~$0.42 for a full 6-analysis run with Opus/Sonnet.

        Skips hypotheses that already have an llm_confidence_score.
        """
        result = await session.execute(
            select(Hypothesis.id)
            .where(
                Hypothesis.composite_score >= min_score,
                Hypothesis.llm_confidence_score.is_(None),
            )
            .order_by(Hypothesis.composite_score.desc())
            .limit(limit)
        )
        hypothesis_ids = [r[0] for r in result.all()]

        logger.info(
            "Running confidence-only assessment for %d hypotheses "
            "(cost_mode=%s, min_score=%.1f)",
            len(hypothesis_ids),
            self._cost_mode,
            min_score,
        )

        results = {"total": len(hypothesis_ids), "completed": 0, "errors": [], "cost_mode": self._cost_mode}
        for hyp_id in hypothesis_ids:
            try:
                await self.assess_confidence(hyp_id, session)
                results["completed"] += 1
                if results["completed"] % 10 == 0:
                    await session.commit()
                    logger.info(
                        "Confidence assessed: %d/%d", results["completed"], len(hypothesis_ids)
                    )
            except Exception as e:
                logger.error("Confidence assessment failed for hypothesis %d: %s", hyp_id, e)
                results["errors"].append({"hypothesis_id": hyp_id, "error": str(e)})

        await session.commit()
        return results

    async def generate_narratives_batch(
        self,
        session: AsyncSession,
        min_score: float = 0.0,
        limit: int = 100,
    ) -> dict:
        """Generate narratives for top hypotheses that don't have one yet."""
        # Find hypotheses without narratives
        existing_narrative_ids = (
            select(HypothesisAnalysis.hypothesis_id)
            .where(HypothesisAnalysis.analysis_type == "narrative")
        )
        result = await session.execute(
            select(Hypothesis.id)
            .where(
                Hypothesis.composite_score >= min_score,
                ~Hypothesis.id.in_(existing_narrative_ids),
            )
            .order_by(Hypothesis.composite_score.desc())
            .limit(limit)
        )
        hypothesis_ids = [r[0] for r in result.all()]

        logger.info(
            "Generating narratives for %d hypotheses (min_score=%.1f)",
            len(hypothesis_ids),
            min_score,
        )

        results = {"total": len(hypothesis_ids), "completed": 0, "errors": []}
        for hyp_id in hypothesis_ids:
            try:
                await self.generate_narrative(hyp_id, session)
                results["completed"] += 1
                # Commit every 5 to avoid long transactions
                if results["completed"] % 5 == 0:
                    await session.commit()
            except Exception as e:
                logger.error("Narrative generation failed for hypothesis %d: %s", hyp_id, e)
                results["errors"].append({"hypothesis_id": hyp_id, "error": str(e)})

        await session.commit()
        return results

    async def generate_full_analysis_batch(
        self,
        session: AsyncSession,
        min_score: float = 50.0,
        limit: int = 20,
    ) -> dict:
        """Generate full analyses for top hypotheses."""
        result = await session.execute(
            select(Hypothesis.id)
            .where(Hypothesis.composite_score >= min_score)
            .order_by(Hypothesis.composite_score.desc())
            .limit(limit)
        )
        hypothesis_ids = [r[0] for r in result.all()]

        logger.info(
            "Generating full analyses for %d hypotheses (min_score=%.1f)",
            len(hypothesis_ids),
            min_score,
        )

        results = {"total": len(hypothesis_ids), "completed": 0, "errors": []}
        for hyp_id in hypothesis_ids:
            try:
                await self.generate_full_analysis(hyp_id, session)
                results["completed"] += 1
                await session.commit()
            except Exception as e:
                logger.error(
                    "Full analysis failed for hypothesis %d: %s", hyp_id, e
                )
                results["errors"].append({"hypothesis_id": hyp_id, "error": str(e)})

        await session.commit()
        return results

    async def generate_comparative_analyses(
        self,
        session: AsyncSession,
        cancer_type_id: int | None = None,
        min_score: float = 30.0,
    ) -> dict:
        """Generate comparative analyses for hypotheses grouped by cancer type."""
        query = select(Hypothesis.cancer_type_id).distinct()
        if cancer_type_id:
            query = query.where(Hypothesis.cancer_type_id == cancer_type_id)
        query = query.where(Hypothesis.composite_score >= min_score)

        ct_result = await session.execute(query)
        cancer_type_ids = [r[0] for r in ct_result.all()]

        results = {"cancer_types_processed": 0, "total_analyses": 0, "errors": []}

        for ct_id in cancer_type_ids:
            # Get hypotheses for this cancer type
            hyp_result = await session.execute(
                select(Hypothesis.id)
                .where(
                    Hypothesis.cancer_type_id == ct_id,
                    Hypothesis.composite_score >= min_score,
                )
                .order_by(Hypothesis.composite_score.desc())
                .limit(10)
            )
            hyp_ids = [r[0] for r in hyp_result.all()]

            for hyp_id in hyp_ids:
                try:
                    await self.generate_comparative_analysis(hyp_id, session)
                    results["total_analyses"] += 1
                except Exception as e:
                    logger.error(
                        "Comparative analysis failed for hypothesis %d: %s",
                        hyp_id,
                        e,
                    )
                    results["errors"].append(
                        {"hypothesis_id": hyp_id, "error": str(e)}
                    )

            results["cancer_types_processed"] += 1
            await session.commit()

        return results

    # ------------------------------------------------------------------
    # Usage statistics
    # ------------------------------------------------------------------

    async def get_usage_stats(
        self, session: AsyncSession, days: int = 30
    ) -> dict:
        """Get LLM usage statistics for the specified period."""
        from datetime import datetime, timedelta

        cutoff = datetime.utcnow() - timedelta(days=days)

        # Total calls and cost
        totals_result = await session.execute(
            select(
                func.count(LLMUsageLog.id),
                func.sum(LLMUsageLog.input_tokens),
                func.sum(LLMUsageLog.output_tokens),
                func.sum(LLMUsageLog.estimated_cost_usd),
            ).where(LLMUsageLog.created_at >= cutoff)
        )
        row = totals_result.one()
        total_calls = row[0] or 0
        total_input_tokens = row[1] or 0
        total_output_tokens = row[2] or 0
        total_cost = row[3] or 0.0

        # By model
        model_result = await session.execute(
            select(
                LLMUsageLog.model,
                func.count(LLMUsageLog.id),
                func.sum(LLMUsageLog.input_tokens),
                func.sum(LLMUsageLog.output_tokens),
                func.sum(LLMUsageLog.estimated_cost_usd),
            )
            .where(LLMUsageLog.created_at >= cutoff)
            .group_by(LLMUsageLog.model)
        )
        by_model = {
            row[0]: {
                "calls": row[1],
                "input_tokens": row[2] or 0,
                "output_tokens": row[3] or 0,
                "cost_usd": round(row[4] or 0.0, 4),
            }
            for row in model_result.all()
        }

        # By analysis type
        type_result = await session.execute(
            select(
                LLMUsageLog.analysis_type,
                func.count(LLMUsageLog.id),
                func.sum(LLMUsageLog.estimated_cost_usd),
            )
            .where(LLMUsageLog.created_at >= cutoff)
            .group_by(LLMUsageLog.analysis_type)
        )
        by_type = {
            row[0]: {"calls": row[1], "cost_usd": round(row[2] or 0.0, 4)}
            for row in type_result.all()
        }

        # Error count
        error_result = await session.execute(
            select(func.count(LLMUsageLog.id)).where(
                LLMUsageLog.created_at >= cutoff,
                LLMUsageLog.error.isnot(None),
            )
        )
        error_count = error_result.scalar() or 0

        return {
            "period_days": days,
            "total_calls": total_calls,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_cost_usd": round(total_cost, 4),
            "error_count": error_count,
            "by_model": by_model,
            "by_analysis_type": by_type,
        }
