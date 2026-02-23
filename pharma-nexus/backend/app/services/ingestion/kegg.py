"""KEGG Pathway connector — curated biological pathway maps.

Fetches via the KEGG REST API (no auth required):
  - All ~340 human pathway IDs and names
  - Gene members for each pathway (parsed from KEGG flat file format)
  - Pathway category/class information

API base: https://rest.kegg.jp
Rate limit: 1 req/sec (academic resource — be polite)

KEGG flat file format notes:
  - Section headers start at column 1 (ENTRY, NAME, GENE, CLASS, etc.)
  - Continuation lines are indented with spaces
  - GENE section format: {entrez_id}  {symbol}; {description} [KO:...] [EC:...]
  - Records end with ///
"""

import logging
import re
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pathway import Pathway, PathwayTarget
from app.models.target import Target
from app.services.ingestion.base import BaseConnector

logger = logging.getLogger(__name__)

KEGG_API_BASE = "https://rest.kegg.jp"

# Cancer-relevant KEGG pathway IDs and category keywords
CANCER_PATHWAY_IDS = {
    "hsa05200",  # Pathways in cancer
    "hsa05210", "hsa05211", "hsa05212", "hsa05213", "hsa05214",
    "hsa05215", "hsa05216", "hsa05217", "hsa05218", "hsa05219",
    "hsa05220", "hsa05221", "hsa05222", "hsa05223", "hsa05224",
    "hsa05225", "hsa05226", "hsa05230", "hsa05231", "hsa05235",
}
CANCER_RELEVANT_CATEGORIES = {
    "signal transduction", "cell growth and death", "immune system",
    "cancer", "cellular community", "cell motility",
    "transport and catabolism",
}


def parse_kegg_flat_file(text: str) -> dict[str, Any]:
    """Parse a KEGG flat file entry into structured data.

    KEGG flat files use a section-based format:
      - Section headers start at column 1 (e.g., ENTRY, NAME, GENE)
      - Continuation lines are indented with spaces (12 chars typically)
      - The record ends with ///

    Returns dict with keys: entry, name, description, category, genes.
    """
    sections: dict[str, list[str]] = {}
    current_section = ""

    for line in text.split("\n"):
        if line.startswith("///"):
            break
        if not line or not line.strip():
            continue

        # Check if this is a section header (starts at column 1, not with space)
        if line[0] != " " and not line[0].isdigit():
            # Section header — extract the keyword (first word)
            parts = line.split(None, 1)
            if parts:
                current_section = parts[0].strip()
                remainder = parts[1].strip() if len(parts) > 1 else ""
                if current_section not in sections:
                    sections[current_section] = []
                if remainder:
                    sections[current_section].append(remainder)
        else:
            # Continuation line
            if current_section:
                sections.setdefault(current_section, []).append(line.strip())

    # Extract structured data
    result: dict[str, Any] = {
        "entry": "",
        "name": "",
        "description": "",
        "category": "",
        "genes": [],
    }

    # ENTRY
    if "ENTRY" in sections and sections["ENTRY"]:
        entry_line = sections["ENTRY"][0]
        entry_match = re.match(r"(\S+)", entry_line)
        if entry_match:
            result["entry"] = entry_match.group(1)

    # NAME — remove " - Homo sapiens (human)" suffix
    if "NAME" in sections and sections["NAME"]:
        name = " ".join(sections["NAME"])
        name = re.sub(r"\s*-\s*Homo sapiens.*$", "", name).strip()
        result["name"] = name

    # DESCRIPTION
    if "DESCRIPTION" in sections:
        result["description"] = " ".join(sections["DESCRIPTION"])

    # CLASS — pathway category
    if "CLASS" in sections and sections["CLASS"]:
        result["category"] = " ".join(sections["CLASS"])

    # GENE section — the most complex part
    if "GENE" in sections:
        for gene_line in sections["GENE"]:
            gene_info = _parse_gene_line(gene_line)
            if gene_info:
                result["genes"].append(gene_info)

    return result


def _parse_gene_line(line: str) -> dict[str, str] | None:
    """Parse a single KEGG GENE line.

    Format: {entrez_id}  {symbol}; {description} [KO:K12407] [EC:2.7.1.2]
    Some lines may have just: {entrez_id}  {symbol}
    """
    line = line.strip()
    if not line:
        return None

    # Match: number(s) followed by spaces, then gene symbol
    match = re.match(r"(\d+)\s+(\S+?)(?:;|\s|$)(.*)", line)
    if not match:
        return None

    entrez_id = match.group(1)
    gene_symbol = match.group(2).rstrip(";")
    description = match.group(3).strip() if match.group(3) else ""

    # Clean description — remove [KO:...] and [EC:...] tags
    description = re.sub(r"\[KO:\S+\]", "", description)
    description = re.sub(r"\[EC:\S+\]", "", description)
    description = description.strip().rstrip(";").strip()

    return {
        "entrez_id": entrez_id,
        "gene_symbol": gene_symbol,
        "description": description,
    }


