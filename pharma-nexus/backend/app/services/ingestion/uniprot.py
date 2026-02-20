"""UniProt connector — enriches target records with detailed protein data.

Fetches via the UniProt REST API:
  - Function descriptions
  - Subcellular locations
  - Protein family/class
  - Ensembl Gene ID cross-references (needed by OpenTargets)
  - Gene name verification

API base: https://rest.uniprot.org
No API key required. Rate limit: ~10 req/sec with batching.
Batch up to 100 accessions per request (URL length limits).
"""

import logging
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.target import Target
from app.services.ingestion.base import BaseConnector

logger = logging.getLogger(__name__)

UNIPROT_API_BASE = "https://rest.uniprot.org"
UNIPROT_BATCH_SIZE = 100  # Max accessions per query (URL length)

# Protein class keywords to extract
PROTEIN_CLASS_KEYWORDS = {
    "Kinase": ["kinase", "phosphotransferase"],
    "GPCR": ["g protein-coupled receptor", "gpcr", "rhodopsin-like"],
    "Ion channel": ["ion channel", "voltage-gated", "ligand-gated channel"],
    "Nuclear receptor": ["nuclear receptor", "steroid receptor"],
    "Protease": ["protease", "peptidase", "proteinase"],
    "Phosphatase": ["phosphatase"],
    "Transporter": ["transporter", "solute carrier", "abc transporter"],
    "Transcription factor": ["transcription factor", "zinc finger protein"],
    "Epigenetic regulator": [
        "histone", "methyltransferase", "acetyltransferase",
        "deacetylase", "demethylase",
    ],
    "Ubiquitin system": ["ubiquitin", "e3 ligase", "deubiquitinase"],
    "Cytokine": ["cytokine", "interleukin", "chemokine"],
    "Growth factor": ["growth factor"],
    "Receptor tyrosine kinase": ["receptor tyrosine kinase", "receptor protein-tyrosine kinase"],
}


