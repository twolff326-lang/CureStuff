"""Literature analysis service — semantic search, evidence finder, Claude extraction.

Provides the intelligence layer on top of raw literature data:
  - Semantic similarity search via pgvector embeddings
  - Drug-cancer evidence aggregation across direct/target/pathway papers
  - Claude-powered abstract finding extraction (structured JSON)
  - Literature support score computation (0-100)
"""

import asyncio
import json
import logging
from datetime import date, timedelta
from typing import Any

import anthropic
from sqlalchemy import and_, case, desc, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.cancer_type import CancerType
from app.models.drug import Drug, DrugTarget, LiteratureDrug
from app.models.literature import Literature, LiteratureCancer, LiteratureTarget
from app.models.pathway import PathwayTarget
from app.models.target import Target
from app.services.embedding import EmbeddingService

logger = logging.getLogger(__name__)

CLAUDE_MODEL = "claude-sonnet-4-20250514"
CLAUDE_MAX_RPM = 50  # requests per minute for standard tier

EXTRACTION_SYSTEM_PROMPT = """You are an expert biomedical researcher specializing in drug repurposing and cancer biology.
Your task is to extract structured findings from a PubMed abstract.

Analyze the abstract and return a JSON object with these fields:
- study_type: one of "in_vitro", "in_vivo", "clinical_trial", "epidemiological", "computational", "review", "case_report", "meta_analysis"
- key_finding: one sentence summarizing the most important result
- effect_direction: "positive" (drug shows anticancer activity), "negative" (drug shows no effect or harmful effect), "neutral", or "mixed"
- cell_lines: array of cell line names if in vitro study (empty array if not applicable)
- model_organism: string if in vivo (null if not applicable)
- sample_size: integer if clinical/epidemiological (null if not applicable)
- statistical_significance: p-value or confidence interval if reported (null if not)
- drug_concentration: effective concentration if reported (null if not)
- mechanism_mentioned: brief description of any mechanism discussed (null if none)
- biomarkers_mentioned: array of gene/protein names mentioned as relevant
- quality_indicators: {"peer_reviewed": true, "sample_size_adequate": bool, "controls_mentioned": bool}

Return ONLY valid JSON. No explanations or markdown."""


