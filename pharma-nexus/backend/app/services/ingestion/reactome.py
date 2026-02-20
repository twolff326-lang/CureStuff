"""Reactome connector — detailed pathway hierarchy with reaction-level data.

Uses the bulk UniProt2Reactome TSV for efficient pathway-gene mapping,
plus the ContentService API for pathway hierarchy and metadata.

API base: https://reactome.org/ContentService
Bulk download: https://reactome.org/download/current/UniProt2Reactome.txt
Rate limit: ~3 req/sec
"""

import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pathway import Pathway, PathwayTarget
from app.models.target import Target
from app.services.ingestion.base import BaseConnector

logger = logging.getLogger(__name__)

REACTOME_API_BASE = "https://reactome.org/ContentService"
REACTOME_UNIPROT_TSV = "https://reactome.org/download/current/UniProt2Reactome.txt"
TAXONOMY_HUMAN = "9606"


class ReactomeConnector(BaseConnector):
    """Ingests Reactome pathway data with hierarchy and gene members."""

    def __init__(
        self,
        db_session: AsyncSession | None = None,
        http_client: httpx.AsyncClient | None = None,
        **kwargs,
    ):
        super().__init__(
            db_session=db_session,
            http_client=http_client,
            rate_limit=3.0,
            batch_size=500,
            timeout=60.0,
            **kwargs,
        )
        self._headers = {"Accept": "application/json"}
        # Cache: stId → db pathway id
        self._pathway_db_ids: dict[str, int] = {}

    def get_source_name(self) -> str:
        return "reactome"

    def transform_record(self, raw_record: dict[str, Any]) -> dict[str, Any]:
        """Not used — connector writes directly."""
        return raw_record

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    async def fetch_data(self, session: AsyncSession) -> list[dict[str, Any]]:
        """
        Phase 1: Get top-level pathways and build hierarchy
        Phase 2: Download bulk UniProt→Reactome mapping for gene members
        Phase 3: Create pathway_targets links
        """
        client = await self._get_client()
        owns_client = self._external_client is None

        try:
            target_cache = await self._build_target_cache(session)
            processed = 0

            # Phase 1: Top-level pathways + recursive hierarchy
            top_pathways = await self._fetch_top_level_pathways(client)
            logger.info("Found %d top-level Reactome pathways", len(top_pathways))

            for tp in top_pathways:
                try:
                    count = await self._process_pathway_tree(
                        client, session, tp, parent_db_id=None
                    )
                    processed += count
                    await session.commit()
                except Exception as exc:
                    self.record_error(
                        f"hierarchy_{tp.get('stId', '')}", exc,
                        record_id=tp.get("stId", ""),
                    )
                    await session.rollback()

            # Phase 2: Bulk UniProt→Reactome mapping
            pathway_genes = await self._fetch_uniprot_mapping(client, session)
            logger.info(
                "UniProt2Reactome mapping: %d pathway-gene pairs",
                sum(len(genes) for genes in pathway_genes.values()),
            )

            # Update pathway records with gene lists
            for stId, genes_info in pathway_genes.items():
                gene_symbols = list({g["gene_symbol"] for g in genes_info if g.get("gene_symbol")})
                uniprot_ids = list({g["uniprot_id"] for g in genes_info if g.get("uniprot_id")})

                # Update pathway genes JSONB
                result = await session.execute(
                    select(Pathway.id).where(
                        Pathway.source == "reactome",
                        Pathway.external_id == stId,
                    )
                )
                db_pathway_id = result.scalar_one_or_none()
                if db_pathway_id is None:
                    continue

                # Update genes array
                from sqlalchemy import update
                await session.execute(
                    update(Pathway)
                    .where(Pathway.id == db_pathway_id)
                    .values(genes=gene_symbols)
                )

                # Phase 3: Create pathway_targets links
                pt_records = []
                for gi in genes_info:
                    uniprot_id = gi.get("uniprot_id", "")
                    gene_sym = gi.get("gene_symbol", "")

                    # Try gene symbol first, then uniprot
                    target_id = target_cache.get(gene_sym.upper()) if gene_sym else None
                    if target_id is None and uniprot_id:
                        target_id = target_cache.get(f"UP:{uniprot_id}")

                    if target_id:
                        pt_records.append({
                            "pathway_id": db_pathway_id,
                            "target_id": target_id,
                            "role": "component",
                        })

                if pt_records:
                    await self.batch_upsert_composite(
                        session,
                        PathwayTarget,
                        pt_records,
                        conflict_columns=["pathway_id", "target_id"],
                        update_columns=["role"],
                    )
                    processed += len(pt_records)

            await session.commit()
            self._records_processed = processed
            return []

        finally:
            if owns_client:
                await client.aclose()

    # ------------------------------------------------------------------
    # Phase 1: Pathway hierarchy
    # ------------------------------------------------------------------

    async def _fetch_top_level_pathways(
        self, client: httpx.AsyncClient
    ) -> list[dict[str, Any]]:
        """Fetch top-level human pathways from Reactome."""
        resp = await self.http_get(
            client,
            f"{REACTOME_API_BASE}/data/pathways/top/{TAXONOMY_HUMAN}",
            headers=self._headers,
        )
        return resp.json()

    async def _process_pathway_tree(
        self,
        client: httpx.AsyncClient,
        session: AsyncSession,
        pathway: dict[str, Any],
        parent_db_id: int | None,
        depth: int = 0,
    ) -> int:
        """Recursively process a pathway and its children.

        Max depth of 4 to avoid excessive API calls.
        """
        if depth > 4:
            return 0

        stId = pathway.get("stId", "")
        display_name = pathway.get("displayName", "")

        if not stId:
            return 0

        # Determine category from top-level ancestor
        category = ""
        if depth == 0:
            category = display_name
        elif parent_db_id:
            # Inherit category from parent (top-level)
            result = await session.execute(
                select(Pathway.category).where(Pathway.id == parent_db_id)
            )
            category = result.scalar_one_or_none() or ""

        # Upsert pathway record
        pathway_record = {
            "source": "reactome",
            "external_id": stId,
            "name": display_name,
            "description": pathway.get("summation", [{}])[0].get("text", "")
            if pathway.get("summation") else "",
            "category": category,
            "genes": [],  # Will be filled in Phase 2
            "parent_pathway_id": parent_db_id,
        }

        await self.batch_upsert_composite(
            session,
            Pathway,
            [pathway_record],
            conflict_columns=["source", "external_id"],
            update_columns=["name", "description", "category", "parent_pathway_id"],
        )
        await session.flush()

        # Get the DB ID
        result = await session.execute(
            select(Pathway.id).where(
                Pathway.source == "reactome",
                Pathway.external_id == stId,
            )
        )
        db_id = result.scalar_one_or_none()
        if db_id is None:
            return 1

        self._pathway_db_ids[stId] = db_id
        processed = 1

        # Fetch children (contained events)
        try:
            resp = await self.http_get(
                client,
                f"{REACTOME_API_BASE}/data/pathway/{stId}/containedEvents",
                headers=self._headers,
            )
            children = resp.json()

            # Filter to only Pathway type (skip Reactions at deeper levels)
            child_pathways = [
                c for c in children
                if c.get("schemaClass") == "Pathway"
                or c.get("className") == "Pathway"
            ]

            for child in child_pathways:
                try:
                    count = await self._process_pathway_tree(
                        client, session, child, parent_db_id=db_id, depth=depth + 1
                    )
                    processed += count
                except Exception as exc:
                    self.record_error(
                        f"child_{child.get('stId', '')}",
                        exc,
                        record_id=child.get("stId", ""),
                    )

        except Exception as exc:
            self.record_error(f"children_{stId}", exc, record_id=stId)

        return processed

    # ------------------------------------------------------------------
    # Phase 2: Bulk UniProt → Reactome mapping
    # ------------------------------------------------------------------

    async def _fetch_uniprot_mapping(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> dict[str, list[dict[str, str]]]:
        """Download and parse UniProt2Reactome.txt bulk file.

        TSV columns: UniProt_ID, Reactome_ID, URL, Pathway_Name, Evidence_Code, Species
        Filter to Homo sapiens only.

        Returns: {reactome_stId: [{uniprot_id, gene_symbol}, ...]}
        """
        try:
            resp = await self.http_get(
                client, REACTOME_UNIPROT_TSV,
            )
        except Exception as exc:
            self.record_error("uniprot_mapping_download", exc)
            return {}

        pathway_genes: dict[str, list[dict[str, str]]] = {}

        for line in resp.text.split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 6:
                continue

            uniprot_id = parts[0].strip()
            reactome_id = parts[1].strip()
            species = parts[5].strip()

            if species != "Homo sapiens":
                continue

            if reactome_id not in pathway_genes:
                pathway_genes[reactome_id] = []

            pathway_genes[reactome_id].append({
                "uniprot_id": uniprot_id,
                "gene_symbol": "",
            })

        # Resolve UniProt IDs to gene symbols using our target data
        result = await session.execute(
            select(Target.uniprot_id, Target.gene_symbol).where(
                Target.uniprot_id.isnot(None)
            )
        )
        uniprot_to_gene: dict[str, str] = {
            uid: gsym for uid, gsym in result.all() if uid and gsym
        }

        resolved = 0
        for genes_list in pathway_genes.values():
            for entry in genes_list:
                gene_sym = uniprot_to_gene.get(entry["uniprot_id"], "")
                if gene_sym:
                    entry["gene_symbol"] = gene_sym
                    resolved += 1

        logger.info(
            "Resolved %d/%d UniProt→gene symbol mappings for Reactome pathways",
            resolved,
            sum(len(gl) for gl in pathway_genes.values()),
        )

        return pathway_genes

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _build_target_cache(
        self, session: AsyncSession
    ) -> dict[str, int]:
        """Build both gene_symbol and uniprot_id caches.

        Returns dict where keys are either GENE_SYMBOL (uppercase)
        or "UP:{uniprot_id}" for UniProt-based lookups.
        """
        result = await session.execute(
            select(Target.gene_symbol, Target.uniprot_id, Target.id)
        )
        cache: dict[str, int] = {}
        for gene_sym, uniprot_id, target_id in result.all():
            if gene_sym:
                cache[gene_sym.upper()] = target_id
            if uniprot_id:
                cache[f"UP:{uniprot_id}"] = target_id
        return cache