class UniProtConnector(BaseConnector):
    """Enriches target records with UniProt protein data and Ensembl IDs."""

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
        self._headers = {"Accept": "application/json"}

    def get_source_name(self) -> str:
        return "uniprot"

    def transform_record(self, raw_record: dict[str, Any]) -> dict[str, Any]:
        """Not used — connector updates existing records."""
        return raw_record

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    async def fetch_data(self, session: AsyncSession) -> list[dict[str, Any]]:
        """Enrich all target records with UniProt data."""
        client = await self._get_client()
        owns_client = self._external_client is None

        try:
            # Get all targets with UniProt IDs
            result = await session.execute(
                select(Target.id, Target.uniprot_id).where(
                    Target.uniprot_id.isnot(None)
                )
            )
            targets = result.all()
            logger.info("Enriching %d targets with UniProt data", len(targets))

            await self.set_total_expected(session, len(targets))

            processed = 0
            uniprot_ids = [t[1] for t in targets]

            # Batch query UniProt
            for i in range(0, len(uniprot_ids), UNIPROT_BATCH_SIZE):
                batch = uniprot_ids[i : i + UNIPROT_BATCH_SIZE]
                try:
                    enriched = await self._fetch_batch_proteins(client, batch)
                    count = await self._update_targets(session, enriched)
                    processed += count
                    logger.info(
                        "UniProt batch %d/%d: enriched %d targets",
                        i // UNIPROT_BATCH_SIZE + 1,
                        (len(uniprot_ids) + UNIPROT_BATCH_SIZE - 1) // UNIPROT_BATCH_SIZE,
                        count,
                    )
                except Exception as exc:
                    self.record_error(
                        f"batch_{i}", exc,
                        record_id=f"batch_{i}-{i+UNIPROT_BATCH_SIZE}",
                    )

            await session.commit()

            # Log warnings for targets without UniProt IDs
            no_uniprot = await session.execute(
                select(Target.gene_symbol).where(Target.uniprot_id.is_(None))
            )
            missing = no_uniprot.scalars().all()
            if missing:
                logger.warning(
                    "%d targets lack UniProt IDs: %s...",
                    len(missing), missing[:10],
                )

            self._records_processed = processed
            return []

        finally:
            if owns_client:
                await client.aclose()

    # ------------------------------------------------------------------
    # UniProt batch query
    # ------------------------------------------------------------------

    async def _fetch_batch_proteins(
        self,
        client: httpx.AsyncClient,
        uniprot_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Fetch protein data for a batch of UniProt accessions."""
        # Build query: accession:P00533 OR accession:P04626 OR ...
        query = " OR ".join(f"accession:{uid}" for uid in uniprot_ids)

        resp = await self.http_get(
            client,
            f"{UNIPROT_API_BASE}/uniprotkb/search",
            params={
                "query": query,
                "fields": (
                    "accession,gene_names,protein_name,organism_name,"
                    "cc_function,cc_subcellular_location,protein_families,"
                    "xref_ensembl"
                ),
                "format": "json",
                "size": str(len(uniprot_ids)),
            },
            headers=self._headers,
        )
        data = resp.json()
        return data.get("results", [])

    # ------------------------------------------------------------------
    # Update targets
    # ------------------------------------------------------------------

    async def _update_targets(
        self,
        session: AsyncSession,
        proteins: list[dict[str, Any]],
    ) -> int:
        """Update target records with enriched UniProt data."""
        updated = 0

        for protein in proteins:
            accession = protein.get("primaryAccession", "")
            if not accession:
                continue

            # Extract fields
            gene_symbol = self._extract_gene_symbol(protein)
            gene_name = self._extract_gene_name(protein)
            function_desc = self._extract_function(protein)
            subcellular_loc = self._extract_subcellular_location(protein)
            protein_class = self._classify_protein(protein)
            ensembl_id = self._extract_ensembl_id(protein)

            # Build update values (only non-empty)
            values: dict[str, Any] = {}
            if gene_symbol:
                values["gene_symbol"] = gene_symbol
            if gene_name:
                values["gene_name"] = gene_name
            if function_desc:
                values["function_description"] = function_desc
            if subcellular_loc:
                values["subcellular_location"] = subcellular_loc
            if protein_class:
                values["protein_class"] = protein_class
            if ensembl_id:
                values["ensembl_gene_id"] = ensembl_id

            if values:
                await session.execute(
                    update(Target)
                    .where(Target.uniprot_id == accession)
                    .values(**values)
                )
                updated += 1

        return updated

    # ------------------------------------------------------------------
    # Field extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_gene_symbol(protein: dict) -> str:
        """Extract primary gene symbol."""
        genes = protein.get("genes", [])
        if genes:
            gene_name_obj = genes[0].get("geneName", {})
            return gene_name_obj.get("value", "")
        return ""

    @staticmethod
    def _extract_gene_name(protein: dict) -> str:
        """Extract full gene/protein name."""
        desc = protein.get("proteinDescription", {})
        rec_name = desc.get("recommendedName", {})
        if rec_name:
            full_name = rec_name.get("fullName", {})
            return full_name.get("value", "")
        # Fallback to submitted name
        sub_names = desc.get("submissionNames", [])
        if sub_names:
            return sub_names[0].get("fullName", {}).get("value", "")
        return ""

    @staticmethod
    def _extract_function(protein: dict) -> str:
        """Extract function description from comments."""
        comments = protein.get("comments", [])
        for comment in comments:
            if comment.get("commentType") == "FUNCTION":
                texts = comment.get("texts", [])
                if texts:
                    return texts[0].get("value", "")
        return ""

    @staticmethod
    def _extract_subcellular_location(protein: dict) -> str:
        """Extract subcellular location."""
        comments = protein.get("comments", [])
        for comment in comments:
            if comment.get("commentType") == "SUBCELLULAR LOCATION":
                locations = comment.get("subcellularLocations", [])
                loc_names = []
                for loc in locations:
                    loc_val = loc.get("location", {}).get("value", "")
                    if loc_val:
                        loc_names.append(loc_val)
                return "; ".join(loc_names)
        return ""

    @staticmethod
    def _extract_ensembl_id(protein: dict) -> str:
        """Extract Ensembl Gene ID from cross-references."""
        xrefs = protein.get("uniProtKBCrossReferences", [])
        for xref in xrefs:
            if xref.get("database") == "Ensembl":
                # Ensembl xrefs have properties with gene ID
                props = xref.get("properties", [])
                for prop in props:
                    if prop.get("key") == "GeneId":
                        return prop.get("value", "")
                # Some entries have the gene ID directly in id field
                xref_id = xref.get("id", "")
                if xref_id.startswith("ENSG"):
                    return xref_id
        return ""

    @staticmethod
    def _classify_protein(protein: dict) -> str:
        """Classify protein into a drug-relevant class based on keywords."""
        # Collect searchable text
        search_text = ""

        # Protein families
        comments = protein.get("comments", [])
        for comment in comments:
            if comment.get("commentType") == "SIMILARITY":
                texts = comment.get("texts", [])
                if texts:
                    search_text += " " + texts[0].get("value", "")

        # Protein description
        desc = protein.get("proteinDescription", {})
        rec_name = desc.get("recommendedName", {})
        if rec_name:
            search_text += " " + rec_name.get("fullName", {}).get("value", "")

        # Keywords
        keywords = protein.get("keywords", [])
        for kw in keywords:
            search_text += " " + kw.get("name", "")

        search_lower = search_text.lower()

        # Check classes in priority order (more specific first)
        for class_name, keywords_list in PROTEIN_CLASS_KEYWORDS.items():
            for keyword in keywords_list:
                if keyword in search_lower:
                    return class_name

        return ""