class LiteratureAnalyzer:
    """Intelligence layer for literature analysis and scoring."""

    def __init__(self):
        self._embedding_service = EmbeddingService()
        self._claude_client: anthropic.AsyncAnthropic | None = None
        self._claude_semaphore = asyncio.Semaphore(CLAUDE_MAX_RPM)

    def _get_claude_client(self) -> anthropic.AsyncAnthropic:
        if self._claude_client is None:
            self._claude_client = anthropic.AsyncAnthropic(
                api_key=settings.anthropic_api_key
            )
        return self._claude_client

    # ------------------------------------------------------------------
    # Semantic similarity search
    # ------------------------------------------------------------------

    async def search_similar_abstracts(
        self,
        session: AsyncSession,
        query_text: str,
        top_k: int = 20,
    ) -> list[dict[str, Any]]:
        """Find papers semantically similar to a query text.

        Embeds the query, then uses pgvector cosine similarity.
        Returns list of {id, pmid, title, journal, pub_date, similarity, abstract_snippet}.
        """
        query_embedding = self._embedding_service.embed_single(query_text)

        result = await session.execute(
            text("""
                SELECT id, pmid, title, journal, pub_date, abstract,
                       1 - (abstract_embedding <=> :embedding::vector) AS similarity
                FROM literature
                WHERE abstract_embedding IS NOT NULL
                ORDER BY abstract_embedding <=> :embedding::vector
                LIMIT :top_k
            """),
            {"embedding": str(query_embedding), "top_k": top_k},
        )
        rows = result.all()

        return [
            {
                "id": row[0],
                "pmid": row[1],
                "title": row[2],
                "journal": row[3],
                "pub_date": row[4].isoformat() if row[4] else None,
                "abstract_snippet": (row[5] or "")[:300],
                "similarity": round(float(row[6]), 4),
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Full-text keyword search
    # ------------------------------------------------------------------

    async def search_keyword(
        self,
        session: AsyncSession,
        query_text: str,
        top_k: int = 20,
    ) -> list[dict[str, Any]]:
        """Full-text keyword search using PostgreSQL tsvector."""
        result = await session.execute(
            text("""
                SELECT id, pmid, title, journal, pub_date, abstract,
                       ts_rank(search_vector, websearch_to_tsquery('english', :query)) AS rank
                FROM literature
                WHERE search_vector @@ websearch_to_tsquery('english', :query)
                ORDER BY rank DESC
                LIMIT :top_k
            """),
            {"query": query_text, "top_k": top_k},
        )
        rows = result.all()

        return [
            {
                "id": row[0],
                "pmid": row[1],
                "title": row[2],
                "journal": row[3],
                "pub_date": row[4].isoformat() if row[4] else None,
                "abstract_snippet": (row[5] or "")[:300],
                "relevance_score": round(float(row[6]), 4),
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Hybrid search (keyword + semantic with RRF)
    # ------------------------------------------------------------------

    async def search_hybrid(
        self,
        session: AsyncSession,
        query_text: str,
        top_k: int = 20,
    ) -> list[dict[str, Any]]:
        """Hybrid search combining keyword and semantic results via RRF.

        Reciprocal Rank Fusion: score = sum(1 / (k + rank_i)) across methods.
        """
        k_constant = 60  # RRF constant

        keyword_results = await self.search_keyword(session, query_text, top_k=top_k * 2)
        semantic_results = await self.search_similar_abstracts(session, query_text, top_k=top_k * 2)

        # Build RRF scores keyed by literature ID
        rrf_scores: dict[int, float] = {}
        result_map: dict[int, dict] = {}

        for rank, item in enumerate(keyword_results, start=1):
            lit_id = item["id"]
            rrf_scores[lit_id] = rrf_scores.get(lit_id, 0) + 1.0 / (k_constant + rank)
            result_map[lit_id] = item

        for rank, item in enumerate(semantic_results, start=1):
            lit_id = item["id"]
            rrf_scores[lit_id] = rrf_scores.get(lit_id, 0) + 1.0 / (k_constant + rank)
            if lit_id not in result_map:
                result_map[lit_id] = item

        # Sort by RRF score descending
        sorted_ids = sorted(rrf_scores, key=lambda x: rrf_scores[x], reverse=True)[:top_k]

        results = []
        for lit_id in sorted_ids:
            item = result_map[lit_id]
            item["rrf_score"] = round(rrf_scores[lit_id], 6)
            results.append(item)

        return results

    # ------------------------------------------------------------------
    # Drug-cancer evidence finder
    # ------------------------------------------------------------------

    async def find_drug_cancer_evidence(
        self,
        session: AsyncSession,
        drug_id: int,
        cancer_type_id: int,
    ) -> dict[str, Any]:
        """Find all published evidence connecting a drug to a cancer type.

        Returns direct papers, target papers, pathway papers, and counts.
        """
        # 1. Direct papers mentioning BOTH drug and cancer
        direct_query = (
            select(
                Literature.id, Literature.pmid, Literature.title,
                Literature.journal, Literature.pub_date,
                Literature.extracted_findings,
            )
            .join(LiteratureDrug, LiteratureDrug.literature_id == Literature.id)
            .join(LiteratureCancer, LiteratureCancer.literature_id == Literature.id)
            .where(
                LiteratureDrug.drug_id == drug_id,
                LiteratureCancer.cancer_type_id == cancer_type_id,
            )
            .order_by(desc(Literature.pub_date))
            .limit(50)
        )
        result = await session.execute(direct_query)
        direct_papers = [
            {
                "id": r[0], "pmid": r[1], "title": r[2],
                "journal": r[3],
                "pub_date": r[4].isoformat() if r[4] else None,
                "extracted_findings": r[5],
            }
            for r in result.all()
        ]

        # 2. Papers about the drug's targets in this cancer context
        target_subq = (
            select(DrugTarget.target_id)
            .where(DrugTarget.drug_id == drug_id)
        )
        target_query = (
            select(
                Literature.id, Literature.pmid, Literature.title,
                Literature.journal, Literature.pub_date,
                Literature.extracted_findings,
            )
            .join(LiteratureTarget, LiteratureTarget.literature_id == Literature.id)
            .join(LiteratureCancer, LiteratureCancer.literature_id == Literature.id)
            .where(
                LiteratureTarget.target_id.in_(target_subq),
                LiteratureCancer.cancer_type_id == cancer_type_id,
            )
            .order_by(desc(Literature.pub_date))
            .limit(50)
        )
        result = await session.execute(target_query)
        target_papers = [
            {
                "id": r[0], "pmid": r[1], "title": r[2],
                "journal": r[3],
                "pub_date": r[4].isoformat() if r[4] else None,
                "extracted_findings": r[5],
            }
            for r in result.all()
        ]

        # 3. Pathway-level evidence via semantic search
        # Build a description of the drug-cancer connection for semantic search
        drug_result = await session.execute(
            select(Drug.name, Drug.mechanism_of_action).where(Drug.id == drug_id)
        )
        drug_row = drug_result.first()
        cancer_result = await session.execute(
            select(CancerType.name).where(CancerType.id == cancer_type_id)
        )
        cancer_row = cancer_result.first()

        pathway_papers: list[dict] = []
        if drug_row and cancer_row:
            query_text = (
                f"{drug_row[0]} {drug_row[1] or ''} "
                f"{cancer_row[0]} pathway therapeutic target"
            )
            pathway_papers = await self.search_similar_abstracts(
                session, query_text, top_k=20
            )

        # Determine strongest evidence
        strongest = None
        for paper in direct_papers:
            findings = paper.get("extracted_findings")
            if findings and findings.get("effect_direction") == "positive":
                strongest = (
                    f"{findings.get('study_type', 'study')}: "
                    f"{findings.get('key_finding', paper['title'])} "
                    f"(PMID: {paper['pmid']})"
                )
                break

        return {
            "direct_papers": direct_papers,
            "target_papers": target_papers,
            "pathway_papers": pathway_papers,
            "total_evidence_count": (
                len(direct_papers) + len(target_papers) + len(pathway_papers)
            ),
            "direct_evidence_count": len(direct_papers),
            "indirect_evidence_count": len(target_papers) + len(pathway_papers),
            "strongest_evidence": strongest,
        }

    # ------------------------------------------------------------------
    # Claude-powered abstract extraction
    # ------------------------------------------------------------------

    async def extract_key_findings(
        self,
        abstract_text: str,
        drug_name: str | None = None,
        cancer_type: str | None = None,
    ) -> dict[str, Any]:
        """Use Claude to extract structured findings from a paper abstract."""
        client = self._get_claude_client()

        context = ""
        if drug_name:
            context += f"\nDrug of interest: {drug_name}"
        if cancer_type:
            context += f"\nCancer type of interest: {cancer_type}"

        user_prompt = f"Abstract:{context}\n\n{abstract_text}"

        async with self._claude_semaphore:
            try:
                response = await client.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=1500,
                    temperature=0,
                    system=EXTRACTION_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_prompt}],
                )

                response_text = response.content[0].text.strip()
                # Strip markdown code fences if present
                if response_text.startswith("```"):
                    response_text = response_text.removeprefix("```json").removeprefix("```")
                if response_text.endswith("```"):
                    response_text = response_text.removesuffix("```")
                response_text = response_text.strip()

                return json.loads(response_text)

            except json.JSONDecodeError as exc:
                logger.warning("Claude returned non-JSON: %s", exc)
                return {"error": "Failed to parse Claude response", "raw": response_text}
            except Exception as exc:
                logger.warning("Claude API call failed: %s", exc)
                return {"error": str(exc)}

    async def analyze_paper(
        self,
        session: AsyncSession,
        literature_id: int,
    ) -> dict[str, Any] | None:
        """Extract findings for a single paper and store results."""
        result = await session.execute(
            select(
                Literature.id, Literature.abstract,
                Literature.analysis_status,
            ).where(Literature.id == literature_id)
        )
        row = result.first()
        if not row or not row[1]:
            return None

        if row[2] == "analyzed":
            # Already analyzed — return stored findings
            findings_result = await session.execute(
                select(Literature.extracted_findings).where(
                    Literature.id == literature_id
                )
            )
            return findings_result.scalar_one_or_none()

        findings = await self.extract_key_findings(row[1])

        status = "analyzed" if "error" not in findings else "failed"
        await session.execute(
            update(Literature)
            .where(Literature.id == literature_id)
            .values(extracted_findings=findings, analysis_status=status)
        )
        await session.commit()

        return findings

    async def analyze_batch(
        self,
        session: AsyncSession,
        pmid_list: list[str] | None = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        """Run Claude extraction on a batch of un-analyzed papers.

        Priority: Phase 1 (drug+cancer) > drug+cancer mentions > remaining.
        """
        if pmid_list:
            query = (
                select(Literature.id, Literature.abstract)
                .where(
                    Literature.pmid.in_(pmid_list),
                    Literature.analysis_status == "pending",
                    Literature.abstract.isnot(None),
                )
            )
        else:
            # Prioritize papers linked to both drugs and cancers
            query = (
                select(Literature.id, Literature.abstract)
                .outerjoin(LiteratureDrug, LiteratureDrug.literature_id == Literature.id)
                .outerjoin(LiteratureCancer, LiteratureCancer.literature_id == Literature.id)
                .where(
                    Literature.analysis_status == "pending",
                    Literature.abstract.isnot(None),
                    Literature.abstract != "",
                )
                .order_by(
                    # Papers with both drug and cancer links first
                    desc(
                        case(
                            (
                                and_(
                                    LiteratureDrug.id.isnot(None),
                                    LiteratureCancer.id.isnot(None),
                                ),
                                2,
                            ),
                            (
                                or_(
                                    LiteratureDrug.id.isnot(None),
                                    LiteratureCancer.id.isnot(None),
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    )
                )
                .group_by(Literature.id)
                .limit(limit)
            )

        result = await session.execute(query)
        rows = result.all()
        logger.info("Analyzing %d papers with Claude", len(rows))

        analyzed = 0
        failed = 0

        for lit_id, abstract in rows:
            try:
                findings = await self.extract_key_findings(abstract)
                status = "analyzed" if "error" not in findings else "failed"

                await session.execute(
                    update(Literature)
                    .where(Literature.id == lit_id)
                    .values(extracted_findings=findings, analysis_status=status)
                )

                if status == "analyzed":
                    analyzed += 1
                else:
                    failed += 1

                # Commit every 50 records
                if (analyzed + failed) % 50 == 0:
                    await session.commit()
                    logger.info(
                        "Analysis progress: %d analyzed, %d failed", analyzed, failed
                    )

                # Rate limit: ~50 requests/minute
                await asyncio.sleep(1.2)

            except Exception as exc:
                logger.warning("Analysis failed for lit_id=%d: %s", lit_id, exc)
                failed += 1

        await session.commit()
        return {"analyzed": analyzed, "failed": failed, "total": len(rows)}

    # ------------------------------------------------------------------
    # Literature support score
    # ------------------------------------------------------------------

    async def compute_literature_support_score(
        self,
        session: AsyncSession,
        drug_id: int,
        cancer_type_id: int,
    ) -> dict[str, Any]:
        """Compute a 0-100 literature support score for a drug-cancer pair.

        Scoring:
          Direct evidence (drug + cancer in same paper):
            clinical_trial/meta_analysis positive: +25 (max 50)
            epidemiological positive: +15 (max 30)
            in_vivo positive: +10 (max 20)
            in_vitro positive: +5 (max 15)
            computational: +3 (max 9)
            review: +2 (max 6)
          Target papers: 0.7x multiplier
          Pathway papers: 0.4x multiplier
          Recency bonus: 1.5x for papers < 3 years old
          Negative evidence: -10 per paper
        """
        evidence = await self.find_drug_cancer_evidence(
            session, drug_id, cancer_type_id
        )

        study_weights = {
            "clinical_trial": 25, "meta_analysis": 25,
            "epidemiological": 15,
            "in_vivo": 10,
            "in_vitro": 5,
            "computational": 3,
            "review": 2, "case_report": 2,
        }
        study_caps = {
            "clinical_trial": 50, "meta_analysis": 50,
            "epidemiological": 30,
            "in_vivo": 20,
            "in_vitro": 15,
            "computational": 9,
            "review": 6, "case_report": 6,
        }

        three_years_ago = date.today() - timedelta(days=3 * 365)
        score = 0.0
        breakdown = {
            "direct_clinical": 0.0,
            "direct_preclinical": 0.0,
            "indirect_target": 0.0,
            "indirect_pathway": 0.0,
            "recency_bonus": 0.0,
            "negative_penalty": 0.0,
        }
        key_papers: list[dict] = []
        negative_count = 0
        accumulated: dict[str, float] = {}

        def _score_paper(
            paper: dict, multiplier: float, category: str
        ) -> float:
            findings = paper.get("extracted_findings") or {}
            study_type = findings.get("study_type", "review")
            direction = findings.get("effect_direction", "neutral")

            if direction == "negative":
                nonlocal negative_count
                negative_count += 1
                penalty = -10 * multiplier
                breakdown["negative_penalty"] += penalty
                return penalty

            if direction not in ("positive", "mixed"):
                return 0.0

            weight = study_weights.get(study_type, 2) * multiplier
            cap_key = f"{category}_{study_type}"
            current = accumulated.get(cap_key, 0.0)
            cap = study_caps.get(study_type, 6) * multiplier
            contribution = min(weight, cap - current)
            if contribution <= 0:
                return 0.0
            accumulated[cap_key] = current + contribution

            # Recency bonus
            pub_date_str = paper.get("pub_date")
            recency = 0.0
            if pub_date_str:
                try:
                    pd = date.fromisoformat(pub_date_str) if isinstance(pub_date_str, str) else pub_date_str
                    if pd and pd >= three_years_ago:
                        recency = contribution * 0.5
                        breakdown["recency_bonus"] += recency
                except (ValueError, TypeError):
                    pass

            if contribution > 0:
                key_papers.append({
                    "pmid": paper.get("pmid", ""),
                    "title": paper.get("title", ""),
                    "contribution": round(contribution + recency, 1),
                    "study_type": study_type,
                })

            return contribution + recency

        # Score direct papers
        for paper in evidence["direct_papers"]:
            pts = _score_paper(paper, 1.0, "direct")
            if pts > 0:
                breakdown["direct_clinical" if "clinical" in (paper.get("extracted_findings") or {}).get("study_type", "") else "direct_preclinical"] += pts
            score += pts

        # Score target papers (0.7x)
        for paper in evidence["target_papers"]:
            pts = _score_paper(paper, 0.7, "target")
            breakdown["indirect_target"] += max(pts, 0)
            score += pts

        # Score pathway papers (0.4x)
        for paper in evidence["pathway_papers"]:
            pts = _score_paper(paper, 0.4, "pathway")
            breakdown["indirect_pathway"] += max(pts, 0)
            score += pts

        # Clamp to 0-100
        final_score = max(0, min(100, round(score)))

        # Determine strongest study type
        strongest_type = "none"
        for paper in evidence["direct_papers"]:
            findings = paper.get("extracted_findings") or {}
            if findings.get("effect_direction") == "positive":
                strongest_type = findings.get("study_type", "unknown")
                break

        # Round breakdown values
        for k in breakdown:
            breakdown[k] = round(breakdown[k], 1)

        return {
            "score": final_score,
            "paper_count": evidence["total_evidence_count"],
            "direct_evidence_count": evidence["direct_evidence_count"],
            "indirect_evidence_count": evidence["indirect_evidence_count"],
            "strongest_study_type": strongest_type,
            "negative_evidence_count": negative_count,
            "key_papers": sorted(key_papers, key=lambda x: x["contribution"], reverse=True)[:10],
            "score_breakdown": breakdown,
        }