class KEGGConnector(BaseConnector):
    """Ingests KEGG human pathway data (~340 pathways with gene members)."""

    def __init__(
        self,
        db_session: AsyncSession | None = None,
        http_client: httpx.AsyncClient | None = None,
        **kwargs,
    ):
        super().__init__(
            db_session=db_session,
            http_client=http_client,
            rate_limit=1.0,  # KEGG: 1 req/sec — academic resource
            batch_size=500,
            **kwargs,
        )

    def get_source_name(self) -> str:
        return "kegg"

    def transform_record(self, raw_record: dict[str, Any]) -> dict[str, Any]:
        """Not used — connector writes directly."""
        return raw_record

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    async def fetch_data(self, session: AsyncSession) -> list[dict[str, Any]]:
        """Phase 1: list pathways, Phase 2: fetch details + genes for each."""
        client = await self._get_client()
        owns_client = self._external_client is None

        try:
            # Build gene_symbol → target_id cache
            target_cache = await self._build_target_cache(session)

            # Phase 1: List all human pathways
            pathway_list = await self._list_human_pathways(client)
            logger.info("Found %d human pathways in KEGG", len(pathway_list))

            await self.set_total_expected(session, len(pathway_list))

            processed = 0

            # Phase 2: Fetch detail for each pathway
            for idx, (pathway_id, pathway_name) in enumerate(pathway_list, 1):
                try:
                    count = await self._fetch_pathway_detail(
                        client, session, pathway_id, pathway_name, target_cache
                    )
                    processed += count
                except Exception as exc:
                    self.record_error(
                        f"pathway_{pathway_id}", exc, record_id=pathway_id
                    )

                # Flush progress every 10 pathways
                self._records_processed = idx
                if idx % 10 == 0:
                    await self._flush_progress(session)

            await session.commit()
            self._records_processed = len(pathway_list)
            await self._flush_progress(session)
            return []

        finally:
            if owns_client:
                await client.aclose()

    # ------------------------------------------------------------------
    # Phase 1: List pathways
    # ------------------------------------------------------------------

    async def _list_human_pathways(
        self, client: httpx.AsyncClient
    ) -> list[tuple[str, str]]:
        """Fetch list of all human pathways from KEGG.

        Returns list of (pathway_id, pathway_name) tuples.
        Response format: tab-delimited text, one pathway per line.
        """
        resp = await self.http_get(client, f"{KEGG_API_BASE}/list/pathway/hsa")
        text = resp.text

        pathways = []
        for line in text.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            if len(parts) == 2:
                pathway_id = parts[0].strip()
                name = parts[1].strip()
                # Remove " - Homo sapiens (human)" suffix
                name = re.sub(r"\s*-\s*Homo sapiens.*$", "", name).strip()
                # Normalize ID: "path:hsa00010" → "hsa00010"
                pathway_id = pathway_id.replace("path:", "")
                pathways.append((pathway_id, name))

        return pathways

    # ------------------------------------------------------------------
    # Phase 2: Fetch pathway details
    # ------------------------------------------------------------------

    async def _fetch_pathway_detail(
        self,
        client: httpx.AsyncClient,
        session: AsyncSession,
        pathway_id: str,
        pathway_name: str,
        target_cache: dict[str, int],
    ) -> int:
        """Fetch KEGG flat file for one pathway and upsert data."""
        try:
            resp = await self.http_get(client, f"{KEGG_API_BASE}/get/{pathway_id}")
        except Exception as exc:
            self.record_error(f"fetch_{pathway_id}", exc, record_id=pathway_id)
            return 0

        parsed = parse_kegg_flat_file(resp.text)
        gene_symbols = [g["gene_symbol"] for g in parsed["genes"]]

        # Upsert pathway record
        pathway_record = {
            "source": "kegg",
            "external_id": pathway_id,
            "name": parsed.get("name") or pathway_name,
            "description": parsed.get("description", ""),
            "category": parsed.get("category", ""),
            "genes": gene_symbols,
        }

        await self.batch_upsert_composite(
            session,
            Pathway,
            [pathway_record],
            conflict_columns=["source", "external_id"],
            update_columns=["name", "description", "category", "genes"],
        )
        await session.flush()

        # Look up the pathway ID we just upserted
        result = await session.execute(
            select(Pathway.id).where(
                Pathway.source == "kegg",
                Pathway.external_id == pathway_id,
            )
        )
        db_pathway_id = result.scalar_one_or_none()
        if db_pathway_id is None:
            return 1

        # Create pathway_targets links for genes in our targets table
        pt_records = []
        for gene_sym in gene_symbols:
            target_id = target_cache.get(gene_sym.upper())
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

        return 1 + len(pt_records)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _build_target_cache(
        self, session: AsyncSession
    ) -> dict[str, int]:
        """Build gene_symbol → target.id cache from all targets."""
        result = await session.execute(
            select(Target.gene_symbol, Target.id)
        )
        return {row[0].upper(): row[1] for row in result.all() if row[0]}
