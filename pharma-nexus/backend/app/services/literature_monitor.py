"""Real-time literature monitoring service.

Polls PubMed for new publications matching tracked drug-cancer pairs,
ingests them, triggers selective re-scoring, and generates alerts when
hypothesis scores change significantly.

Pipeline:
  1. Query PubMed E-utilities for recent papers (last N days)
  2. Match papers to tracked hypotheses via drug name + cancer type
  3. Ingest new papers into Literature table
  4. Re-score affected hypotheses
  5. Record score changes in ScoreHistory
  6. Generate LiteratureAlerts for significant changes (delta > threshold)
"""

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hypothesis import Hypothesis
from app.models.literature_alert import LiteratureAlert, MonitoringConfig, ScoreHistory

logger = logging.getLogger(__name__)


class LiteratureMonitor:
    """Monitors PubMed for new papers affecting tracked hypotheses."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def check_for_new_literature(
        self,
        days_back: int = 7,
        max_hypotheses: int = 100,
    ) -> dict[str, Any]:
        """Main monitoring loop: check PubMed for new papers, rescore, alert.

        Args:
            days_back: How many days back to search PubMed.
            max_hypotheses: Max hypotheses to check in one run.

        Returns:
            Summary of papers found, hypotheses rescored, alerts generated.
        """
        from app.models.cancer_type import CancerType
        from app.models.drug import Drug

        # Get actively monitored hypotheses
        monitored = await self._get_monitored_hypotheses(max_hypotheses)
        if not monitored:
            logger.info("No hypotheses being monitored")
            return {"checked": 0, "new_papers": 0, "alerts": 0}

        logger.info("Checking %d monitored hypotheses for new literature", len(monitored))

        total_new_papers = 0
        total_alerts = 0
        rescored_ids = []

        for hyp in monitored:
            # Get drug name and cancer name for PubMed query
            drug_result = await self.db.execute(
                select(Drug.name).where(Drug.id == hyp.drug_id)
            )
            drug_name = drug_result.scalar_one_or_none()

            cancer_result = await self.db.execute(
                select(CancerType.name).where(CancerType.id == hyp.cancer_type_id)
            )
            cancer_name = cancer_result.scalar_one_or_none()

            if not drug_name or not cancer_name:
                continue

            # Search PubMed for recent papers
            new_papers = await self._search_pubmed_recent(
                drug_name, cancer_name, days_back
            )

            if new_papers:
                total_new_papers += len(new_papers)

                # Ingest the new papers
                ingested = await self._ingest_papers(new_papers, hyp.drug_id, hyp.cancer_type_id)

                if ingested > 0:
                    # Re-score this hypothesis
                    old_score = hyp.composite_score
                    new_score = await self._rescore_hypothesis(hyp)

                    if new_score is not None:
                        delta = new_score - old_score
                        rescored_ids.append(hyp.id)

                        # Record score history
                        await self._record_score_change(
                            hyp, old_score, new_score, "new_literature",
                            {"papers_found": len(new_papers), "papers_ingested": ingested},
                        )

                        # Generate alert if significant
                        alert = await self._maybe_generate_alert(
                            hyp, old_score, new_score, new_papers
                        )
                        if alert:
                            total_alerts += 1

            # Update last_checked timestamp
            await self._update_last_checked(hyp.id)

        await self.db.commit()

        summary = {
            "checked": len(monitored),
            "new_papers": total_new_papers,
            "rescored": len(rescored_ids),
            "alerts": total_alerts,
            "rescored_hypothesis_ids": rescored_ids,
        }
        logger.info("Literature monitoring complete: %s", summary)
        return summary

    async def _get_monitored_hypotheses(self, limit: int) -> list:
        """Get hypotheses that are actively monitored and due for checking."""
        # If monitoring_config has active entries, use those
        config_result = await self.db.execute(
            select(MonitoringConfig.hypothesis_id).where(
                MonitoringConfig.is_active == 1
            ).limit(limit)
        )
        monitored_ids = [r[0] for r in config_result.all()]

        if monitored_ids:
            result = await self.db.execute(
                select(Hypothesis).where(Hypothesis.id.in_(monitored_ids))
            )
            return list(result.scalars().all())

        # Fallback: monitor all hypotheses with composite_score >= 30
        result = await self.db.execute(
            select(Hypothesis).where(
                Hypothesis.composite_score >= 30
            ).order_by(Hypothesis.composite_score.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def _search_pubmed_recent(
        self,
        drug_name: str,
        cancer_name: str,
        days_back: int,
    ) -> list[dict]:
        """Search PubMed E-utilities for recent papers mentioning drug + cancer.

        Uses the NCBI E-utilities esearch API with reldate parameter.
        """
        import httpx

        query = f'"{drug_name}"[Title/Abstract] AND "{cancer_name}"[Title/Abstract]'
        params = {
            "db": "pubmed",
            "term": query,
            "reldate": days_back,
            "datetype": "edat",
            "retmax": 20,
            "retmode": "json",
            "sort": "date",
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                # Search
                search_resp = await client.get(
                    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                    params=params,
                )
                search_resp.raise_for_status()
                search_data = search_resp.json()

                id_list = search_data.get("esearchresult", {}).get("idlist", [])
                if not id_list:
                    return []

                # Fetch summaries
                summary_resp = await client.get(
                    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
                    params={
                        "db": "pubmed",
                        "id": ",".join(id_list),
                        "retmode": "json",
                    },
                )
                summary_resp.raise_for_status()
                summary_data = summary_resp.json()

                papers = []
                result_block = summary_data.get("result", {})
                for pmid in id_list:
                    info = result_block.get(pmid, {})
                    if isinstance(info, dict) and "title" in info:
                        papers.append({
                            "pmid": pmid,
                            "title": info.get("title", ""),
                            "source": info.get("source", ""),
                            "pubdate": info.get("pubdate", ""),
                        })

                logger.info(
                    "PubMed search for '%s' + '%s': %d results",
                    drug_name, cancer_name, len(papers),
                )
                return papers

        except Exception as e:
            logger.warning("PubMed search failed for %s + %s: %s", drug_name, cancer_name, e)
            return []

    async def _ingest_papers(
        self,
        papers: list[dict],
        drug_id: int,
        cancer_type_id: int,
    ) -> int:
        """Ingest new papers into the Literature table if they don't exist."""
        from app.models.drug import LiteratureDrug
        from app.models.literature import Literature, LiteratureCancer

        ingested = 0
        for paper in papers:
            pmid = paper["pmid"]

            # Check if already exists
            existing = await self.db.execute(
                select(func.count(Literature.id)).where(Literature.pmid == pmid)
            )
            if (existing.scalar() or 0) > 0:
                continue

            # Create literature record
            lit = Literature(
                pmid=pmid,
                title=paper.get("title", ""),
                journal=paper.get("source", ""),
                pub_date=datetime.now(timezone.utc).date(),
                analysis_status="pending",
            )
            self.db.add(lit)
            await self.db.flush()

            # Link to drug and cancer
            self.db.add(LiteratureDrug(
                literature_id=lit.id,
                drug_id=drug_id,
                mention_type="co_mention",
            ))
            self.db.add(LiteratureCancer(
                literature_id=lit.id,
                cancer_type_id=cancer_type_id,
                mention_type="co_mention",
            ))
            ingested += 1

        return ingested

    async def _rescore_hypothesis(self, hypothesis) -> float | None:
        """Re-score a single hypothesis with current weights."""
        try:
            from app.services.evidence_scorer import EvidenceScorer
            from app.services.scoring_config import ScoringConfig

            scorer = EvidenceScorer()
            config = ScoringConfig()
            weights = await config.get_active_weights(self.db)

            dimension_scores = await scorer.score_all_dimensions(
                hypothesis.drug_id, hypothesis.cancer_type_id, self.db
            )

            new_composite = config.compute_composite_score(dimension_scores, weights)
            new_strength = config.determine_evidence_strength(new_composite)

            # Update hypothesis
            hypothesis.composite_score = new_composite
            hypothesis.evidence_strength = new_strength
            hypothesis.pathway_overlap_score = dimension_scores["pathway_overlap"]["score"]
            hypothesis.expression_correlation_score = dimension_scores["expression_correlation"]["score"]
            hypothesis.literature_support_score = dimension_scores["literature_support"]["score"]
            hypothesis.clinical_evidence_score = dimension_scores["clinical_evidence"]["score"]
            hypothesis.safety_score = dimension_scores["safety"]["score"]
            hypothesis.novelty_score = dimension_scores["novelty"]["score"]
            hypothesis.causal_dependency_score = dimension_scores["causal_dependency"]["score"]

            return new_composite

        except Exception as e:
            logger.warning("Failed to rescore hypothesis %d: %s", hypothesis.id, e)
            return None

    async def _record_score_change(
        self,
        hypothesis,
        old_score: float,
        new_score: float,
        trigger: str,
        trigger_details: dict,
    ) -> None:
        """Record a score change in the ScoreHistory table."""
        history = ScoreHistory(
            hypothesis_id=hypothesis.id,
            old_composite_score=old_score,
            new_composite_score=new_score,
            score_delta=round(new_score - old_score, 2),
            trigger=trigger,
            trigger_details=trigger_details,
            dimension_scores={
                "pathway_overlap": hypothesis.pathway_overlap_score,
                "expression_correlation": hypothesis.expression_correlation_score,
                "literature_support": hypothesis.literature_support_score,
                "clinical_evidence": hypothesis.clinical_evidence_score,
                "safety": hypothesis.safety_score,
                "novelty": hypothesis.novelty_score,
                "causal_dependency": hypothesis.causal_dependency_score,
            },
        )
        self.db.add(history)

    async def _maybe_generate_alert(
        self,
        hypothesis,
        old_score: float,
        new_score: float,
        papers: list[dict],
    ) -> LiteratureAlert | None:
        """Generate an alert if the score change exceeds the threshold."""
        delta = new_score - old_score

        # Get threshold from monitoring config or use default
        config_result = await self.db.execute(
            select(MonitoringConfig).where(
                MonitoringConfig.hypothesis_id == hypothesis.id,
                MonitoringConfig.is_active == 1,
            )
        )
        config = config_result.scalar_one_or_none()
        threshold = config.alert_threshold if config else 5.0

        if abs(delta) < threshold:
            return None

        # Determine severity
        abs_delta = abs(delta)
        if abs_delta >= 15:
            severity = "critical"
        elif abs_delta >= 10:
            severity = "significant"
        elif abs_delta >= 5:
            severity = "notable"
        else:
            severity = "info"

        direction = "increased" if delta > 0 else "decreased"
        paper_titles = [p.get("title", "")[:100] for p in papers[:3]]

        alert = LiteratureAlert(
            hypothesis_id=hypothesis.id,
            alert_type=f"score_{direction.split('d')[0]}se" if delta < 0 else "score_increase",
            severity=severity,
            title=(
                f"Score {direction} by {abs_delta:.1f} pts for hypothesis #{hypothesis.id} "
                f"({old_score:.1f} -> {new_score:.1f})"
            ),
            description=(
                f"{len(papers)} new paper(s) found. "
                f"Latest: {paper_titles[0] if paper_titles else 'N/A'}"
            ),
            score_delta=round(delta, 2),
            source_pmid=papers[0].get("pmid") if papers else None,
            source_data={"papers": papers[:5]},
        )
        self.db.add(alert)

        logger.info(
            "Alert generated: %s (hypothesis=%d, delta=%.1f)",
            severity, hypothesis.id, delta,
        )
        return alert

    async def _update_last_checked(self, hypothesis_id: int) -> None:
        """Update the last_checked_at timestamp for a monitored hypothesis."""
        await self.db.execute(
            update(MonitoringConfig).where(
                MonitoringConfig.hypothesis_id == hypothesis_id
            ).values(last_checked_at=datetime.now(timezone.utc))
        )

    # ------------------------------------------------------------------
    # Alert management
    # ------------------------------------------------------------------

    async def get_alerts(
        self,
        unread_only: bool = True,
        severity: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Get literature alerts with filtering."""
        query = select(LiteratureAlert)
        count_query = select(func.count(LiteratureAlert.id))

        if unread_only:
            query = query.where(LiteratureAlert.is_read == 0)
            count_query = count_query.where(LiteratureAlert.is_read == 0)
        if severity:
            query = query.where(LiteratureAlert.severity == severity)
            count_query = count_query.where(LiteratureAlert.severity == severity)

        total = (await self.db.execute(count_query)).scalar() or 0
        result = await self.db.execute(
            query.order_by(LiteratureAlert.created_at.desc()).offset(offset).limit(limit)
        )
        alerts = result.scalars().all()

        return {
            "total": total,
            "alerts": [
                {
                    "id": a.id,
                    "hypothesis_id": a.hypothesis_id,
                    "alert_type": a.alert_type,
                    "severity": a.severity,
                    "title": a.title,
                    "description": a.description,
                    "score_delta": a.score_delta,
                    "source_pmid": a.source_pmid,
                    "is_read": a.is_read,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                }
                for a in alerts
            ],
        }

    async def mark_alert_read(self, alert_id: int) -> bool:
        """Mark an alert as read."""
        result = await self.db.execute(
            update(LiteratureAlert).where(
                LiteratureAlert.id == alert_id
            ).values(is_read=1)
        )
        return result.rowcount > 0

    async def get_score_history(
        self,
        hypothesis_id: int,
        limit: int = 50,
    ) -> list[dict]:
        """Get score change history for a hypothesis."""
        result = await self.db.execute(
            select(ScoreHistory).where(
                ScoreHistory.hypothesis_id == hypothesis_id
            ).order_by(ScoreHistory.created_at.desc()).limit(limit)
        )
        history = result.scalars().all()
        return [
            {
                "id": h.id,
                "old_score": h.old_composite_score,
                "new_score": h.new_composite_score,
                "delta": h.score_delta,
                "trigger": h.trigger,
                "trigger_details": h.trigger_details,
                "dimension_scores": h.dimension_scores,
                "created_at": h.created_at.isoformat() if h.created_at else None,
            }
            for h in history
        ]

    async def enable_monitoring(
        self,
        hypothesis_id: int,
        alert_threshold: float = 5.0,
        check_interval_hours: int = 168,
    ) -> dict:
        """Enable monitoring for a hypothesis."""
        existing = await self.db.execute(
            select(MonitoringConfig).where(
                MonitoringConfig.hypothesis_id == hypothesis_id
            )
        )
        config = existing.scalar_one_or_none()

        if config:
            config.is_active = 1
            config.alert_threshold = alert_threshold
            config.check_interval_hours = check_interval_hours
        else:
            config = MonitoringConfig(
                hypothesis_id=hypothesis_id,
                is_active=1,
                alert_threshold=alert_threshold,
                check_interval_hours=check_interval_hours,
            )
            self.db.add(config)

        await self.db.flush()
        return {
            "hypothesis_id": hypothesis_id,
            "is_active": True,
            "alert_threshold": alert_threshold,
            "check_interval_hours": check_interval_hours,
        }

    async def disable_monitoring(self, hypothesis_id: int) -> bool:
        """Disable monitoring for a hypothesis."""
        result = await self.db.execute(
            update(MonitoringConfig).where(
                MonitoringConfig.hypothesis_id == hypothesis_id
            ).values(is_active=0)
        )
        return result.rowcount > 0
