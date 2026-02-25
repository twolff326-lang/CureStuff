"""ChEMBL ingestion connector.

Fetches drug-target binding data from ChEMBL's free REST API.
Two phases:
  1. Fetch approved drug mechanisms to get target mappings
  2. For each drug in our DB, fetch binding affinities (IC50, Ki, Kd, EC50)
"""
import asyncio
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.drug import Drug
from app.models.target import Target
from app.models.drug_target import DrugTarget
from app.services.ingestion.base import BaseConnector

logger = logging.getLogger(__name__)

CHEMBL_BASE = "https://www.ebi.ac.uk/chembl/api/data"
ACTIVITY_TYPES = {"IC50", "Ki", "Kd", "EC50"}


class ChEMBLConnector(BaseConnector):
    SOURCE_NAME = "chembl"

    async def run(self):
        async with httpx.AsyncClient() as client:
            # Phase 1: Fetch approved drug mechanisms
            mechanisms = await self._fetch_mechanisms(client)
            logger.info(f"ChEMBL Phase 1: {len(mechanisms)} approved mechanisms found")

            # Phase 2: Match mechanisms to our drugs and fetch activities
            drugs = (await self.db.execute(select(Drug))).scalars().all()
            drug_map = {d.name.lower(): d for d in drugs}
            drug_chembl_map = {d.chembl_id: d for d in drugs if d.chembl_id}

            await self._update_log(total_expected=len(drugs))
            processed = 0

            for mech in mechanisms:
                drug_name = (mech.get("molecule_name") or "").lower()
                chembl_id = mech.get("molecule_chembl_id")
                target_chembl_id = mech.get("target_chembl_id")

                drug = drug_map.get(drug_name) or drug_chembl_map.get(chembl_id)
                if not drug or not target_chembl_id:
                    continue

                # Update drug's ChEMBL ID if missing
                if not drug.chembl_id and chembl_id:
                    drug.chembl_id = chembl_id
                    await self.db.commit()

                # Fetch target info
                target = await self._get_or_create_target(client, target_chembl_id)
                if not target:
                    continue

                # Fetch binding activities for this drug-target pair
                if chembl_id:
                    activities = await self._fetch_activities(client, chembl_id, target_chembl_id)
                    for act in activities:
                        await self._upsert_drug_target(drug, target, act, mech)

                processed += 1
                if processed % 10 == 0:
                    await self._update_log(records_processed=processed)

                await asyncio.sleep(0.3)

        await self._mark_completed(processed)
        logger.info(f"ChEMBL ingestion complete: {processed} drug-target pairs processed")

    async def _fetch_mechanisms(self, client: httpx.AsyncClient) -> list[dict]:
        """Fetch all approved drug mechanisms from ChEMBL."""
        mechanisms = []
        url = f"{CHEMBL_BASE}/mechanism.json"
        params = {"limit": 100, "offset": 0}

        while True:
            data = await self._fetch_json(client, url, params=params)
            if not data:
                break

            results = data.get("mechanisms", [])
            mechanisms.extend(results)

            page_meta = data.get("page_meta", {})
            if not page_meta.get("next"):
                break

            params["offset"] += params["limit"]
            await asyncio.sleep(0.3)

        return mechanisms

    async def _get_or_create_target(
        self, client: httpx.AsyncClient, target_chembl_id: str
    ) -> Target | None:
        """Get target from DB or create from ChEMBL data."""
        # Fetch target details from ChEMBL
        url = f"{CHEMBL_BASE}/target/{target_chembl_id}.json"
        data = await self._fetch_json(client, url)
        if not data:
            return None

        # Extract gene symbol from target components
        gene_symbol = None
        components = data.get("target_components", [])
        for comp in components:
            for syn in comp.get("target_component_synonyms", []):
                if syn.get("syn_type") == "GENE_SYMBOL":
                    gene_symbol = syn.get("component_synonym")
                    break
            if gene_symbol:
                break

        if not gene_symbol:
            # Try accession as fallback
            for comp in components:
                accession = comp.get("accession")
                if accession:
                    gene_symbol = accession
                    break

        if not gene_symbol:
            return None

        # Check if target exists
        result = await self.db.execute(
            select(Target).where(Target.gene_symbol == gene_symbol)
        )
        target = result.scalar_one_or_none()

        if not target:
            target = Target(
                gene_symbol=gene_symbol,
                name=data.get("pref_name"),
                description=data.get("organism"),
            )
            # Try to get UniProt ID
            for comp in components:
                accession = comp.get("accession")
                if accession and accession.startswith(("P", "Q", "O")):
                    target.uniprot_id = accession
                    break

            self.db.add(target)
            await self.db.commit()
            await self.db.refresh(target)

        return target

    async def _fetch_activities(
        self, client: httpx.AsyncClient, molecule_chembl_id: str, target_chembl_id: str
    ) -> list[dict]:
        """Fetch binding activity data for a specific drug-target pair."""
        url = f"{CHEMBL_BASE}/activity.json"
        params = {
            "molecule_chembl_id": molecule_chembl_id,
            "target_chembl_id": target_chembl_id,
            "limit": 50,
        }
        data = await self._fetch_json(client, url, params=params)
        if not data:
            return []

        activities = []
        for act in data.get("activities", []):
            act_type = act.get("standard_type")
            if act_type in ACTIVITY_TYPES and act.get("standard_value"):
                try:
                    activities.append({
                        "type": act_type,
                        "value": float(act["standard_value"]),
                        "units": act.get("standard_units", "nM"),
                    })
                except (ValueError, TypeError):
                    continue

        return activities

    async def _upsert_drug_target(
        self, drug: Drug, target: Target, activity: dict, mechanism: dict
    ):
        """Create or update a drug-target binding record."""
        # Check if this exact pair already exists
        result = await self.db.execute(
            select(DrugTarget).where(
                DrugTarget.drug_id == drug.id,
                DrugTarget.target_id == target.id,
                DrugTarget.affinity_type == activity["type"],
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.binding_affinity = activity["value"]
            existing.affinity_units = activity["units"]
        else:
            dt = DrugTarget(
                drug_id=drug.id,
                target_id=target.id,
                action_type=mechanism.get("action_type"),
                binding_affinity=activity["value"],
                affinity_type=activity["type"],
                affinity_units=activity["units"],
                source="chembl",
            )
            self.db.add(dt)

        await self.db.commit()
