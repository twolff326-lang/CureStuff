"""Strategy 7: LLM Literature Synthesis Discovery.

The NOVEL part of Pharma Nexus. Instead of matching structured data,
this service feeds Claude batches of papers about a cancer type along
with drug mechanism data, and asks it to REASON about implicit
connections — transitive inferences, mechanistic analogies, and
cross-domain insights that no database captures.

Example of what this finds that Strategies 1-6 miss:
  Paper A: "Metformin activates AMPK, suppressing mTOR"
  Paper B: "mTOR hyperactivation drives resistance in KRAS-mutant CRC"
  Paper C: "AMPK activation restores targeted therapy sensitivity in CRC"
  → Inference: Metformin may overcome therapy resistance in KRAS-mutant CRC

Pipeline:
  1. Gather papers about a cancer type (abstracts + extracted findings)
  2. Gather drug mechanisms, targets, and pathways
  3. Feed batches to Claude with a synthesis prompt
  4. Parse proposed drug-cancer pairs with reasoning chains
  5. Store proposals in llm_discovery_proposals
  6. Quality-filter and inject into hypothesis engine
"""

import asyncio
import json
import logging
import uuid
from typing import Any

import anthropic
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.cancer_type import CancerType, CancerMolecularProfile
from app.models.drug import Drug, DrugTarget, LiteratureDrug
from app.models.discovery import LLMDiscoveryProposal
from app.models.hypothesis import Hypothesis
from app.models.literature import Literature, LiteratureCancer
from app.models.mutation import Mutation
from app.models.target import Target
from app.services.llm_analyst import (
    COST_MODE_MODELS,
    PRICING,
    RateLimiter,
)

logger = logging.getLogger(__name__)

# How many papers to include per cancer-type batch
PAPERS_PER_BATCH = 30
# How many drugs to include as mechanism context
DRUGS_PER_BATCH = 40
# Minimum LLM self-assessed confidence to accept a proposal
MIN_PROPOSAL_CONFIDENCE = 0.4


