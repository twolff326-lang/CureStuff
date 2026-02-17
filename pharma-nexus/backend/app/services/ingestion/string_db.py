"""STRING database connector — protein-protein interaction networks.

Fetches interaction partners for all drug targets in our database using
STRING's batch query API.

API base: https://string-db.org/api
No API key required. Supports batch queries of up to 2,000 proteins.
Rate limit: ~1 req/sec between batches.

Interactions are bidirectional — A-B is the same as B-A. We store each
pair once (alphabetically sorted) to avoid duplicates.
"""

import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.target import ProteinInteraction, Target
from app.services.ingestion.base import BaseConnector

logger = logging.getLogger(__name__)

STRING_API_BASE = "https://string-db.org/api"

# Minimum interaction score (0-1000). 400 = medium confidence (standard practice)
MIN_INTERACTION_SCORE = 400
# Batch size for STRING queries (max 2000)
STRING_BATCH_SIZE = 2000


class STRINGConnector(BaseConnector):
    """Ingests protein-protein interaction data from STRING for all drug targets."""

    def __init__(
        self,
        db_session: AsyncSession | None = None,
        http_client: httpx.AsyncClient | None = None,
        **kwargs,
    ):
        super().__init__(
            db_session=db_session,
            http_client=http_client,
            rate_limit=1.0,  # 1 batch request per second
            batch_size=500,
            timeout=120.0,  # STRING batch queries can be slow
            **kwargs,
        )

    def get_source_name(self) -> str:
        return "string"

    def transform_record(self, raw_record: dict[str, Any]) -> dict[str, Any]:
        """Not used — connector writes directly."""
        return raw_record

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    async def fetch_data(self, session: AsyncSession) -> list[dict[str, Any]]:
        """Fetch STRING interactions for all drug targets."""
        client = await self._get_client()
        owns_client = self._external_client is None

        try:
            # Get all unique gene symbols from targets
            result = await session.execute(
                select(Target.gene_symbol, Target.uniprot_id).where(
                    Target.gene_symbol.isnot(None)
                )
            )
            targets = result.all()
            gene_symbols = [t[0] for t in targets if t[0]]
            # Build gene → UniProt mapping
            gene_to_uniprot: dict[str, str] = {
                t[0].upper(): t[1] for t in targets if t[0] and t[1]
            }

            logger.info(
                "Fetching STRING interactions for %d targets", len(gene_symbols)
            )

            # Batch query STRING
            all_interactions: list[dict[str, Any]] = []
            for i in range(0, len(gene_symbols), STRING_BATCH_SIZE):
                batch = gene_symbols[i : i + STRING_BATCH_SIZE]
                try:
                    interactions = await self._fetch_batch_interactions(
                        client, batch
                    )
                    all_interactions.extend(interactions)
                    logger.info(
                        "STRING batch %d/%d: %d interactions",
                        i // STRING_BATCH_SIZE + 1,
                        (len(gene_symbols) + STRING_BATCH_SIZE - 1) // STRING_BATCH_SIZE,
                        len(interactions),
                    )
                except Exception as exc:
                    self.record_error(
                        f"batch_{i}", exc, record_id=f"batch_{i}-{i+STRING_BATCH_SIZE}"
                    )

            # Deduplicate: store each pair once (alphabetically sorted)
            seen_pairs: set[tuple[str, str]] = set()
            unique_records: list[dict[str, Any]] = []

            for interaction in all_interactions:
                prot_a = interaction.get("preferredName_A", "")
                prot_b = interaction.get("preferredName_B", "")

                if not prot_a or not prot_b or prot_a == prot_b:
                    continue

                # Sort alphabetically to deduplicate bidirectional interactions
                if prot_a > prot_b:
                    prot_a, prot_b = prot_b, prot_a

                pair_key = (prot_a, prot_b)
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                score = interaction.get("score", 0)
                if score < MIN_INTERACTION_SCORE:
                    continue

                # Map gene symbols to UniProt IDs
                uniprot_a = gene_to_uniprot.get(prot_a.upper(), prot_a)
                uniprot_b = gene_to_uniprot.get(prot_b.upper(), prot_b)

                unique_records.append({
                    "protein_a_uniprot": uniprot_a,
                    "protein_b_uniprot": uniprot_b,
                    "interaction_score": score / 1000.0,  # Normalize to 0-1
                    "experimental_score": interaction.get("escore", 0) / 1000.0
                    if interaction.get("escore") else None,
                    "database_score": interaction.get("dscore", 0) / 1000.0
                    if interaction.get("dscore") else None,
                    "textmining_score": interaction.get("tscore", 0) / 1000.0
                    if interaction.get("tscore") else None,
                    "source": "string",
                })

            logger.info(
                "Storing %d unique interactions (from %d raw)",
                len(unique_records), len(all_interactions),
            )

            # Batch upsert interactions
            count = await self.batch_upsert_composite(
                session,
                ProteinInteraction,
                unique_records,
                conflict_columns=["protein_a_uniprot", "protein_b_uniprot"],
                update_columns=[
                    "interaction_score", "experimental_score",
                    "database_score", "textmining_score", "source",
                ],
            )
            await session.commit()

            self._records_processed = count
            return []

        finally:
            if owns_client:
                await client.aclose()

    # ------------------------------------------------------------------
    # STRING batch query
    # ------------------------------------------------------------------

    async def _fetch_batch_interactions(
        self,
        client: httpx.AsyncClient,
        gene_symbols: list[str],
    ) -> list[dict[str, Any]]:
        """Fetch interactions for a batch of proteins from STRING.

        Uses the TSV endpoint for efficient parsing.
        """
        identifiers = "%0d".join(gene_symbols)

        await self._rate_limiter.acquire()
        resp = await client.post(
            f"{STRING_API_BASE}/json/network",
            data={
                "identifiers": identifiers,
                "species": "9606",
                "required_score": str(MIN_INTERACTION_SCORE),
                "caller_identity": "pharma_nexus",
            },
            timeout=httpx.Timeout(120.0),
        )
        resp.raise_for_status()
        return resp.json()
