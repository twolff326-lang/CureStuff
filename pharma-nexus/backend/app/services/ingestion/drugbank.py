"""DrugBank data ingestion connector.

Supports two modes:
  Mode A (primary): Parse DrugBank XML dump for comprehensive drug/target data.
  Mode B (fallback): Use PubChem REST API to fetch basic drug data.

DrugBank is the authoritative source for: name, description, mechanism_of_action,
indication, targets, and action types.
"""

import logging
import os
from typing import Any
from xml.etree.ElementTree import iterparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.drug import Drug, DrugTarget
from app.models.target import Target
from app.services.ingestion.base import BaseConnector

logger = logging.getLogger(__name__)

# DrugBank XML namespace
DB_NS = "{http://www.drugbank.ca}"

# Default path to DrugBank XML file
DEFAULT_XML_PATH = os.environ.get("DRUGBANK_XML_PATH", "/data/drugbank.xml")


class DrugBankConnector(BaseConnector):
    """Connector for DrugBank drug and target data."""

    def __init__(
        self,
        db_session: AsyncSession | None = None,
        http_client: httpx.AsyncClient | None = None,
        xml_path: str | None = None,
        rate_limit: float = 1.0,
        **kwargs: Any,
    ):
        super().__init__(
            db_session=db_session,
            http_client=http_client,
            rate_limit=rate_limit,
            **kwargs,
        )
        self._xml_path = xml_path or DEFAULT_XML_PATH

    def get_source_name(self) -> str:
        return "drugbank"

    # ------------------------------------------------------------------
    # Main fetch orchestrator
    # ------------------------------------------------------------------

    async def fetch_data(self, session: AsyncSession) -> list[dict[str, Any]]:
        """Fetch drug data from XML file or fall back to PubChem API."""
        if os.path.isfile(self._xml_path):
            logger.info("DrugBank XML found at %s, parsing...", self._xml_path)
            return await self._fetch_from_xml(session)
        else:
            logger.info(
                "DrugBank XML not found at %s, falling back to PubChem API",
                self._xml_path,
            )
            return await self._fetch_from_pubchem_fallback(session)

    def transform_record(self, raw_record: dict[str, Any]) -> dict[str, Any]:
        """Transform a raw drug dict into the drugs table schema."""
        return {
            "drugbank_id": raw_record["drugbank_id"],
            "name": raw_record.get("name", "")[:500],
            "generic_name": (raw_record.get("generic_name") or "")[:500] or None,
            "description": raw_record.get("description"),
            "mechanism_of_action": raw_record.get("mechanism_of_action"),
            "pharmacodynamics": raw_record.get("pharmacodynamics"),
            "indication": raw_record.get("indication"),
            "status": raw_record.get("status", "approved"),
            "molecular_formula": raw_record.get("molecular_formula"),
            "smiles": raw_record.get("smiles"),
            "inchi_key": raw_record.get("inchi_key"),
            "cas_number": raw_record.get("cas_number"),
            "categories": raw_record.get("categories", []),
        }

    # ------------------------------------------------------------------
    # Mode A: XML file parsing (memory-efficient iterparse)
    # ------------------------------------------------------------------

    async def _fetch_from_xml(self, session: AsyncSession) -> list[dict[str, Any]]:
        """Parse DrugBank XML using iterparse for memory efficiency."""
        drug_records: list[dict[str, Any]] = []
        target_records: list[dict[str, Any]] = []
        drug_count = 0

        # iterparse is synchronous (file I/O), but we process in batches
        # and commit asynchronously
        try:
            for event, elem in iterparse(self._xml_path, events=("end",)):
                if elem.tag != f"{DB_NS}drug":
                    continue

                # Only process top-level <drug> elements (skip nested)
                drug_type = elem.attrib.get("type", "")
                if drug_type != "small molecule" and drug_type != "biotech":
                    elem.clear()
                    continue

                try:
                    raw = self._parse_drug_element(elem)
                    if raw is None:
                        elem.clear()
                        continue

                    drug_records.append(self.transform_record(raw))

                    # Collect target records for this drug
                    for t in raw.get("_targets", []):
                        target_records.append(t)

                    drug_count += 1

                    # Batch commit every 500 drugs
                    if len(drug_records) >= self._batch_size:
                        await self._commit_drug_batch(
                            session, drug_records, target_records
                        )
                        drug_records.clear()
                        target_records.clear()
                        logger.info("Processed %d drugs so far...", drug_count)

                except Exception as exc:
                    dbid = self._get_text(
                        elem, f"{DB_NS}drugbank-id[@primary='true']"
                    )
                    self.record_error(
                        "parse_drug_xml", exc, record_id=dbid or "unknown"
                    )

                elem.clear()

            # Final batch
            if drug_records:
                await self._commit_drug_batch(
                    session, drug_records, target_records
                )

        except Exception as exc:
            self.record_error("xml_parse_fatal", exc)
            raise

        self._records_processed = drug_count
        logger.info("DrugBank XML parsing complete: %d drugs", drug_count)
        return []  # Data already committed in batches

    def _parse_drug_element(self, elem: Any) -> dict[str, Any] | None:
        """Extract drug data from a single <drug> XML element."""
        drugbank_id = self._get_text(
            elem, f"{DB_NS}drugbank-id[@primary='true']"
        )
        if not drugbank_id:
            return None

        # Check status via groups
        groups = [
            g.text
            for g in elem.findall(f"{DB_NS}groups/{DB_NS}group")
            if g.text
        ]
        status = "approved" if "approved" in groups else (
            "experimental" if "experimental" in groups else (
                "withdrawn" if "withdrawn" in groups else "unknown"
            )
        )

        # Categories
        categories = [
            cat.text
            for cat_el in elem.findall(f"{DB_NS}categories/{DB_NS}category")
            for cat in cat_el.findall(f"{DB_NS}category")
            if cat.text
        ]

        # Calculated properties (SMILES, InChIKey, molecular formula)
        smiles = None
        inchi_key = None
        molecular_formula = None
        for prop in elem.findall(
            f"{DB_NS}calculated-properties/{DB_NS}property"
        ):
            kind = self._get_text(prop, f"{DB_NS}kind")
            value = self._get_text(prop, f"{DB_NS}value")
            if kind == "SMILES":
                smiles = value
            elif kind == "InChIKey":
                inchi_key = value
            elif kind == "Molecular Formula":
                molecular_formula = value

        # CAS number
        cas_number = self._get_text(elem, f"{DB_NS}cas-number")

        # Generic name from synonyms
        synonyms = [
            s.text
            for s in elem.findall(f"{DB_NS}synonyms/{DB_NS}synonym")
            if s.text
        ]
        generic_name = synonyms[0] if synonyms else None

        # Targets
        targets = []
        for target_el in elem.findall(f"{DB_NS}targets/{DB_NS}target"):
            target_data = self._parse_target_element(target_el, drugbank_id)
            if target_data:
                targets.append(target_data)

        return {
            "drugbank_id": drugbank_id,
            "name": self._get_text(elem, f"{DB_NS}name") or drugbank_id,
            "generic_name": generic_name,
            "description": self._get_text(elem, f"{DB_NS}description"),
            "mechanism_of_action": self._get_text(
                elem, f"{DB_NS}mechanism-of-action"
            ),
            "pharmacodynamics": self._get_text(
                elem, f"{DB_NS}pharmacodynamics"
            ),
            "indication": self._get_text(elem, f"{DB_NS}indication"),
            "status": status,
            "molecular_formula": molecular_formula,
            "smiles": smiles,
            "inchi_key": inchi_key,
            "cas_number": cas_number,
            "categories": categories,
            "_targets": targets,
        }

    def _parse_target_element(
        self, target_el: Any, drugbank_id: str
    ) -> dict[str, Any] | None:
        """Extract target data from a <target> XML element."""
        # Get UniProt ID from polypeptide
        polypeptide = target_el.find(
            f"{DB_NS}polypeptide[@source='Swiss-Prot']"
        )
        if polypeptide is None:
            # Try TrEMBL as fallback
            polypeptide = target_el.find(f"{DB_NS}polypeptide")

        if polypeptide is None:
            return None

        uniprot_id = polypeptide.attrib.get("id", "")
        if not uniprot_id:
            return None

        gene_symbol = (
            self._get_text(polypeptide, f"{DB_NS}gene-name") or ""
        )
        gene_name = self._get_text(polypeptide, f"{DB_NS}name") or ""
        organism = (
            self._get_text(polypeptide, f"{DB_NS}organism") or "Homo sapiens"
        )

        # Actions
        actions = [
            a.text
            for a in target_el.findall(f"{DB_NS}actions/{DB_NS}action")
            if a.text
        ]
        action_type = actions[0] if actions else None

        known_action_text = self._get_text(target_el, f"{DB_NS}known-action")
        known_action = known_action_text == "yes" if known_action_text else False

        return {
            "drugbank_id": drugbank_id,
            "uniprot_id": uniprot_id,
            "gene_symbol": gene_symbol,
            "gene_name": gene_name,
            "organism": organism,
            "action_type": action_type,
            "known_action": known_action,
        }

    async def _commit_drug_batch(
        self,
        session: AsyncSession,
        drug_records: list[dict[str, Any]],
        target_records: list[dict[str, Any]],
    ) -> None:
        """Upsert a batch of drugs and their targets."""
        # Upsert drugs
        if drug_records:
            await self.batch_upsert(
                session, Drug, drug_records,
                conflict_column="drugbank_id",
                update_columns=[
                    "name", "generic_name", "description",
                    "mechanism_of_action", "pharmacodynamics", "indication",
                    "status", "molecular_formula", "smiles", "inchi_key",
                    "cas_number", "categories",
                ],
            )

        # Process targets: ensure target exists, then create drug_targets
        if target_records:
            drug_target_rows: list[dict[str, Any]] = []
            for t in target_records:
                try:
                    target_id = await self.get_or_create_target(
                        session,
                        uniprot_id=t["uniprot_id"],
                        gene_symbol=t.get("gene_symbol", ""),
                        gene_name=t.get("gene_name", ""),
                        organism=t.get("organism", "Homo sapiens"),
                    )
                    if target_id is None:
                        continue

                    drug_id = await self.get_drug_id_by_drugbank_id(
                        session, t["drugbank_id"]
                    )
                    if drug_id is None:
                        continue

                    drug_target_rows.append({
                        "drug_id": drug_id,
                        "target_id": target_id,
                        "action_type": t.get("action_type"),
                        "known_action": t.get("known_action", False),
                        "source": "drugbank",
                        "references": [],
                    })
                except Exception as exc:
                    self.record_error(
                        "process_target", exc,
                        record_id=(
                            f"{t.get('drugbank_id')}:{t.get('uniprot_id')}"
                        ),
                    )

            if drug_target_rows:
                await self.batch_insert_no_conflict(
                    session, DrugTarget, drug_target_rows,
                )

        await session.commit()

    # ------------------------------------------------------------------
    # Mode B: PubChem API fallback
    # ------------------------------------------------------------------

    async def _fetch_from_pubchem_fallback(
        self, session: AsyncSession
    ) -> list[dict[str, Any]]:
        """Fetch a set of common FDA-approved drugs from PubChem as fallback."""
        # Well-known FDA-approved drugs relevant to cancer repurposing research
        FALLBACK_DRUGS = [
            "Metformin", "Aspirin", "Ibuprofen", "Celecoxib",
            "Thalidomide", "Doxycycline", "Chloroquine",
            "Hydroxychloroquine", "Methotrexate", "Tamoxifen",
            "Raloxifene", "Finasteride", "Dutasteride",
            "Atorvastatin", "Simvastatin", "Lovastatin", "Propranolol",
            "Carvedilol", "Losartan", "Disulfiram", "Mebendazole",
            "Niclosamide", "Ivermectin", "Sirolimus",
            "Everolimus", "Valproic Acid", "Vorinostat", "Pioglitazone",
            "Rosiglitazone", "Cimetidine", "Digoxin", "Nelfinavir",
            "Ritonavir", "Auranofin", "Itraconazole", "Ketoconazole",
        ]

        client = await self._get_client()
        owns_client = self._external_client is None
        drug_records: list[dict[str, Any]] = []

        try:
            for drug_name in FALLBACK_DRUGS:
                try:
                    record = await self._fetch_pubchem_compound(
                        client, drug_name
                    )
                    if record:
                        drug_records.append(self.transform_record(record))
                except Exception as exc:
                    self.record_error(
                        "pubchem_fallback_fetch", exc, record_id=drug_name
                    )

            # Batch upsert all
            if drug_records:
                await self.batch_upsert(
                    session, Drug, drug_records,
                    conflict_column="drugbank_id",
                    update_columns=[
                        "molecular_formula", "smiles", "inchi_key",
                    ],
                )
                await session.commit()

        finally:
            if owns_client:
                await client.aclose()

        self._records_processed = len(drug_records)
        return []

    async def _fetch_pubchem_compound(
        self, client: httpx.AsyncClient, drug_name: str
    ) -> dict[str, Any] | None:
        """Fetch compound data from PubChem by name."""
        url = (
            "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
            f"{drug_name}/property/"
            "MolecularFormula,CanonicalSMILES,InChIKey,MolecularWeight,IUPACName/"
            "JSON"
        )
        try:
            resp = await self.http_get(client, url)
            data = resp.json()
            props = data.get("PropertyTable", {}).get("Properties", [])
            if not props:
                return None

            p = props[0]
            cid = p.get("CID", "")
            return {
                "drugbank_id": f"PC{cid}",
                "name": drug_name,
                "generic_name": p.get("IUPACName"),
                "description": None,
                "mechanism_of_action": None,
                "pharmacodynamics": None,
                "indication": None,
                "status": "approved",
                "molecular_formula": p.get("MolecularFormula"),
                "smiles": p.get("CanonicalSMILES"),
                "inchi_key": p.get("InChIKey"),
                "cas_number": None,
                "categories": [],
            }
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                logger.debug("Drug '%s' not found in PubChem", drug_name)
                return None
            raise

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_text(elem: Any, path: str) -> str | None:
        """Safely get text content from an XML sub-element."""
        child = elem.find(path)
        if child is not None and child.text:
            return child.text.strip()
        return None
