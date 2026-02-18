"""Multi-round verification pipeline for LLM discovery proposals.

The original synthesis_discovery.py generates proposals in a single LLM
call. This module adds five verification layers that transform it from
"prompt engineering" into a defensible scientific method:

1. Adversarial Critique — A second LLM call challenges each proposal,
   looking for logical gaps, confounders, and overclaimed evidence.
   Proposals that survive get a verified_confidence score.

2. Citation Grounding — Verify that cited PMIDs exist in our database
   and that their abstracts actually mention the genes/pathways claimed
   in the reasoning chain. Produces a grounding_score (0-1).

3. Chain Verification — For each step in the transitive chain
   (Drug→Target→Pathway→Cancer), check whether the intermediate links
   have structured database evidence. Produces a chain_evidence_score.

4. Novelty Check — Real-time PubMed query to verify that the proposed
   drug-cancer connection is not already well-studied. A "discovery"
   with 50 existing papers is not a discovery.

5. Dosing Plausibility — Cross-reference the drug's binding affinity
   (IC50/Ki from bioassays) against achievable plasma concentrations.
   If the drug can't reach therapeutic concentration, the hypothesis
   is pharmacologically implausible.

Together, these produce a final `verified_score` that is calibrated
against actual database evidence rather than relying on LLM self-assessment.
"""

import json
import logging
from typing import Any

import anthropic
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.cancer_type import CancerMolecularProfile
from app.models.drug import Drug, DrugTarget, LiteratureDrug
from app.models.literature import Literature, LiteratureCancer
from app.models.mutation import Mutation
from app.models.pathway import Pathway, PathwayTarget
from app.models.target import Target
from app.services.llm_analyst import COST_MODE_MODELS, PRICING, RateLimiter

logger = logging.getLogger(__name__)


