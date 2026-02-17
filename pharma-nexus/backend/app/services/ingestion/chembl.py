"""ChEMBL data ingestion connector.

ChEMBL REST API — free, no API key needed.
Primary source for: drug-target binding affinities (IC50, Ki, Kd, EC50).
Secondary source for: approved drug mechanisms, target-UniProt mappings.

Rate limit: ~2 requests/second (academic resource, be polite).
API base: https://www.ebi.ac.uk/chembl/api/data
Pagination: limit/offset, pages of 500. Response includes page_meta.total_count.
"""

import logging
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.drug import Drug, DrugTarget
from app.models.evidence import Bioassay
from app.models.target import Target
from app.services.ingestion.base import BaseConnector

logger = logging.getLogger(__name__)

CHEMBL_BASE = "https://www.ebi.ac.uk/chembl/api/data"
PAGE_SIZE = 500


class ChEMBLConnector(BaseConnector):
    """Connector for ChEMBL drug-target binding affinity data.

    Three-phase ingestion:
      1. Fetch approved drug mechanisms (max_phase=4).
      2. Fetch target info with UniProt mappings.
      3. Fetch binding affinities (IC50/Ki/Kd/EC50) for each drug.
    """

    def __init__(
        self,
        db_session: AsyncSession | None = None,
        http_client: httpx.AsyncClient | None = None,
        rate_limit: float = 2.0,
        **kwargs: Any,
    ):
        super().__init__(
            db_session=db_session,
            http_client=http_client,
            rate_limit=rate_limit,
            **kwargs,
        )
        # Cache: ChEMBL target ID -> (uniprot_id, gene_symbol)
        self._target_cache: dict[str, tuple[str, str]] = {}
        # Cache: ChEMBL molecule ID -> drugbank_id
        self._molecule_drugbank_map: dict[str, str] = {}

    def get_source_name(self) -> str:
        return "chembl"

    # ------------------------------------------------------------------
    # Main fetch orchestrator
    # ------------------------------------------------------------------

    async def fetch_data(self, session: AsyncSession) -> list[dict[str, Any]]:
        """Run full ChEMBL ingestion pipeline."""
        client = await self._get_client()
        owns_client = self._external_client is None
        total = 0

        try:
            # Phase 1: Fetch approved drug mechanisms and cross-ref to DrugBank
            mech_count = await self._fetch_approved_mechanisms(session, client)
            total += mech_count

            # Phase 2: Fetch binding affinities for drugs in our database
            affinity_count = await self._fetch_binding_affinities(
                session, client
            )
            total += affinity_count

        finally:
            if owns_client:
                await client.aclose()

        self._records_processed = total
        return []

    def transform_record(self, raw_record: dict[str, Any]) -> dict[str, Any]:
        """Transform ChEMBL API response into our schema."""
        return raw_record

    # ------------------------------------------------------------------
    # Phase 1: Approved drug mechanisms
    # ------------------------------------------------------------------

    async def _fetch_approved_mechanisms(
        self, session: AsyncSession, client: httpx.AsyncClient
    ) -> int:
        """Fetch approved drug mechanisms from ChEMBL (max_phase=4)."""
        offset = 0
        total_fetched = 0
        drug_target_records: list[dict[str, Any]] = []

        while True:
            url = f"{CHEMBL_BASE}/mechanism.json"
            params = {
                "max_phase": 4,
                "limit": PAGE_SIZE,
                "offset": offset,
            }

            try:
                resp = await self.http_get(client, url, params=params)
                data = resp.json()
            except Exception as exc:
                self.record_error(
                    "fetch_mechanisms", exc,
                    record_id=f"offset:{offset}",
                )
                break

            mechanisms = data.get("mechanisms", [])
            if not mechanisms:
                break

            page_meta = data.get("page_meta", {})

            for mech in mechanisms:
                try:
                    molecule_chembl_id = mech.get("molecule_chembl_id")
                    target_chembl_id = mech.get("target_chembl_id")
                    action_type = mech.get("action_type")
                    mechanism_of_action = mech.get("mechanism_of_action")

                    if not molecule_chembl_id or not target_chembl_id:
                        continue

                    # Resolve target to UniProt ID
                    uniprot_id, gene_symbol = await self._resolve_target(
                        client, target_chembl_id
                    )
                    if not uniprot_id:
                        continue

                    # Resolve molecule to DrugBank ID
                    drugbank_id = await self._resolve_molecule_to_drugbank(
                        client, molecule_chembl_id
                    )

                    # Get or create target in our DB
                    target_id = await self.get_or_create_target(
                        session,
                        uniprot_id=uniprot_id,
                        gene_symbol=gene_symbol,
                    )
                    if target_id is None:
                        continue

                    # If we can map to a DrugBank drug, create the drug-target link
                    if drugbank_id:
                        drug_id = await self.get_drug_id_by_drugbank_id(
                            session, drugbank_id
                        )
                        if drug_id:
                            drug_target_records.append({
                                "drug_id": drug_id,
                                "target_id": target_id,
                                "action_type": action_type,
                                "known_action": True,
                                "source": "chembl",
                                "references": [
                                    {
                                        "chembl_molecule": molecule_chembl_id,
                                        "mechanism": mechanism_of_action,
                                    }
                                ],
                            })

                    total_fetched += 1

                except Exception as exc:
                    self.record_error(
                        "process_mechanism", exc,
                        record_id=mech.get("molecule_chembl_id", "unknown"),
                    )

            # Batch commit
            if len(drug_target_records) >= self._batch_size:
                await self.batch_insert_no_conflict(
                    session, DrugTarget, drug_target_records
                )
                await session.commit()
                drug_target_records.clear()

            # Check if more pages
            offset += PAGE_SIZE
            total_count = page_meta.get("total_count", 0)
            if offset >= total_count:
                break

        # Final batch
        if drug_target_records:
            await self.batch_insert_no_conflict(
                session, DrugTarget, drug_target_records
            )
            await session.commit()

        logger.info("Fetched %d mechanisms from ChEMBL", total_fetched)
        return total_fetched

    # ------------------------------------------------------------------
    # Phase 2: Binding affinities
    # ------------------------------------------------------------------

    async def _fetch_binding_affinities(
        self, session: AsyncSession, client: httpx.AsyncClient
    ) -> int:
        """Fetch quantitative binding affinities for drugs in our database."""
        # Get all drugs that have ChEMBL cross-references
        result = await session.execute(
            select(Drug.id, Drug.drugbank_id, Drug.name)
            .where(Drug.status == "approved")
            .order_by(Drug.id)
        )
        drugs = result.all()

        if not drugs:
            logger.info("No drugs to fetch affinities for")
            return 0

        total_activities = 0

        for drug_id, drugbank_id, drug_name in drugs:
            # Find ChEMBL molecule ID for this drug
            chembl_id = await self._find_chembl_id_for_drug(
                client, drugbank_id, drug_name
            )
            if not chembl_id:
                continue

            try:
                activities = await self._fetch_activities_for_molecule(
                    session, client, chembl_id, drug_id
                )
                total_activities += activities
            except Exception as exc:
                self.record_error(
                    "fetch_affinities", exc, record_id=drugbank_id
                )

        logger.info(
            "Fetched %d binding affinity records from ChEMBL",
            total_activities,
        )
        return total_activities

    async def _fetch_activities_for_molecule(
        self,
        session: AsyncSession,
        client: httpx.AsyncClient,
        molecule_chembl_id: str,
        drug_id: int,
    ) -> int:
        """Fetch IC50/Ki/Kd/EC50 activities for a specific molecule."""
        offset = 0
        total = 0
        bioassay_records: list[dict[str, Any]] = []
        affinity_updates: list[tuple[int, int, float]] = []

        while True:
            url = f"{CHEMBL_BASE}/activity.json"
            params = {
                "molecule_chembl_id": molecule_chembl_id,
                "standard_type__in": "IC50,Ki,Kd,EC50",
                "limit": PAGE_SIZE,
                "offset": offset,
            }

            try:
                resp = await self.http_get(client, url, params=params)
                data = resp.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    break
                raise
            except Exception as exc:
                self.record_error(
                    "fetch_activities", exc,
                    record_id=molecule_chembl_id,
                )
                break

            activities = data.get("activities", [])
            if not activities:
                break

            page_meta = data.get("page_meta", {})

            for act in activities:
                try:
                    target_chembl_id = act.get("target_chembl_id")
                    standard_type = act.get("standard_type")
                    standard_value = act.get("standard_value")
                    standard_units = act.get("standard_units")

                    if not target_chembl_id or standard_value is None:
                        continue

                    try:
                        value_nm = float(standard_value)
                    except (ValueError, TypeError):
                        continue

                    # Resolve target
                    uniprot_id, gene_symbol = await self._resolve_target(
                        client, target_chembl_id
                    )
                    if not uniprot_id:
                        continue

                    target_id = await self.get_or_create_target(
                        session,
                        uniprot_id=uniprot_id,
                        gene_symbol=gene_symbol,
                    )
                    if target_id is None:
                        continue

                    # Convert to nM if needed
                    if standard_units == "uM":
                        value_nm = value_nm * 1000
                    elif standard_units == "pM":
                        value_nm = value_nm / 1000

                    # Track best affinity for drug_targets update
                    affinity_updates.append((drug_id, target_id, value_nm))

                    # Create bioassay record
                    bioassay_records.append({
                        "pubchem_aid": None,
                        "target_id": target_id,
                        "drug_id": drug_id,
                        "activity_type": standard_type[:20] if standard_type else None,
                        "activity_value": value_nm,
                        "activity_unit": "nM",
                        "activity_outcome": "active",
                        "source": "chembl",
                    })

                    total += 1

                except Exception as exc:
                    self.record_error(
                        "process_activity", exc,
                        record_id=(
                            f"{molecule_chembl_id}:"
                            f"{act.get('target_chembl_id', '?')}"
                        ),
                    )

            # Batch commit
            if len(bioassay_records) >= self._batch_size:
                await self.batch_insert_no_conflict(
                    session, Bioassay, bioassay_records
                )
                await session.commit()
                bioassay_records.clear()

            offset += PAGE_SIZE
            total_count = page_meta.get("total_count", 0)
            if offset >= total_count:
                break

        # Final batch of bioassays
        if bioassay_records:
            await self.batch_insert_no_conflict(
                session, Bioassay, bioassay_records
            )

        # Update drug_targets with best binding affinity
        await self._update_binding_affinities(session, affinity_updates)
        await session.commit()

        return total

    async def _update_binding_affinities(
        self,
        session: AsyncSession,
        updates: list[tuple[int, int, float]],
    ) -> None:
        """Update drug_targets rows with the best (lowest) binding affinity."""
        # Group by (drug_id, target_id) and take the best (lowest) value
        best: dict[tuple[int, int], float] = {}
        for drug_id, target_id, value_nm in updates:
            key = (drug_id, target_id)
            if key not in best or value_nm < best[key]:
                best[key] = value_nm

        for (drug_id, target_id), affinity in best.items():
            stmt = (
                update(DrugTarget)
                .where(
                    DrugTarget.drug_id == drug_id,
                    DrugTarget.target_id == target_id,
                )
                .values(binding_affinity_nm=affinity)
            )
            await session.execute(stmt)

    # ------------------------------------------------------------------
    # Target resolution (ChEMBL target ID -> UniProt ID)
    # ------------------------------------------------------------------

    async def _resolve_target(
        self, client: httpx.AsyncClient, target_chembl_id: str
    ) -> tuple[str, str]:
        """Resolve a ChEMBL target ID to (uniprot_id, gene_symbol).

        Returns ("", "") if resolution fails.
        """
        if target_chembl_id in self._target_cache:
            return self._target_cache[target_chembl_id]

        url = f"{CHEMBL_BASE}/target/{target_chembl_id}.json"
        try:
            resp = await self.http_get(client, url)
            data = resp.json()
        except Exception:
            self._target_cache[target_chembl_id] = ("", "")
            return ("", "")

        # Extract UniProt accession from target_components
        components = data.get("target_components", [])
        uniprot_id = ""
        gene_symbol = ""

        for comp in components:
            accessions = comp.get("target_component_xrefs", [])
            for xref in accessions:
                if xref.get("xref_src_db") == "UniProt":
                    uniprot_id = xref.get("xref_id", "")
                    break

            # Get gene symbol from synonyms or accession
            synonyms = comp.get("target_component_synonyms", [])
            for syn in synonyms:
                if syn.get("syn_type") == "GENE_SYMBOL":
                    gene_symbol = syn.get("component_synonym", "")
                    break

            if uniprot_id:
                break

        self._target_cache[target_chembl_id] = (uniprot_id, gene_symbol)
        return (uniprot_id, gene_symbol)

    # ------------------------------------------------------------------
    # Molecule -> DrugBank ID resolution
    # ------------------------------------------------------------------

    async def _resolve_molecule_to_drugbank(
        self, client: httpx.AsyncClient, molecule_chembl_id: str
    ) -> str | None:
        """Look up cross-references for a ChEMBL molecule to find its DrugBank ID."""
        if molecule_chembl_id in self._molecule_drugbank_map:
            return self._molecule_drugbank_map[molecule_chembl_id] or None

        url = f"{CHEMBL_BASE}/molecule/{molecule_chembl_id}.json"
        try:
            resp = await self.http_get(client, url)
            data = resp.json()
        except Exception:
            self._molecule_drugbank_map[molecule_chembl_id] = ""
            return None

        cross_refs = data.get("cross_references", [])
        for xref in cross_refs:
            if xref.get("xref_src") == "drugbank":
                db_id = xref.get("xref_id", "")
                self._molecule_drugbank_map[molecule_chembl_id] = db_id
                return db_id

        self._molecule_drugbank_map[molecule_chembl_id] = ""
        return None

    async def _find_chembl_id_for_drug(
        self,
        client: httpx.AsyncClient,
        drugbank_id: str,
        drug_name: str,
    ) -> str | None:
        """Find ChEMBL molecule ID for a drug using name search."""
        # Try searching by name
        url = f"{CHEMBL_BASE}/molecule/search.json"
        params = {"q": drug_name, "limit": 5}

        try:
            resp = await self.http_get(client, url, params=params)
            data = resp.json()
        except Exception:
            return None

        molecules = data.get("molecules", [])
        for mol in molecules:
            # Check cross-references for matching DrugBank ID
            xrefs = mol.get("cross_references", [])
            for xref in xrefs:
                if (
                    xref.get("xref_src") == "drugbank"
                    and xref.get("xref_id") == drugbank_id
                ):
                    return mol.get("molecule_chembl_id")

            # If no cross-ref match, accept first approved molecule
            if mol.get("max_phase") == 4:
                chembl_id = mol.get("molecule_chembl_id")
                if chembl_id:
                    return chembl_id

        return None