class SynthesisDiscovery:
    """LLM-powered discovery of novel drug-cancer connections.

    Reads literature and drug mechanisms, then asks Claude to propose
    connections that structured databases miss.
    """

    def __init__(self, cost_mode: str | None = None) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._cost_mode = cost_mode or settings.llm_cost_mode
        self._rate_limiter = RateLimiter(max_per_minute=30)
        self._sem = asyncio.Semaphore(3)

    def _model(self) -> str:
        models = COST_MODE_MODELS.get(self._cost_mode, COST_MODE_MODELS["standard"])
        return models["top"]  # Use best model available — this is the creative step

    # ------------------------------------------------------------------
    # Data assembly
    # ------------------------------------------------------------------

    async def _get_cancer_papers(
        self,
        cancer_type_id: int,
        session: AsyncSession,
        limit: int = PAPERS_PER_BATCH,
    ) -> list[dict]:
        """Get the most informative papers about a cancer type."""
        result = await session.execute(
            select(Literature)
            .join(LiteratureCancer, LiteratureCancer.literature_id == Literature.id)
            .where(
                LiteratureCancer.cancer_type_id == cancer_type_id,
                Literature.abstract.isnot(None),
            )
            .order_by(Literature.pub_date.desc().nulls_last())
            .limit(limit)
        )
        papers = result.scalars().all()
        return [
            {
                "pmid": p.pmid,
                "title": p.title,
                "abstract": (p.abstract or "")[:600],
                "journal": p.journal,
                "year": str(p.pub_date.year) if p.pub_date else None,
                "findings": p.extracted_findings,
            }
            for p in papers
        ]

    async def _get_cancer_context(
        self,
        cancer_type_id: int,
        session: AsyncSession,
    ) -> dict:
        """Get cancer type context: mutations, expression, pathways."""
        cancer_result = await session.execute(
            select(CancerType).where(CancerType.id == cancer_type_id)
        )
        cancer = cancer_result.scalar_one()

        # Top mutations
        mut_result = await session.execute(
            select(Mutation.gene_symbol, Mutation.mutation_type, Mutation.frequency_percent)
            .where(Mutation.cancer_type_id == cancer_type_id)
            .order_by(Mutation.frequency_percent.desc().nulls_last())
            .limit(20)
        )
        mutations = [
            {"gene": r[0], "type": r[1], "frequency": r[2]}
            for r in mut_result.all()
        ]

        # Top dysregulated genes
        expr_result = await session.execute(
            select(
                CancerMolecularProfile.gene_symbol,
                CancerMolecularProfile.alteration_type,
                CancerMolecularProfile.expression_zscore,
            )
            .where(CancerMolecularProfile.cancer_type_id == cancer_type_id)
            .order_by(
                func.abs(CancerMolecularProfile.expression_zscore).desc().nulls_last()
            )
            .limit(20)
        )
        expression = [
            {"gene": r[0], "alteration": r[1], "zscore": r[2]}
            for r in expr_result.all()
        ]

        return {
            "name": cancer.name,
            "tcga_code": cancer.tcga_code,
            "tissue": cancer.tissue,
            "organ": cancer.organ,
            "top_mutations": mutations,
            "top_expression_changes": expression,
        }

    async def _get_drug_mechanisms(
        self,
        session: AsyncSession,
        exclude_drug_ids: set[int] | None = None,
        limit: int = DRUGS_PER_BATCH,
    ) -> list[dict]:
        """Get drug mechanisms for drugs with known targets.

        Excludes drugs that already have hypotheses for the target cancer.
        """
        query = (
            select(Drug)
            .where(
                Drug.mechanism_of_action.isnot(None),
                Drug.status.in_(["approved", "investigational"]),
            )
            .limit(limit)
        )
        if exclude_drug_ids:
            query = query.where(Drug.id.notin_(exclude_drug_ids))

        result = await session.execute(query)
        drugs = result.scalars().all()

        drug_data = []
        for d in drugs:
            # Get targets
            target_result = await session.execute(
                select(Target.gene_symbol, DrugTarget.action_type)
                .join(DrugTarget, DrugTarget.target_id == Target.id)
                .where(DrugTarget.drug_id == d.id)
                .limit(5)
            )
            targets = [
                {"gene": r[0], "action": r[1]} for r in target_result.all()
            ]

            drug_data.append({
                "drug_id": d.id,
                "name": d.name,
                "drugbank_id": d.drugbank_id,
                "mechanism": (d.mechanism_of_action or "")[:300],
                "indication": (d.indication or "")[:200],
                "status": d.status,
                "targets": targets,
            })

        return drug_data

    async def _get_existing_hypothesis_drug_ids(
        self,
        cancer_type_id: int,
        session: AsyncSession,
    ) -> set[int]:
        """Get drug IDs that already have hypotheses for this cancer."""
        result = await session.execute(
            select(Hypothesis.drug_id).where(
                Hypothesis.cancer_type_id == cancer_type_id
            )
        )
        return set(r[0] for r in result.all())

    # ------------------------------------------------------------------
    # LLM synthesis call
    # ------------------------------------------------------------------

    async def _call_synthesis(
        self,
        cancer_context: dict,
        papers: list[dict],
        drugs: list[dict],
    ) -> dict[str, Any]:
        """Ask Claude to reason about implicit drug-cancer connections."""
        model = self._model()

        system_prompt = (
            "You are a world-class drug repurposing scientist. Your unique "
            "ability is reading scientific literature and reasoning about "
            "IMPLICIT connections between drugs and cancers that are NOT "
            "stated explicitly in any single paper.\n\n"
            "You are looking for TRANSITIVE INFERENCES — connections across "
            "papers that no one has put together yet:\n"
            "  - Paper A says Drug X affects Pathway P\n"
            "  - Paper B says Pathway P is critical in Cancer Y\n"
            "  - NO paper mentions Drug X + Cancer Y together\n"
            "  → YOU propose: Drug X may treat Cancer Y, via Pathway P\n\n"
            "You will be given:\n"
            "  1. Context about a cancer type (mutations, expression changes)\n"
            "  2. Recent papers about this cancer\n"
            "  3. A list of drugs with their mechanisms and targets\n\n"
            "Your task: propose drug-cancer pairs that the papers IMPLY but "
            "do NOT state directly. Focus on:\n"
            "  - Mechanistic logic (drug target → pathway → cancer driver)\n"
            "  - Off-target effects that could be therapeutic\n"
            "  - Resistance mechanism reversal\n"
            "  - Immune modulation relevance\n"
            "  - Synthetic lethality opportunities\n"
            "  - Metabolic vulnerability exploitation\n\n"
            "CRITICAL: Only propose connections with genuine mechanistic "
            "rationale. Do NOT hallucinate paper findings. If a paper doesn't "
            "support a claim, don't cite it.\n\n"
            "Respond ONLY with a JSON object containing:\n"
            '- "proposals": Array of objects, each with:\n'
            '    - "drug_name": Name of the drug\n'
            '    - "drug_id": The drug_id from the input\n'
            '    - "mechanism_rationale": 2-4 sentence explanation of the '
            "mechanistic logic connecting this drug to the cancer\n"
            '    - "transitive_chain": Array of strings describing the '
            'reasoning chain (e.g. ["Drug X inhibits Target A", '
            '"Target A activates Pathway P", '
            '"Pathway P drives Cancer Y progression"])\n'
            '    - "key_papers": Array of PMIDs from the input papers that '
            "support parts of the reasoning (only cite papers you were given)\n"
            '    - "inferred_pathways": Array of pathway names involved\n'
            '    - "confidence": Float 0-1 (how confident are you in this '
            "connection? Be calibrated — 0.8+ means very strong logic)\n"
            '    - "novelty_reasoning": Why this connection is not obvious\n'
            '    - "category": One of "mechanistic", "resistance_reversal", '
            '"immune_modulation", "synthetic_lethality", "metabolic", "off_target"\n'
            "\n"
            "Propose 3-10 connections. Quality over quantity."
        )

        user_prompt = (
            f"## Cancer Type\n"
            f"{json.dumps(cancer_context, indent=2)}\n\n"
            f"## Recent Papers About This Cancer\n"
            f"{json.dumps(papers, indent=2)}\n\n"
            f"## Available Drugs (mechanisms and targets)\n"
            f"{json.dumps(drugs, indent=2)}\n\n"
            f"Propose novel drug repurposing connections for "
            f"{cancer_context['name']} based on implicit reasoning "
            f"across these papers and drug mechanisms."
        )

        await self._rate_limiter.acquire()
        async with self._sem:
            response = await self._client.messages.create(
                model=model,
                max_tokens=4096,
                temperature=0.3,  # Some creativity, but grounded
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )

        raw_text = response.content[0].text
        # Parse JSON from response
        parsed = self._parse_json(raw_text)

        return {
            "content": parsed,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "model": model,
        }

    @staticmethod
    def _parse_json(text: str) -> dict | list | None:
        """Parse JSON from Claude's response."""
        import re

        cleaned = re.sub(r"```(?:json)?\s*\n?", "", text).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        for pattern in [r"\{[\s\S]*\}", r"\[[\s\S]*\]"]:
            match = re.search(pattern, text)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    continue
        return None

    # ------------------------------------------------------------------
    # Store proposals
    # ------------------------------------------------------------------

    async def _store_proposals(
        self,
        proposals: list[dict],
        cancer_type_id: int,
        batch_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        drug_lookup: dict[str, int],
        session: AsyncSession,
    ) -> list[LLMDiscoveryProposal]:
        """Store valid proposals in the database."""
        stored = []
        for p in proposals:
            # Resolve drug_id
            drug_id = p.get("drug_id")
            if drug_id is None:
                drug_name = p.get("drug_name", "")
                drug_id = drug_lookup.get(drug_name.lower())
            if drug_id is None:
                continue

            confidence = p.get("confidence", 0)
            try:
                confidence = float(confidence)
            except (TypeError, ValueError):
                confidence = 0

            if confidence < MIN_PROPOSAL_CONFIDENCE:
                continue

            # Check for duplicate (same drug+cancer in this batch)
            existing = await session.execute(
                select(LLMDiscoveryProposal.id).where(
                    LLMDiscoveryProposal.drug_id == drug_id,
                    LLMDiscoveryProposal.cancer_type_id == cancer_type_id,
                    LLMDiscoveryProposal.batch_id == batch_id,
                )
            )
            if existing.scalar_one_or_none():
                continue

            proposal = LLMDiscoveryProposal(
                drug_id=drug_id,
                cancer_type_id=cancer_type_id,
                mechanism_rationale=p.get("mechanism_rationale", ""),
                key_papers=p.get("key_papers", []),
                inferred_pathways=p.get("inferred_pathways", []),
                transitive_chain=p.get("transitive_chain", []),
                confidence=min(max(confidence, 0), 1),
                novelty_reasoning=p.get("novelty_reasoning"),
                status="proposed",
                model_used=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                batch_id=batch_id,
            )
            session.add(proposal)
            stored.append(proposal)

        if stored:
            await session.flush()
        return stored

    # ------------------------------------------------------------------
    # Main entry points
    # ------------------------------------------------------------------

    async def discover_for_cancer(
        self,
        cancer_type_id: int,
        session: AsyncSession,
    ) -> dict[str, Any]:
        """Run LLM synthesis discovery for a single cancer type.

        Returns summary of proposals generated.
        """
        batch_id = str(uuid.uuid4())[:8]

        # Gather context
        cancer_context = await self._get_cancer_context(cancer_type_id, session)
        papers = await self._get_cancer_papers(cancer_type_id, session)

        if not papers:
            return {
                "cancer_type": cancer_context["name"],
                "proposals": 0,
                "error": "No papers found for this cancer type",
            }

        # Get drugs not already hypothesized for this cancer
        existing_drug_ids = await self._get_existing_hypothesis_drug_ids(
            cancer_type_id, session
        )
        drugs = await self._get_drug_mechanisms(
            session, exclude_drug_ids=existing_drug_ids
        )

        if not drugs:
            return {
                "cancer_type": cancer_context["name"],
                "proposals": 0,
                "error": "No unhypothesized drugs available",
            }

        # Build drug name → id lookup
        drug_lookup = {d["name"].lower(): d["drug_id"] for d in drugs}

        logger.info(
            "Running LLM synthesis discovery for %s: %d papers, %d drugs "
            "(batch=%s, model=%s)",
            cancer_context["name"],
            len(papers),
            len(drugs),
            batch_id,
            self._model(),
        )

        # Call Claude
        result = await self._call_synthesis(cancer_context, papers, drugs)
        content = result["content"]

        if not isinstance(content, dict) or "proposals" not in content:
            logger.warning(
                "LLM synthesis returned invalid structure for %s",
                cancer_context["name"],
            )
            return {
                "cancer_type": cancer_context["name"],
                "proposals": 0,
                "error": "Invalid LLM response structure",
            }

        # Store proposals
        raw_proposals = content["proposals"]
        stored = await self._store_proposals(
            raw_proposals,
            cancer_type_id,
            batch_id,
            result["model"],
            result.get("input_tokens", 0),
            result.get("output_tokens", 0),
            drug_lookup,
            session,
        )

        # Log usage
        from app.models.llm_analysis import LLMUsageLog

        prices = PRICING.get(result["model"], PRICING[list(PRICING.keys())[0]])
        cost = (
            result.get("input_tokens", 0) * prices["input"]
            + result.get("output_tokens", 0) * prices["output"]
        ) / 1_000_000

        usage_log = LLMUsageLog(
            hypothesis_id=None,
            analysis_type="synthesis_discovery",
            model=result["model"],
            input_tokens=result.get("input_tokens", 0),
            output_tokens=result.get("output_tokens", 0),
            estimated_cost_usd=cost,
        )
        session.add(usage_log)
        await session.flush()

        logger.info(
            "LLM synthesis for %s: %d proposals from %d raw (batch=%s, cost=$%.4f)",
            cancer_context["name"],
            len(stored),
            len(raw_proposals),
            batch_id,
            cost,
        )

        return {
            "cancer_type": cancer_context["name"],
            "cancer_type_id": cancer_type_id,
            "batch_id": batch_id,
            "proposals": len(stored),
            "raw_proposals": len(raw_proposals),
            "filtered_out": len(raw_proposals) - len(stored),
            "model": result["model"],
            "cost_usd": round(cost, 4),
            "top_proposals": [
                {
                    "drug_id": p.drug_id,
                    "confidence": p.confidence,
                    "rationale": p.mechanism_rationale[:200],
                }
                for p in sorted(stored, key=lambda x: x.confidence, reverse=True)[:5]
            ],
        }

    async def discover_batch(
        self,
        session: AsyncSession,
        cancer_type_ids: list[int] | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Run synthesis discovery across multiple cancer types.

        If cancer_type_ids is None, selects cancer types with the most
        papers to maximize discovery potential.
        """
        if cancer_type_ids is None:
            # Pick cancer types with the most literature
            ct_result = await session.execute(
                select(
                    LiteratureCancer.cancer_type_id,
                    func.count(LiteratureCancer.literature_id),
                )
                .group_by(LiteratureCancer.cancer_type_id)
                .order_by(func.count(LiteratureCancer.literature_id).desc())
                .limit(limit)
            )
            cancer_type_ids = [r[0] for r in ct_result.all()]

        results = {
            "cancer_types_processed": 0,
            "total_proposals": 0,
            "total_cost_usd": 0.0,
            "by_cancer_type": [],
            "errors": [],
        }

        for ct_id in cancer_type_ids:
            try:
                r = await self.discover_for_cancer(ct_id, session)
                results["cancer_types_processed"] += 1
                results["total_proposals"] += r.get("proposals", 0)
                results["total_cost_usd"] += r.get("cost_usd", 0)
                results["by_cancer_type"].append(r)
                await session.commit()
            except Exception as e:
                logger.error(
                    "Synthesis discovery failed for cancer_type_id=%d: %s",
                    ct_id, e,
                )
                results["errors"].append(
                    {"cancer_type_id": ct_id, "error": str(e)}
                )

        results["total_cost_usd"] = round(results["total_cost_usd"], 4)
        return results

    async def promote_proposals(
        self,
        session: AsyncSession,
        min_confidence: float = 0.5,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Promote accepted proposals into the hypothesis engine.

        Creates hypothesis candidates from LLM proposals, then runs
        them through the standard scoring pipeline.
        """
        # Get proposals that haven't been promoted yet
        result = await session.execute(
            select(LLMDiscoveryProposal)
            .where(
                LLMDiscoveryProposal.status == "proposed",
                LLMDiscoveryProposal.confidence >= min_confidence,
            )
            .order_by(LLMDiscoveryProposal.confidence.desc())
            .limit(limit)
        )
        proposals = result.scalars().all()

        if not proposals:
            return {"promoted": 0, "message": "No proposals to promote"}

        from app.services.hypothesis_engine import HypothesisEngine
        from app.services.scoring_config import ScoringConfig

        engine = HypothesisEngine()
        config = ScoringConfig()
        weights = await config.get_active_weights(session)

        promoted = 0
        errors = []

        for proposal in proposals:
            try:
                # Score through the standard pipeline
                hyp_result = await engine._score_and_store_hypothesis(
                    drug_id=proposal.drug_id,
                    cancer_type_id=proposal.cancer_type_id,
                    strategies=["llm_synthesis"],
                    weights=weights,
                    db=session,
                )

                if hyp_result:
                    proposal.status = "hypothesis_created"
                    proposal.hypothesis_id = hyp_result["hypothesis_id"]
                    promoted += 1
                else:
                    proposal.status = "rejected"
            except Exception as e:
                logger.warning(
                    "Failed to promote proposal %d: %s", proposal.id, e
                )
                errors.append({"proposal_id": proposal.id, "error": str(e)})

        await session.commit()

        return {
            "promoted": promoted,
            "total_proposals": len(proposals),
            "errors": errors,
        }

    async def get_proposals(
        self,
        session: AsyncSession,
        status: str | None = None,
        cancer_type_id: int | None = None,
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> list[dict]:
        """Get discovery proposals with optional filters."""
        query = select(LLMDiscoveryProposal).where(
            LLMDiscoveryProposal.confidence >= min_confidence
        )
        if status:
            query = query.where(LLMDiscoveryProposal.status == status)
        if cancer_type_id:
            query = query.where(
                LLMDiscoveryProposal.cancer_type_id == cancer_type_id
            )
        query = query.order_by(
            LLMDiscoveryProposal.confidence.desc()
        ).limit(limit)

        result = await session.execute(query)
        proposals = result.scalars().all()

        # Get drug and cancer names
        out = []
        for p in proposals:
            drug_result = await session.execute(
                select(Drug.name).where(Drug.id == p.drug_id)
            )
            drug_name = drug_result.scalar_one_or_none() or f"Drug#{p.drug_id}"

            cancer_result = await session.execute(
                select(CancerType.name).where(CancerType.id == p.cancer_type_id)
            )
            cancer_name = cancer_result.scalar_one_or_none() or f"Cancer#{p.cancer_type_id}"

            out.append({
                "id": p.id,
                "drug_id": p.drug_id,
                "drug_name": drug_name,
                "cancer_type_id": p.cancer_type_id,
                "cancer_name": cancer_name,
                "mechanism_rationale": p.mechanism_rationale,
                "transitive_chain": p.transitive_chain,
                "key_papers": p.key_papers,
                "inferred_pathways": p.inferred_pathways,
                "confidence": p.confidence,
                "novelty_reasoning": p.novelty_reasoning,
                "status": p.status,
                "hypothesis_id": p.hypothesis_id,
                "model_used": p.model_used,
                "batch_id": p.batch_id,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            })
        return out
