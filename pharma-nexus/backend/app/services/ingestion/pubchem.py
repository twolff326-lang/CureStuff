"""PubChem ingestion connector.

Fetches FDA-approved drugs commonly used in oncology from PubChem's free REST API.
Uses a curated list of ~150 cancer-relevant approved drugs and enriches them with
chemical properties (SMILES, molecular formula, weight, CID).
"""
import asyncio
import logging
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.drug import Drug
from app.services.ingestion.base import BaseConnector

logger = logging.getLogger(__name__)

PUBCHEM_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"

# Curated list of FDA-approved drugs with known cancer/oncology relevance
# These are well-known drugs that PubChem has rich data for
CANCER_DRUGS = [
    "Imatinib", "Tamoxifen", "Methotrexate", "Doxorubicin", "Cisplatin",
    "Carboplatin", "Paclitaxel", "Docetaxel", "Gemcitabine", "Fluorouracil",
    "Cyclophosphamide", "Vincristine", "Etoposide", "Bleomycin", "Rituximab",
    "Trastuzumab", "Bevacizumab", "Erlotinib", "Gefitinib", "Sorafenib",
    "Sunitinib", "Lapatinib", "Dasatinib", "Nilotinib", "Bortezomib",
    "Lenalidomide", "Thalidomide", "Temozolomide", "Irinotecan", "Oxaliplatin",
    "Capecitabine", "Pemetrexed", "Letrozole", "Anastrozole", "Exemestane",
    "Fulvestrant", "Goserelin", "Leuprolide", "Bicalutamide", "Enzalutamide",
    "Abiraterone", "Vemurafenib", "Dabrafenib", "Trametinib", "Cobimetinib",
    "Crizotinib", "Ceritinib", "Alectinib", "Osimertinib", "Afatinib",
    "Ibrutinib", "Idelalisib", "Venetoclax", "Olaparib", "Rucaparib",
    "Niraparib", "Pembrolizumab", "Nivolumab", "Atezolizumab", "Durvalumab",
    "Ipilimumab", "Regorafenib", "Cabozantinib", "Lenvatinib", "Axitinib",
    "Pazopanib", "Everolimus", "Temsirolimus", "Panobinostat", "Vorinostat",
    "Romidepsin", "Carfilzomib", "Ixazomib", "Pomalidomide", "Vismodegib",
    "Sonidegib", "Palbociclib", "Ribociclib", "Abemaciclib", "Tucatinib",
    "Neratinib", "Pertuzumab", "Ado-trastuzumab", "Encorafenib", "Binimetinib",
    "Lorlatinib", "Brigatinib", "Entrectinib", "Larotrectinib", "Selpercatinib",
    "Pralsetinib", "Capmatinib", "Tepotinib", "Mobocertinib", "Sotorasib",
    "Adagrasib", "Tazemetostat", "Selinexor", "Belzutifan", "Zanubrutinib",
    "Acalabrutinib", "Copanlisib", "Duvelisib", "Umbralisib", "Gilteritinib",
    "Midostaurin", "Quizartinib", "Ivosidenib", "Enasidenib", "Glasdegib",
    "Avapritinib", "Ripretinib", "Infigratinib", "Futibatinib", "Pemigatinib",
    "Erdafitinib", "Sacituzumab", "Enfortumab", "Polatuzumab", "Loncastuximab",
    "Margetuximab", "Tivozanib", "Lurbinectedin", "Alpelisib", "Elacestrant",
    "Decitabine", "Azacitidine", "Hydroxyurea", "Mercaptopurine", "Thioguanine",
    "Cytarabine", "Cladribine", "Fludarabine", "Nelarabine", "Pralatrexate",
    "Clofarabine", "Busulfan", "Melphalan", "Chlorambucil", "Bendamustine",
    "Ifosfamide", "Thiotepa", "Mechlorethamine", "Procarbazine", "Dacarbazine",
    "Streptozocin", "Mitomycin", "Daunorubicin", "Epirubicin", "Idarubicin",
    "Mitoxantrone", "Vinblastine", "Vinorelbine", "Topotecan", "Trabectedin",
]


class PubChemConnector(BaseConnector):
    SOURCE_NAME = "pubchem"

    async def run(self):
        await self._update_log(total_expected=len(CANCER_DRUGS))
        inserted = 0

        async with httpx.AsyncClient() as client:
            for i, drug_name in enumerate(CANCER_DRUGS):
                try:
                    props = await self._fetch_compound(client, drug_name)
                    if props:
                        await self._upsert_drug(drug_name, props)
                        inserted += 1

                    if (i + 1) % 10 == 0:
                        await self._update_log(records_processed=inserted)
                        logger.info(f"PubChem progress: {i + 1}/{len(CANCER_DRUGS)} ({inserted} inserted)")

                    # Rate limit: ~5 requests/sec for PubChem
                    await asyncio.sleep(0.25)

                except Exception as e:
                    logger.warning(f"Failed to fetch {drug_name}: {e}")
                    continue

        await self._mark_completed(inserted)
        logger.info(f"PubChem ingestion complete: {inserted} drugs inserted")

    async def _fetch_compound(self, client: httpx.AsyncClient, name: str) -> dict | None:
        """Fetch compound properties from PubChem by drug name."""
        url = f"{PUBCHEM_BASE}/compound/name/{quote(name)}/property/MolecularFormula,MolecularWeight,CanonicalSMILES,IsomericSMILES,IUPACName/JSON"
        data = await self._fetch_json(client, url)
        if not data:
            return None

        props = data.get("PropertyTable", {}).get("Properties", [])
        if not props:
            return None

        return props[0]

    async def _upsert_drug(self, name: str, props: dict):
        """Insert or update a drug record."""
        cid = props.get("CID")

        # Check if drug already exists by CID or name
        result = await self.db.execute(
            select(Drug).where(
                (Drug.pubchem_cid == cid) | (Drug.name == name)
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.pubchem_cid = cid
            existing.smiles = props.get("CanonicalSMILES") or props.get("IsomericSMILES")
            existing.molecular_formula = props.get("MolecularFormula")
            existing.molecular_weight = props.get("MolecularWeight")
            if not existing.generic_name:
                existing.generic_name = props.get("IUPACName")
        else:
            drug = Drug(
                name=name,
                generic_name=props.get("IUPACName"),
                pubchem_cid=cid,
                smiles=props.get("CanonicalSMILES") or props.get("IsomericSMILES"),
                molecular_formula=props.get("MolecularFormula"),
                molecular_weight=props.get("MolecularWeight"),
                status="approved",
            )
            self.db.add(drug)

        await self.db.commit()
