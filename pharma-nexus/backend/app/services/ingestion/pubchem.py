"""PubChem data ingestion connector.

PubChem PUG REST API — completely free, no API key required.
Primary source for: bioassay data, chemical properties (SMILES, InChI, MW).
Fallback/enrichment source for: drug compound data.

Rate limit: 5 requests/second.
API base: https://pubchem.ncbi.nlm.nih.gov/rest/pug
"""

import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.drug import Drug
from app.models.evidence import Bioassay
from app.models.target import Target
from app.services.ingestion.base import BaseConnector

logger = logging.getLogger(__name__)

PUBCHEM_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"


class PubChemConnector(BaseConnector):
    """Connector for PubChem compound and bioassay data.

    Two-phase ingestion:
      1. Enrich existing drugs with PubChem chemical property data.
      2. Fetch bioassay activity data for known drug targets.
    """

    def __init__(
        self,
        db_session: AsyncSession | None = None,
        http_client: httpx.AsyncClient | None = None,
        rate_limit: float = 5.0,
        **kwargs: Any,
    ):
        super().__init__(
            db_session=db_session,
            http_client=http_client,
            rate_limit=rate_limit,
            **kwargs,
        )

    def get_source_name(self) -> str:
        return "pubchem"

    # ------------------------------------------------------------------
    # Main fetch orchestrator
    # ------------------------------------------------------------------

    async def fetch_data(self, session: AsyncSession) -> list[dict[str, Any]]:
        """Run full PubChem ingestion: drug enrichment + bioassays."""
        client = await self._get_client()
        owns_client = self._external_client is None
        total = 0

        try:
            # Phase 1: Enrich existing drugs with chemical properties
            enriched = await self._enrich_existing_drugs(session, client)
            total += enriched

            # Phase 2: Fetch bioassay data for known targets
            bioassays = await self._fetch_bioassays_for_targets(session, client)
            total += bioassays

        finally:
            if owns_client:
                await client.aclose()

        self._records_processed = total
        return []

    def transform_record(self, raw_record: dict[str, Any]) -> dict[str, Any]:
        """Transform a PubChem API response into our schema."""
        return raw_record

    # ------------------------------------------------------------------
    # Phase 1: Enrich existing drugs with PubChem chemical data
    # ------------------------------------------------------------------

    async def _enrich_existing_drugs(
        self, session: AsyncSession, client: httpx.AsyncClient
    ) -> int:
        """Look up existing drugs in PubChem and fill in missing chemical data."""
        result = await session.execute(
            select(Drug.id, Drug.name, Drug.drugbank_id, Drug.smiles)
            .where(Drug.status == "approved")
            .order_by(Drug.id)
        )
        drugs = result.all()

        if not drugs:
            logger.info("No existing drugs to enrich from PubChem")
            return 0

        await self.set_total_expected(session, len(drugs))

        enrichment_records: list[dict[str, Any]] = []
        for drug_id, drug_name, drugbank_id, existing_smiles in drugs:
            try:
                props = await self._fetch_compound_properties(
                    client, drug_name
                )
                if props is None:
                    continue

                record = {
                    "drugbank_id": drugbank_id,
                    "name": drug_name,
                    "molecular_formula": props.get("MolecularFormula"),
                    "smiles": props.get("CanonicalSMILES") or existing_smiles,
                    "inchi_key": props.get("InChIKey"),
                }
                enrichment_records.append(record)

            except Exception as exc:
                self.record_error(
                    "enrich_drug", exc, record_id=drugbank_id
                )

            # Commit in batches
            if len(enrichment_records) >= self._batch_size:
                await self.batch_upsert(
                    session, Drug, enrichment_records,
                    conflict_column="drugbank_id",
                    update_columns=["molecular_formula", "smiles", "inchi_key"],
                )
                await session.commit()
                enrichment_records.clear()

        # Final batch
        if enrichment_records:
            await self.batch_upsert(
                session, Drug, enrichment_records,
                conflict_column="drugbank_id",
                update_columns=["molecular_formula", "smiles", "inchi_key"],
            )
            await session.commit()

        logger.info("Enriched %d drugs with PubChem data", len(drugs))
        return len(drugs)

    async def _fetch_compound_properties(
        self, client: httpx.AsyncClient, drug_name: str
    ) -> dict[str, Any] | None:
        """Fetch chemical properties for a compound by name."""
        url = (
            f"{PUBCHEM_BASE}/compound/name/{drug_name}/property/"
            "MolecularFormula,CanonicalSMILES,InChIKey,MolecularWeight/"
            "JSON"
        )
        try:
            resp = await self.http_get(client, url)
            data = resp.json()
            props = data.get("PropertyTable", {}).get("Properties", [])
            return props[0] if props else None
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    # ------------------------------------------------------------------
    # Phase 2: Fetch bioassay data for known targets
    # ------------------------------------------------------------------

    async def _fetch_bioassays_for_targets(
        self, session: AsyncSession, client: httpx.AsyncClient
    ) -> int:
        """Fetch PubChem bioassay results for gene targets in our database."""
        result = await session.execute(
            select(Target.id, Target.gene_symbol, Target.uniprot_id)
            .where(Target.gene_symbol != "")
            .where(Target.gene_symbol.isnot(None))
            .order_by(Target.id)
        )
        targets = result.all()

        if not targets:
            logger.info("No targets found for bioassay lookup")
            return 0

        # Update total: current progress + targets to process
        await self.set_total_expected(
            session, self._records_processed + len(targets),
        )

        total_bioassays = 0
        # Build a mapping of drug name/CID -> drug_id for linking
        drug_map = await self._build_drug_lookup(session)

        for target_id, gene_symbol, uniprot_id in targets:
            try:
                aids = await self._get_assay_ids_for_gene(
                    client, gene_symbol
                )
                if not aids:
                    continue

                # Process up to 20 assays per target to avoid overloading
                for aid in aids[:20]:
                    try:
                        bioassay_records = await self._fetch_assay_results(
                            client, aid, target_id, drug_map
                        )
                        if bioassay_records:
                            await self.batch_insert_no_conflict(
                                session, Bioassay, bioassay_records
                            )
                            total_bioassays += len(bioassay_records)
                    except Exception as exc:
                        self.record_error(
                            "fetch_assay_results", exc,
                            record_id=f"AID:{aid}",
                        )

                await session.commit()

            except Exception as exc:
                self.record_error(
                    "fetch_bioassays_for_target", exc,
                    record_id=f"{gene_symbol}:{uniprot_id}",
                )

        logger.info(
            "Fetched %d bioassay records for %d targets",
            total_bioassays, len(targets),
        )
        return total_bioassays

    async def _get_assay_ids_for_gene(
        self, client: httpx.AsyncClient, gene_symbol: str
    ) -> list[int]:
        """Get PubChem assay IDs for a given gene target."""
        url = f"{PUBCHEM_BASE}/assay/target/genesymbol/{gene_symbol}/aids/JSON"
        try:
            resp = await self.http_get(client, url)
            data = resp.json()
            aids = (
                data.get("InformationList", {})
                .get("Information", [{}])[0]
                .get("AID", [])
            )
            return aids
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return []
            raise

    async def _fetch_assay_results(
        self,
        client: httpx.AsyncClient,
        aid: int,
        target_id: int,
        drug_map: dict[str, int],
    ) -> list[dict[str, Any]]:
        """Fetch concise bioassay results for a specific assay."""
        url = f"{PUBCHEM_BASE}/assay/aid/{aid}/concise/JSON"
        try:
            resp = await self.http_get(client, url)
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return []
            raise

        table = data.get("Table", {})
        columns = table.get("Columns", {}).get("Column", [])
        rows = table.get("Row", [])

        if not columns or not rows:
            return []

        # Map column names to indices
        col_idx = {name: i for i, name in enumerate(columns)}
        cid_idx = col_idx.get("CID")
        activity_idx = col_idx.get("Activity Outcome")
        activity_val_idx = col_idx.get("Activity Value [uM]")
        activity_name_idx = col_idx.get("Activity Name")

        # Batch-resolve CIDs from active rows to drug names for linking.
        # Collect unique CIDs that don't have a direct PC{CID} match.
        active_cids_needing_lookup: set[str] = set()
        for row in rows:
            cells = row.get("Cell", [])
            if not cells:
                continue
            cid = str(cells[cid_idx]) if cid_idx is not None and cid_idx < len(cells) else None
            outcome = cells[activity_idx] if activity_idx is not None and activity_idx < len(cells) else None
            if outcome and str(outcome).lower() != "active":
                continue
            if cid and f"PC{cid}" not in drug_map:
                active_cids_needing_lookup.add(cid)

        # Batch lookup: fetch compound titles for unresolved CIDs (max 100 at a time)
        cid_to_drug_id: dict[str, int] = {}
        cids_list = list(active_cids_needing_lookup)[:100]  # Cap to avoid huge requests
        if cids_list:
            try:
                cids_param = ",".join(cids_list)
                title_url = f"{PUBCHEM_BASE}/compound/cid/{cids_param}/property/Title/JSON"
                title_resp = await self.http_get(client, title_url)
                title_data = title_resp.json()
                for prop in title_data.get("PropertyTable", {}).get("Properties", []):
                    pcid = str(prop.get("CID", ""))
                    title = prop.get("Title", "")
                    if title:
                        drug_id = drug_map.get(f"NAME:{title.lower()}")
                        if drug_id:
                            cid_to_drug_id[pcid] = drug_id
            except Exception:
                pass  # Name-based linking is best-effort

        records = []
        for row in rows:
            cells = row.get("Cell", [])
            if not cells:
                continue

            try:
                cid = str(cells[cid_idx]) if cid_idx is not None else None
                outcome = (
                    cells[activity_idx] if activity_idx is not None else None
                )

                # Only record active results
                if outcome and str(outcome).lower() != "active":
                    continue

                value_raw = (
                    cells[activity_val_idx]
                    if activity_val_idx is not None
                    else None
                )
                activity_value = None
                if value_raw is not None:
                    try:
                        activity_value = float(value_raw)
                    except (ValueError, TypeError):
                        pass

                activity_name = (
                    cells[activity_name_idx]
                    if activity_name_idx is not None
                    else None
                )

                # Try to link CID to existing drug (direct match, then name-based)
                drug_id = None
                if cid:
                    drug_id = drug_map.get(f"PC{cid}") or cid_to_drug_id.get(cid)

                records.append({
                    "pubchem_aid": str(aid),
                    "target_id": target_id,
                    "drug_id": drug_id,
                    "activity_type": str(activity_name)[:20] if activity_name else None,
                    "activity_value": activity_value,
                    "activity_unit": "uM",
                    "activity_outcome": "active",
                    "source": "pubchem",
                })

            except (IndexError, TypeError) as exc:
                self.record_error(
                    "parse_assay_row", exc,
                    record_id=f"AID:{aid}",
                )

        return records

    # ------------------------------------------------------------------
    # Compound-gene association enrichment
    # ------------------------------------------------------------------

    async def fetch_compound_gene_associations(
        self,
        session: AsyncSession,
        client: httpx.AsyncClient,
        cid: int,
    ) -> list[dict[str, Any]]:
        """Fetch all bioactivity for a compound (compound-gene associations)."""
        url = f"{PUBCHEM_BASE}/compound/cid/{cid}/assaysummary/JSON"
        try:
            resp = await self.http_get(client, url)
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return []
            raise

        table = data.get("Table", {})
        columns = table.get("Columns", {}).get("Column", [])
        rows = table.get("Row", [])

        if not columns or not rows:
            return []

        col_idx = {name: i for i, name in enumerate(columns)}
        results = []
        for row in rows:
            cells = row.get("Cell", [])
            gene_idx = col_idx.get("Target GeneSymbol")
            aid_idx = col_idx.get("AID")
            outcome_idx = col_idx.get("Activity Outcome")

            if gene_idx is None or not cells:
                continue

            try:
                gene = cells[gene_idx] if gene_idx < len(cells) else None
                aid_val = cells[aid_idx] if aid_idx is not None and aid_idx < len(cells) else None
                outcome = cells[outcome_idx] if outcome_idx is not None and outcome_idx < len(cells) else None

                if gene and outcome and str(outcome).lower() == "active":
                    results.append({
                        "cid": cid,
                        "gene_symbol": str(gene),
                        "aid": str(aid_val) if aid_val else None,
                        "outcome": "active",
                    })
            except (IndexError, TypeError):
                continue

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _build_drug_lookup(
        self, session: AsyncSession
    ) -> dict[str, int]:
        """Build a mapping for CID linking.

        Maps both drugbank_id (e.g. 'PC12345') and lowercase drug name
        to drug.id so bioassay CIDs can be resolved via PubChem name
        lookup when the direct PC{CID} match fails.
        """
        result = await session.execute(
            select(Drug.drugbank_id, Drug.name, Drug.id)
        )
        lookup: dict[str, int] = {}
        for drugbank_id, name, drug_id in result.all():
            lookup[drugbank_id] = drug_id
            if name:
                lookup[f"NAME:{name.lower()}"] = drug_id
        return lookup