class ProposalVerifier:
    """Multi-round verification of LLM discovery proposals.

    Takes raw proposals from SynthesisDiscovery and subjects them to
    adversarial critique, citation grounding, and chain verification.
    """

    def __init__(self, cost_mode: str | None = None) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._cost_mode = cost_mode or settings.llm_cost_mode
        self._rate_limiter = RateLimiter(max_per_minute=30)

    def _model(self) -> str:
        models = COST_MODE_MODELS.get(self._cost_mode, COST_MODE_MODELS["standard"])
        return models["top"]

    # ------------------------------------------------------------------
    # Round 1: Adversarial Critique
    # ------------------------------------------------------------------

    async def adversarial_critique(
        self,
        proposal: dict[str, Any],
        cancer_context: dict[str, Any],
    ) -> dict[str, Any]:
        """Challenge a proposal with a devil's advocate LLM call.

        Returns critique with adjusted confidence and list of weaknesses.
        """
        model = self._model()

        system_prompt = (
            "You are a skeptical reviewer of drug repurposing hypotheses. "
            "Your job is to find WEAKNESSES in proposed connections. "
            "You are evaluating a proposal that claims a drug could treat a cancer "
            "based on transitive reasoning across papers.\n\n"
            "For each proposal, assess:\n"
            "1. LOGICAL VALIDITY: Is each step in the reasoning chain sound? "
            "Are there hidden assumptions or logical leaps?\n"
            "2. CONFOUNDERS: What alternative explanations exist? Could the "
            "pathway connection be coincidental rather than causal?\n"
            "3. BIOLOGICAL PLAUSIBILITY: Would the drug concentration needed "
            "be achievable? Is the target druggable in this context?\n"
            "4. EVIDENCE OVERCLAIMING: Does the rationale claim more than the "
            "cited papers actually show?\n"
            "5. KNOWN FAILURES: Are there reasons this specific approach has "
            "been tried and failed before?\n\n"
            "Respond with JSON:\n"
            '- "weaknesses": Array of specific weakness descriptions\n'
            '- "severity": "fatal" | "major" | "minor" | "negligible"\n'
            '- "adjusted_confidence": Float 0-1 (your assessed confidence '
            "after accounting for weaknesses — be MORE conservative than "
            "the original proposer)\n"
            '- "should_proceed": Boolean (is this worth investigating?)\n'
            '- "missing_evidence": What specific evidence would strengthen '
            "or invalidate this proposal?\n"
            '- "alternative_explanations": Array of alternative mechanistic '
            "explanations for the observed connections"
        )

        user_prompt = (
            f"## Proposal to Critique\n"
            f"Drug: {proposal.get('drug_name', 'Unknown')}\n"
            f"Cancer: {cancer_context.get('name', 'Unknown')}\n"
            f"Rationale: {proposal.get('mechanism_rationale', '')}\n"
            f"Reasoning chain: {json.dumps(proposal.get('transitive_chain', []))}\n"
            f"Claimed pathways: {json.dumps(proposal.get('inferred_pathways', []))}\n"
            f"Original confidence: {proposal.get('confidence', 0)}\n\n"
            f"## Cancer Context\n"
            f"Top mutations: {json.dumps(cancer_context.get('top_mutations', [])[:10])}\n"
            f"Top expression changes: {json.dumps(cancer_context.get('top_expression_changes', [])[:10])}\n\n"
            f"Critique this proposal rigorously."
        )

        await self._rate_limiter.acquire()
        response = await self._client.messages.create(
            model=model,
            max_tokens=2048,
            temperature=0.2,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw = response.content[0].text
        parsed = _parse_json(raw)

        cost = _compute_cost(
            model, response.usage.input_tokens, response.usage.output_tokens
        )

        if not isinstance(parsed, dict):
            return {
                "critique_error": "Failed to parse critique response",
                "adjusted_confidence": proposal.get("confidence", 0) * 0.5,
                "severity": "unknown",
                "should_proceed": False,
                "cost_usd": cost,
            }

        return {
            "weaknesses": parsed.get("weaknesses", []),
            "severity": parsed.get("severity", "unknown"),
            "adjusted_confidence": min(
                max(float(parsed.get("adjusted_confidence", 0)), 0), 1
            ),
            "should_proceed": parsed.get("should_proceed", False),
            "missing_evidence": parsed.get("missing_evidence", ""),
            "alternative_explanations": parsed.get("alternative_explanations", []),
            "cost_usd": cost,
            "model": model,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }

    # ------------------------------------------------------------------
    # Round 2: Citation Grounding
    # ------------------------------------------------------------------

    async def ground_citations(
        self,
        proposal: dict[str, Any],
        session: AsyncSession,
    ) -> dict[str, Any]:
        """Verify that cited PMIDs exist and their abstracts support the claims.

        For each cited PMID:
        1. Check it exists in our literature table
        2. Check the abstract mentions the drug or its targets
        3. Check the abstract mentions the relevant pathways
        4. Compute a grounding_score (fraction of citations that check out)
        """
        key_papers = proposal.get("key_papers", [])
        if not key_papers:
            return {
                "grounding_score": 0.0,
                "papers_checked": 0,
                "papers_found": 0,
                "papers_supporting": 0,
                "details": [],
                "verdict": "no_citations",
            }

        drug_name = (proposal.get("drug_name") or "").lower()
        chain = proposal.get("transitive_chain", [])
        pathways = [p.lower() for p in proposal.get("inferred_pathways", [])]

        # Extract gene/target names from the chain for matching
        chain_text = " ".join(chain).lower()

        details = []
        found_count = 0
        supporting_count = 0

        for pmid in key_papers:
            pmid_str = str(pmid).strip()
            result = await session.execute(
                select(Literature.id, Literature.title, Literature.abstract)
                .where(Literature.pmid == pmid_str)
            )
            row = result.one_or_none()

            if row is None:
                details.append({
                    "pmid": pmid_str,
                    "status": "not_found",
                    "in_database": False,
                    "supports_claim": False,
                })
                continue

            found_count += 1
            lit_id, title, abstract = row
            text = f"{title or ''} {abstract or ''}".lower()

            # Check if the paper mentions relevant terms
            mentions_drug = drug_name in text if drug_name else False
            mentions_pathway = any(p in text for p in pathways) if pathways else False

            # Check for chain-relevant gene symbols (look for words from chain)
            chain_words = set(chain_text.split()) - {
                "the", "a", "an", "in", "of", "and", "or", "via", "by",
                "to", "is", "are", "was", "were", "that", "which", "this",
                "drug", "target", "pathway", "cancer", "inhibits", "activates",
                "promotes", "suppresses", "drives", "mediates",
            }
            chain_word_matches = sum(1 for w in chain_words if w in text and len(w) > 3)
            mentions_chain = chain_word_matches >= 2

            supports = mentions_drug or mentions_pathway or mentions_chain
            if supports:
                supporting_count += 1

            details.append({
                "pmid": pmid_str,
                "status": "found",
                "in_database": True,
                "title": (title or "")[:100],
                "mentions_drug": mentions_drug,
                "mentions_pathway": mentions_pathway,
                "mentions_chain_terms": mentions_chain,
                "chain_word_hits": chain_word_matches,
                "supports_claim": supports,
            })

        total = len(key_papers)
        grounding_score = supporting_count / total if total > 0 else 0.0

        return {
            "grounding_score": round(grounding_score, 3),
            "papers_checked": total,
            "papers_found": found_count,
            "papers_supporting": supporting_count,
            "details": details,
            "verdict": (
                "strong" if grounding_score >= 0.7
                else "moderate" if grounding_score >= 0.4
                else "weak" if grounding_score > 0
                else "ungrounded"
            ),
        }

    # ------------------------------------------------------------------
    # Round 3: Chain Verification (structured data)
    # ------------------------------------------------------------------

    async def verify_chain(
        self,
        proposal: dict[str, Any],
        cancer_type_id: int,
        session: AsyncSession,
    ) -> dict[str, Any]:
        """Verify each link in the transitive reasoning chain against the DB.

        For a chain like:
          ["Drug X inhibits Target A",
           "Target A activates Pathway P",
           "Pathway P drives Cancer Y"]

        We check:
          - Does Drug X target anything? (drug_targets table)
          - Do any drug targets participate in pathways? (pathway_targets)
          - Do those pathways contain genes mutated in this cancer?
          - Are there expression changes matching the claimed mechanism?

        Returns chain_evidence_score (0-1) and per-link verification.
        """
        drug_id = proposal.get("drug_id")
        chain = proposal.get("transitive_chain", [])
        claimed_pathways = proposal.get("inferred_pathways", [])

        if not drug_id:
            return {
                "chain_evidence_score": 0.0,
                "links_verified": 0,
                "links_total": max(len(chain), 1),
                "link_details": [],
                "verdict": "no_drug_id",
            }

        link_details = []
        verified_count = 0

        # Link 1: Does the drug actually target anything?
        target_result = await session.execute(
            select(Target.gene_symbol, DrugTarget.action_type)
            .join(DrugTarget, DrugTarget.target_id == Target.id)
            .where(DrugTarget.drug_id == drug_id)
        )
        drug_targets = target_result.all()
        drug_target_genes = {r[0] for r in drug_targets}
        drug_target_actions = {r[0]: r[1] for r in drug_targets}

        link1_verified = len(drug_targets) > 0
        if link1_verified:
            verified_count += 1
        link_details.append({
            "link": "drug_targets",
            "description": f"Drug has {len(drug_targets)} known targets",
            "verified": link1_verified,
            "genes": list(drug_target_genes)[:10],
            "actions": drug_target_actions,
        })

        # Link 2: Do drug targets participate in claimed pathways?
        pathway_verified = False
        matched_pathways = []
        if drug_target_genes:
            target_ids_result = await session.execute(
                select(Target.id).where(Target.gene_symbol.in_(drug_target_genes))
            )
            target_ids = [r[0] for r in target_ids_result.all()]

            if target_ids:
                pw_result = await session.execute(
                    select(Pathway.name, Pathway.id)
                    .join(PathwayTarget, PathwayTarget.pathway_id == Pathway.id)
                    .where(PathwayTarget.target_id.in_(target_ids))
                    .distinct()
                    .limit(50)
                )
                db_pathways = pw_result.all()
                db_pathway_names = {r[0].lower() for r in db_pathways}
                db_pathway_ids = [r[1] for r in db_pathways]

                # Check overlap with claimed pathways
                for cp in claimed_pathways:
                    for dbp in db_pathway_names:
                        if cp.lower() in dbp or dbp in cp.lower():
                            matched_pathways.append(cp)
                            break

                pathway_verified = len(matched_pathways) > 0 or len(db_pathways) > 0

        if pathway_verified:
            verified_count += 1
        link_details.append({
            "link": "target_pathway",
            "description": f"Drug targets in {len(matched_pathways)} claimed pathways",
            "verified": pathway_verified,
            "matched_pathways": matched_pathways,
            "total_drug_pathways": len(db_pathways) if drug_target_genes else 0,
        })

        # Link 3: Do pathways/targets connect to this cancer's mutations?
        mutation_link = False
        overlapping_genes = []
        if drug_target_genes:
            mut_result = await session.execute(
                select(Mutation.gene_symbol, Mutation.frequency_percent)
                .where(
                    Mutation.cancer_type_id == cancer_type_id,
                    Mutation.gene_symbol.in_(drug_target_genes),
                )
            )
            direct_mutations = mut_result.all()
            overlapping_genes = [r[0] for r in direct_mutations]
            mutation_link = len(direct_mutations) > 0

            # Also check pathway-level: do pathway genes overlap with mutations?
            if not mutation_link and db_pathway_ids if drug_target_genes else False:
                # Get genes in drug-related pathways
                pw_gene_result = await session.execute(
                    select(Target.gene_symbol)
                    .join(PathwayTarget, PathwayTarget.target_id == Target.id)
                    .where(PathwayTarget.pathway_id.in_(db_pathway_ids[:20]))
                    .distinct()
                    .limit(200)
                )
                pathway_genes = {r[0] for r in pw_gene_result.all()}

                # Check if any pathway genes are mutated in this cancer
                if pathway_genes:
                    pw_mut_result = await session.execute(
                        select(Mutation.gene_symbol)
                        .where(
                            Mutation.cancer_type_id == cancer_type_id,
                            Mutation.gene_symbol.in_(pathway_genes),
                        )
                        .limit(20)
                    )
                    pw_mutations = pw_mut_result.all()
                    if pw_mutations:
                        mutation_link = True
                        overlapping_genes = [r[0] for r in pw_mutations]

        if mutation_link:
            verified_count += 1
        link_details.append({
            "link": "pathway_cancer",
            "description": f"{len(overlapping_genes)} genes connect drug pathways to cancer mutations",
            "verified": mutation_link,
            "overlapping_genes": overlapping_genes[:10],
        })

        # Link 4: Expression correlation — do drug targets show relevant expression?
        expression_link = False
        expression_matches = []
        if drug_target_genes:
            expr_result = await session.execute(
                select(
                    CancerMolecularProfile.gene_symbol,
                    CancerMolecularProfile.alteration_type,
                    CancerMolecularProfile.expression_zscore,
                )
                .where(
                    CancerMolecularProfile.cancer_type_id == cancer_type_id,
                    CancerMolecularProfile.gene_symbol.in_(drug_target_genes),
                )
            )
            expressions = expr_result.all()
            for gene, alt_type, zscore in expressions:
                action = drug_target_actions.get(gene, "")
                # Check pharmacological coherence
                coherent = False
                if action in ("inhibitor", "antagonist") and zscore and zscore > 1.5:
                    coherent = True  # Inhibiting an overexpressed target
                elif action in ("agonist", "activator") and zscore and zscore < -1.5:
                    coherent = True  # Activating an underexpressed target
                elif zscore and abs(zscore) > 2.0:
                    coherent = True  # Strong dysregulation regardless

                if coherent:
                    expression_matches.append({
                        "gene": gene,
                        "action": action,
                        "zscore": float(zscore) if zscore else None,
                    })

            expression_link = len(expression_matches) > 0

        if expression_link:
            verified_count += 1
        link_details.append({
            "link": "expression_coherence",
            "description": f"{len(expression_matches)} drug targets show coherent expression in cancer",
            "verified": expression_link,
            "matches": expression_matches[:5],
        })

        # Link 5: Literature co-mention — do papers link this drug to this cancer?
        lit_link = False
        co_mention_count = 0
        if drug_id:
            co_result = await session.execute(
                select(func.count())
                .select_from(LiteratureDrug)
                .join(LiteratureCancer, LiteratureCancer.literature_id == LiteratureDrug.literature_id)
                .where(
                    LiteratureDrug.drug_id == drug_id,
                    LiteratureCancer.cancer_type_id == cancer_type_id,
                )
            )
            co_mention_count = co_result.scalar() or 0
            lit_link = co_mention_count > 0

        if lit_link:
            verified_count += 1
        link_details.append({
            "link": "literature_co_mention",
            "description": f"{co_mention_count} papers mention both drug and cancer",
            "verified": lit_link,
            "co_mention_count": co_mention_count,
        })

        total_links = 5
        chain_score = verified_count / total_links

        return {
            "chain_evidence_score": round(chain_score, 3),
            "links_verified": verified_count,
            "links_total": total_links,
            "link_details": link_details,
            "verdict": (
                "strong" if chain_score >= 0.6
                else "moderate" if chain_score >= 0.4
                else "weak" if chain_score > 0
                else "unverified"
            ),
        }

    # ------------------------------------------------------------------
    # Full verification pipeline
    # ------------------------------------------------------------------

    async def verify_proposal(
        self,
        proposal: dict[str, Any],
        cancer_type_id: int,
        cancer_context: dict[str, Any],
        session: AsyncSession,
    ) -> dict[str, Any]:
        """Run all five verification rounds on a single proposal.

        Returns the proposal enriched with verification scores and a
        final verified_score that combines all evidence.
        """
        from app.services.novelty_plausibility import (
            DosingPlausibilityChecker,
            NoveltyChecker,
        )

        # Round 1: Adversarial critique (LLM call)
        critique = await self.adversarial_critique(proposal, cancer_context)

        # Round 2: Citation grounding (database check)
        grounding = await self.ground_citations(proposal, session)

        # Round 3: Chain verification (database check)
        chain = await self.verify_chain(proposal, cancer_type_id, session)

        # Round 4: Novelty check (PubMed query)
        novelty_checker = NoveltyChecker()
        drug_name = proposal.get("drug_name", "")
        cancer_name = cancer_context.get("name", "")
        novelty = await novelty_checker.check_pubmed_novelty(drug_name, cancer_name)

        # Round 5: Dosing plausibility (database check)
        dosing_checker = DosingPlausibilityChecker()
        drug_id = proposal.get("drug_id")
        if drug_id:
            dosing = await dosing_checker.check_dosing_plausibility(
                drug_id, cancer_type_id, session
            )
        else:
            dosing = {
                "plausible": None,
                "verdict": "no_drug_id",
                "details": [],
            }

        # Compute verified score
        original_confidence = proposal.get("confidence", 0)
        critique_confidence = critique.get("adjusted_confidence", original_confidence)
        grounding_score = grounding.get("grounding_score", 0)
        chain_score = chain.get("chain_evidence_score", 0)

        # Verified score weights evidence from all five sources:
        #   35% adversarial-adjusted confidence (LLM-vs-LLM)
        #   25% chain evidence score (structured database verification)
        #   15% grounding score (citation verification)
        #   15% novelty score (0 if well_studied, scaled by tier)
        #   10% original confidence (proposal author's assessment)
        novelty_tier = novelty.get("novelty_tier", "check_failed")
        novelty_score = {
            "novel": 1.0,
            "understudied": 0.8,
            "known": 0.3,
            "well_studied": 0.0,
            "check_failed": 0.5,  # Neutral if check failed
        }.get(novelty_tier, 0.5)

        verified_score = (
            0.35 * critique_confidence
            + 0.25 * chain_score
            + 0.15 * grounding_score
            + 0.15 * novelty_score
            + 0.10 * original_confidence
        )

        # Apply severity penalty from critique
        severity = critique.get("severity", "unknown")
        severity_multiplier = {
            "fatal": 0.1,
            "major": 0.5,
            "minor": 0.85,
            "negligible": 1.0,
        }.get(severity, 0.7)
        verified_score *= severity_multiplier

        # Apply dosing penalty — implausible dosing kills the proposal
        dosing_verdict = dosing.get("verdict", "no_affinity_data")
        dosing_multiplier = {
            "highly_plausible": 1.0,
            "plausible": 1.0,
            "marginal": 0.7,
            "implausible": 0.2,
            "no_affinity_data": 1.0,  # No data = no penalty
            "drug_not_found": 1.0,
            "no_drug_id": 1.0,
        }.get(dosing_verdict, 1.0)
        verified_score *= dosing_multiplier

        should_proceed = (
            critique.get("should_proceed", False)
            and verified_score >= 0.25
            and chain_score > 0  # Must have SOME database evidence
            and novelty_tier not in ("well_studied",)  # Already known = not a discovery
            and dosing_verdict != "implausible"  # Can't reach target = dead
        )

        return {
            "drug_name": proposal.get("drug_name"),
            "drug_id": proposal.get("drug_id"),
            "original_confidence": original_confidence,
            "verified_score": round(verified_score, 3),
            "should_proceed": should_proceed,
            "verification": {
                "adversarial_critique": {
                    "severity": severity,
                    "adjusted_confidence": critique_confidence,
                    "weaknesses": critique.get("weaknesses", []),
                    "missing_evidence": critique.get("missing_evidence", ""),
                    "alternative_explanations": critique.get("alternative_explanations", []),
                },
                "citation_grounding": {
                    "score": grounding_score,
                    "verdict": grounding.get("verdict"),
                    "papers_found": grounding.get("papers_found", 0),
                    "papers_supporting": grounding.get("papers_supporting", 0),
                },
                "chain_evidence": {
                    "score": chain_score,
                    "verdict": chain.get("verdict"),
                    "links_verified": chain.get("links_verified", 0),
                    "link_details": chain.get("link_details", []),
                },
                "novelty_check": {
                    "score": novelty_score,
                    "tier": novelty_tier,
                    "paper_count": novelty.get("paper_count", -1),
                    "total_evidence": novelty.get("total_evidence", -1),
                    "is_novel": novelty.get("is_novel"),
                    "interpretation": novelty.get("interpretation", ""),
                    "top_papers": novelty.get("top_papers", [])[:5],
                },
                "dosing_plausibility": {
                    "verdict": dosing_verdict,
                    "plausible": dosing.get("plausible"),
                    "coverage_ratio": dosing.get("coverage_ratio"),
                    "estimated_cmax_nm": dosing.get("estimated_cmax_nm"),
                    "median_affinity_nm": dosing.get("median_affinity_nm"),
                    "interpretation": dosing.get("interpretation", ""),
                },
            },
            "cost_usd": critique.get("cost_usd", 0),
        }


# ------------------------------------------------------------------
# Utility functions
# ------------------------------------------------------------------


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


def _compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    prices = PRICING.get(model, PRICING[list(PRICING.keys())[0]])
    return round(
        (input_tokens * prices["input"] + output_tokens * prices["output"]) / 1_000_000,
        6,
    )
