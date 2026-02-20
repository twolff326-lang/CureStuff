"""OpenTargets connector — pre-computed target-disease association evidence.

Uses the OpenTargets Platform GraphQL API to fetch independent evidence
that a target is relevant to a disease (especially cancers).

API: https://api.platform.opentargets.org/api/v4/graphql
No API key required. Rate limit: ~10 req/sec.
Queries one target at a time (no batch support in GraphQL).
"""

import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.target import Target
from app.models.target_disease import TargetDiseaseAssociation
from app.services.ingestion.base import BaseConnector

logger = logging.getLogger(__name__)

OPENTARGETS_API = "https://api.platform.opentargets.org/api/v4/graphql"

# Cancer-related therapeutic area IDs in OpenTargets (EFO/MONDO ontology)
CANCER_THERAPEUTIC_AREAS = {
    "MONDO_0004992",  # cancer
    "EFO_0000311",    # cancer
    "EFO_0000616",    # neoplasm
    "OTAR_0000018",   # neoplasm
}

# Keywords to identify cancer-related diseases
CANCER_KEYWORDS = {
    "cancer", "carcinoma", "tumor", "tumour", "neoplasm", "neoplasia",
    "lymphoma", "leukemia", "leukaemia", "melanoma", "sarcoma",
    "glioma", "glioblastoma", "myeloma", "mesothelioma", "blastoma",
}

# GraphQL query for target-disease associations
TARGET_DISEASE_QUERY = """
query TargetDiseaseAssociations($ensemblId: String!) {
  target(ensemblId: $ensemblId) {
    id
    approvedSymbol
    associatedDiseases(page: { index: 0, size: 50 }) {
      count
      rows {
        disease {
          id
          name
          therapeuticAreas {
            id
            name
          }
        }
        score
        datatypeScores {
          id
          score
        }
      }
    }
  }
}
"""

# Map OpenTargets datatype IDs to our score columns
DATATYPE_SCORE_MAP = {
    "genetic_association": "genetic_association_score",
    "somatic_mutation": "somatic_mutation_score",
    "known_drug": "known_drug_score",
    "literature": "literature_score",
    "rna_expression": "rna_expression_score",
    "animal_model": "animal_model_score",
}


class OpenTargetsConnector(BaseConnector):
    """Fetches target-disease associations from OpenTargets Platform."""

    def __init__(
        self,
        db_session: AsyncSession | None = None,
        http_client: httpx.AsyncClient | None = None,
        **kwargs,
    ):
        super().__init__(
            db_session=db_session,
            http_client=http_client,
            rate_limit=10.0,
            batch_size=500,
            timeout=30.0,
            **kwargs,
        )
        self._headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def get_source_name(self) -> str:
        return "opentargets"

    def transform_record(self, raw_record: dict[str, Any]) -> dict[str, Any]:
        """Not used — connector writes directly."""
        return raw_record

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    async def fetch_data(self, session: AsyncSession) -> list[dict[str, Any]]:
        """Fetch target-disease associations for all targets with Ensembl IDs."""
        client = await self._get_client()
        owns_client = self._external_client is None

        try:
            # Get targets with Ensembl Gene IDs (populated by UniProt connector)
            result = await session.execute(
                select(Target.id, Target.ensembl_gene_id, Target.gene_symbol).where(
                    Target.ensembl_gene_id.isnot(None),
                    Target.ensembl_gene_id != "",
                )
            )
            targets = result.all()
            logger.info(
                "Fetching OpenTargets associations for %d targets with Ensembl IDs",
                len(targets),
            )

            if not targets:
                logger.warning(
                    "No targets have Ensembl IDs — run UniProt ingestion first"
                )
                return []

            await self.set_total_expected(session, len(targets))

            processed = 0
            skipped = 0

            for target_id, ensembl_id, gene_symbol in targets:
                try:
                    associations = await self._fetch_target_associations(
                        client, ensembl_id
                    )
                    if associations:
                        records = self._build_association_records(
                            target_id, associations
                        )
                        if records:
                            count = await self.batch_upsert_composite(
                                session,
                                TargetDiseaseAssociation,
                                records,
                                conflict_columns=["target_id", "disease_id"],
                                update_columns=[
                                    "disease_name", "overall_score",
                                    "genetic_association_score",
                                    "somatic_mutation_score",
                                    "known_drug_score", "literature_score",
                                    "rna_expression_score",
                                    "animal_model_score", "source",
                                ],
                            )
                            processed += count
                    else:
                        skipped += 1

                    # Commit periodically
                    if processed % 1000 == 0 and processed > 0:
                        await session.commit()

                except Exception as exc:
                    self.record_error(
                        f"target_{gene_symbol}", exc, record_id=ensembl_id
                    )

            await session.commit()
            logger.info(
                "OpenTargets: %d associations stored, %d targets had no data",
                processed, skipped,
            )

            self._records_processed = processed
            return []

        finally:
            if owns_client:
                await client.aclose()

    # ------------------------------------------------------------------
    # GraphQL query
    # ------------------------------------------------------------------

    async def _fetch_target_associations(
        self,
        client: httpx.AsyncClient,
        ensembl_id: str,
    ) -> list[dict[str, Any]]:
        """Fetch disease associations for a single target via GraphQL."""
        await self._rate_limiter.acquire()

        try:
            resp = await client.post(
                OPENTARGETS_API,
                json={
                    "query": TARGET_DISEASE_QUERY,
                    "variables": {"ensemblId": ensembl_id},
                },
                headers=self._headers,
                timeout=httpx.Timeout(30.0),
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        target_data = data.get("data", {}).get("target")
        if not target_data:
            return []

        assoc_data = target_data.get("associatedDiseases", {})
        return assoc_data.get("rows", [])

    # ------------------------------------------------------------------
    # Record building
    # ------------------------------------------------------------------

    def _build_association_records(
        self,
        target_id: int,
        associations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build TargetDiseaseAssociation records from GraphQL response."""
        records = []

        for assoc in associations:
            disease = assoc.get("disease") or {}
            disease_id = disease.get("id", "")
            disease_name = disease.get("name", "")

            if not disease_id:
                continue

            overall_score = assoc.get("score", 0)

            # Extract individual datatype scores
            scores: dict[str, float | None] = {
                col: None for col in DATATYPE_SCORE_MAP.values()
            }
            for ds in assoc.get("datatypeScores", []):
                dt_id = ds.get("id", "")
                dt_score = ds.get("score", 0)
                if dt_id in DATATYPE_SCORE_MAP:
                    scores[DATATYPE_SCORE_MAP[dt_id]] = dt_score

            records.append({
                "target_id": target_id,
                "disease_id": disease_id,
                "disease_name": disease_name,
                "overall_score": overall_score,
                "source": "opentargets",
                **scores,
            })

        return records
