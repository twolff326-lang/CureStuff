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
        """Run full ChEMBL ingestion pipeline.

        Supports resuming from a checkpoint saved by a previous failed run.
        If the checkpoint says Phase 1 already completed, Phase 1 is skipped
        entirely and Phase 2 resumes from the last processed drug index.
        """
        client = await self._get_client()
        owns_client = self._external_client is None
        total = 0

        # Read checkpoint from previous failed run (set by BaseConnector.run)
        cp = self._checkpoint
        skip_phase1 = False
        phase1_saved_count = 0
        resume_drug_idx = 0

        if cp and cp.get("source") == "chembl":
            if cp.get("phase1_done"):
                skip_phase1 = True
                phase1_saved_count = cp.get("phase1_count", 0)
                resume_drug_idx = cp.get("phase2_drug_idx", 0)
                self._log_event(
                    "checkpoint_resume",
                    f"skip Phase 1 (count={phase1_saved_count}), "
                    f"Phase 2 from drug idx {resume_drug_idx}",
                )
                logger.info(
                    "Resuming ChEMBL from checkpoint: skip Phase 1 "
                    "(count=%d), Phase 2 from drug idx %d",
                    phase1_saved_count, resume_drug_idx,
                )

        try:
            if skip_phase1:
                mech_count = phase1_saved_count
                self._records_processed = mech_count
                await self._flush_progress(session)
                self.record_warning(
                    "phase1_skipped",
                    f"Phase 1 skipped via checkpoint (previous count={phase1_saved_count})",
                )
            else:
                # Phase 1: Fetch approved drug mechanisms
                self.begin_phase(
                    "Phase 1: Approved mechanisms",
                    "Fetching approved drug mechanisms from ChEMBL (max_phase=4)",
                )
                mech_count = await self._fetch_approved_mechanisms(
                    session, client,
                )
                self.end_phase(
                    records_in=self._report["data_quality"]["total_api_records_received"],
                    records_out=mech_count,
                    detail=f"{mech_count} mechanisms processed",
                )
                # Save checkpoint: Phase 1 done
                await self._save_checkpoint(session, {
                    "source": "chembl",
                    "phase1_done": True,
                    "phase1_count": mech_count,
                    "phase2_drug_idx": 0,
                })

            total += mech_count

            # Phase 2: Fetch binding affinities for drugs in our database
            self.begin_phase(
                "Phase 2: Binding affinities",
                f"Fetching IC50/Ki/Kd/EC50 for approved drugs"
                + (f" (resuming from idx {resume_drug_idx})" if resume_drug_idx else ""),
            )
            affinity_count = await self._fetch_binding_affinities(
                session, client,
                phase1_count=mech_count,
                resume_drug_idx=resume_drug_idx,
            )
            self.end_phase(
                records_in=self._report["data_quality"]["total_api_records_received"],
                records_out=affinity_count,
                detail=f"{affinity_count} activity records stored",
            )
            total += affinity_count

        finally:
            if owns_client:
                await client.aclose()
            # Report final cache stats
            self._report["caches"]["target_cache"] = {
                "size": len(self._target_cache),
                "resolved": sum(1 for v in self._target_cache.values() if v[0]),
                "unresolved": sum(1 for v in self._target_cache.values() if not v[0]),
            }
            self._report["caches"]["molecule_drugbank_map"] = {
                "size": len(self._molecule_drugbank_map),
                "matched": sum(1 for v in self._molecule_drugbank_map.values() if v),
                "unmatched": sum(1 for v in self._molecule_drugbank_map.values() if not v),
            }

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
        pages_fetched = 0
        skipped_no_ids = 0
        skipped_no_uniprot = 0
        skipped_no_drugbank = 0
        skipped_no_drug = 0

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
                self._log_event(
                    "phase1_empty_page",
                    f"offset={offset}: empty mechanisms list, stopping pagination",
                )
                break

            pages_fetched += 1
            self.report_api_records(len(mechanisms))
            page_meta = data.get("page_meta", {})

            # Set total_expected from first page metadata
            if offset == 0:
                api_total = page_meta.get("total_count")
                if api_total:
                    await self.set_total_expected(session, api_total)
                    self._log_event(
                        "phase1_total_discovered",
                        f"ChEMBL reports {api_total} total mechanisms",
                    )

            for mech in mechanisms:
                if not isinstance(mech, dict):
                    self.record_skip("non_dict")
                    continue

                try:
                    molecule_chembl_id = mech.get("molecule_chembl_id")
                    target_chembl_id = mech.get("target_chembl_id")
                    action_type = mech.get("action_type")
                    mechanism_of_action = mech.get("mechanism_of_action")

                    if not molecule_chembl_id or not target_chembl_id:
                        skipped_no_ids += 1
                        self.record_skip("missing_field")
                        continue

                    # Resolve target to UniProt ID
                    uniprot_id, gene_symbol = await self._resolve_target(
                        client, target_chembl_id
                    )
                    if not uniprot_id:
                        skipped_no_uniprot += 1
                        self.record_skip("filtered")
                        continue

                    # Resolve molecule to DrugBank ID
                    drugbank_id = await self._resolve_molecule_to_drugbank(
                        client, session, molecule_chembl_id
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
                        else:
                            skipped_no_drug += 1
                    else:
                        skipped_no_drugbank += 1

                    total_fetched += 1

                except Exception as exc:
                    self.record_error(
                        "process_mechanism", exc,
                        record_id=mech.get("molecule_chembl_id", "unknown"),
                    )

                # Flush progress every 50 mechanisms — each mechanism
                # makes 2+ HTTP calls at 2 req/sec, so a full page of
                # 500 takes ~500s.  Flushing every 50 keeps the UI alive.
                if total_fetched % 50 == 0:
                    self._records_processed = total_fetched
                    await self._flush_progress(session)

            # Flush at end of page as well
            self._records_processed = total_fetched
            await self._flush_progress(session)
            self._log_event(
                "phase1_page_done",
                f"page={pages_fetched} offset={offset} "
                f"fetched={total_fetched} drug_target_records={len(drug_target_records)}",
            )

            # Batch commit
            if len(drug_target_records) >= self._batch_size:
                await self.batch_upsert_composite(
                    session, DrugTarget, drug_target_records,
                    conflict_columns=["drug_id", "target_id"],
                    update_columns=["action_type", "known_action", "source", "references"],
                )
                await session.commit()
                drug_target_records.clear()

            # Check if more pages
            offset += PAGE_SIZE
            total_count = page_meta.get("total_count") or 10_000_000
            max_pages = 100
            if offset >= total_count or offset >= max_pages * PAGE_SIZE:
                self._log_event(
                    "phase1_pagination_end",
                    f"offset={offset} >= total_count={total_count} or max_pages={max_pages}",
                )
                break

        # Final batch
        if drug_target_records:
            await self.batch_upsert_composite(
                session, DrugTarget, drug_target_records,
                conflict_columns=["drug_id", "target_id"],
                update_columns=["action_type", "known_action", "source", "references"],
            )
            await session.commit()

        # Report Phase 1 skip breakdown
        if skipped_no_ids or skipped_no_uniprot or skipped_no_drugbank or skipped_no_drug:
            self.record_warning(
                "phase1_skip_summary",
                f"Skipped: no_ids={skipped_no_ids} no_uniprot={skipped_no_uniprot} "
                f"no_drugbank={skipped_no_drugbank} no_drug_in_db={skipped_no_drug}",
            )

        logger.info("Fetched %d mechanisms from ChEMBL", total_fetched)
        return total_fetched

    # ------------------------------------------------------------------
    # Phase 2: Binding affinities
    # ------------------------------------------------------------------

    async def _fetch_binding_affinities(
        self, session: AsyncSession, client: httpx.AsyncClient,
        phase1_count: int = 0,
        resume_drug_idx: int = 0,
    ) -> int:
        """Fetch quantitative binding affinities for drugs in our database.

        Args:
            resume_drug_idx: 0-based index into the drug list to resume from.
                Drugs before this index are skipped (already processed in a
                previous run).
        """
        # Get all drugs that have ChEMBL cross-references
        result = await session.execute(
            select(Drug.id, Drug.drugbank_id, Drug.name)
            .where(Drug.status == "approved")
            .order_by(Drug.id)
        )
        drugs = result.all()

        if not drugs:
            self.record_warning("phase2_no_drugs", "No approved drugs found in DB to fetch affinities for")
            logger.info("No drugs to fetch affinities for")
            return 0

        self._log_event(
            "phase2_drug_list",
            f"{len(drugs)} approved drugs found, resume_idx={resume_drug_idx}",
        )

        # Update total_expected: phase1 count + number of drugs to enrich
        await self.set_total_expected(
            session, phase1_count + len(drugs),
        )

        if resume_drug_idx > 0:
            logger.info(
                "Resuming Phase 2 from drug index %d / %d",
                resume_drug_idx, len(drugs),
            )
            # Update progress counter to reflect skipped drugs
            self._records_processed = phase1_count + resume_drug_idx
            await self._flush_progress(session)

        total_activities = 0
        drugs_with_chembl_id = 0
        drugs_without_chembl_id = 0
        drugs_with_activities = 0

        for idx, (drug_id, drugbank_id, drug_name) in enumerate(drugs, 1):
            # Skip drugs already processed in a previous run
            if idx <= resume_drug_idx:
                continue

            # Find ChEMBL molecule ID for this drug
            chembl_id = await self._find_chembl_id_for_drug(
                client, drugbank_id, drug_name
            )
            if not chembl_id:
                drugs_without_chembl_id += 1
                self._records_processed = phase1_count + idx
                if idx % 10 == 0:
                    await self._flush_progress(session)
                continue

            drugs_with_chembl_id += 1

            try:
                activities = await self._fetch_activities_for_molecule(
                    session, client, chembl_id, drug_id
                )
                total_activities += activities
                if activities > 0:
                    drugs_with_activities += 1
            except Exception as exc:
                self.record_error(
                    "fetch_affinities", exc, record_id=drugbank_id
                )

            # Flush progress every 10 drugs so the UI updates
            self._records_processed = phase1_count + idx
            if idx % 10 == 0:
                await self._flush_progress(session)
                # Save checkpoint so retries skip already-processed drugs
                await self._save_checkpoint(session, {
                    "source": "chembl",
                    "phase1_done": True,
                    "phase1_count": phase1_count,
                    "phase2_drug_idx": idx,
                })
                self._log_event(
                    "phase2_progress",
                    f"drug {idx}/{len(drugs)}: "
                    f"with_chembl={drugs_with_chembl_id} "
                    f"without_chembl={drugs_without_chembl_id} "
                    f"activities={total_activities}",
                )

        # Final flush for Phase 2
        self._records_processed = phase1_count + len(drugs)
        await self._flush_progress(session)

        # Report Phase 2 summary
        self.record_warning(
            "phase2_drug_resolution_summary",
            f"Total drugs: {len(drugs)}, "
            f"resolved to ChEMBL ID: {drugs_with_chembl_id}, "
            f"no ChEMBL ID: {drugs_without_chembl_id}, "
            f"had activities: {drugs_with_activities}, "
            f"total activity records: {total_activities}",
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
        pages_for_molecule = 0

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

            pages_for_molecule += 1
            self.report_api_records(len(activities))
            page_meta = data.get("page_meta", {})

            for act in activities:
                if not isinstance(act, dict):
                    self.record_skip("non_dict")
                    continue

                try:
                    target_chembl_id = act.get("target_chembl_id")
                    standard_type = act.get("standard_type")
                    standard_value = act.get("standard_value")
                    standard_units = act.get("standard_units")

                    if not target_chembl_id or standard_value is None:
                        self.record_skip("missing_field")
                        continue

                    try:
                        value_nm = float(standard_value)
                    except (ValueError, TypeError):
                        self.record_skip("filtered")
                        continue

                    # Resolve target
                    uniprot_id, gene_symbol = await self._resolve_target(
                        client, target_chembl_id
                    )
                    if not uniprot_id:
                        self.record_skip("filtered")
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
            total_count = page_meta.get("total_count") or 10_000_000
            max_pages = 100
            if offset >= total_count or offset >= max_pages * PAGE_SIZE:
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

        updated = 0
        skipped = 0
        for (drug_id, target_id), affinity in best.items():
            stmt = (
                update(DrugTarget)
                .where(
                    DrugTarget.drug_id == drug_id,
                    DrugTarget.target_id == target_id,
                )
                .values(binding_affinity_nm=affinity)
            )
            result = await session.execute(stmt)
            if result.rowcount > 0:
                updated += 1
            else:
                skipped += 1
        if skipped > 0:
            self.record_warning(
                "affinity_update_skipped",
                f"{skipped} affinity updates skipped (no matching drug-target row), "
                f"{updated} updated successfully",
            )
            logger.warning(
                "Binding affinity update: %d updated, %d skipped (no matching drug-target row)",
                updated, skipped,
            )

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
            self.report_cache_access("target_cache", hit=True)
            return self._target_cache[target_chembl_id]

        self.report_cache_access("target_cache", hit=False)

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
            if not isinstance(comp, dict):
                continue
            accessions = comp.get("target_component_xrefs", [])
            for xref in accessions:
                if not isinstance(xref, dict):
                    continue
                if xref.get("xref_src_db") == "UniProt":
                    uniprot_id = xref.get("xref_id", "")
                    break

            # Get gene symbol from synonyms or accession
            synonyms = comp.get("target_component_synonyms", [])
            for syn in synonyms:
                if not isinstance(syn, dict):
                    continue
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
        self,
        client: httpx.AsyncClient,
        session: AsyncSession,
        molecule_chembl_id: str,
    ) -> str | None:
        """Look up cross-references for a ChEMBL molecule to find its DrugBank ID.

        First tries the ChEMBL→DrugBank cross-reference. If that fails (e.g.
        when using PubChem fallback IDs like PC12345), falls back to matching
        the molecule's preferred name against drug names in our database.
        """
        if molecule_chembl_id in self._molecule_drugbank_map:
            self.report_cache_access("molecule_drugbank_map", hit=True)
            return self._molecule_drugbank_map[molecule_chembl_id] or None

        self.report_cache_access("molecule_drugbank_map", hit=False)

        url = f"{CHEMBL_BASE}/molecule/{molecule_chembl_id}.json"
        try:
            resp = await self.http_get(client, url)
            data = resp.json()
        except Exception:
            self._molecule_drugbank_map[molecule_chembl_id] = ""
            return None

        # Try 1: ChEMBL→DrugBank cross-reference
        cross_refs = data.get("cross_references", [])
        for xref in cross_refs:
            if not isinstance(xref, dict):
                continue
            if xref.get("xref_src") == "drugbank":
                db_id = xref.get("xref_id", "")
                self._molecule_drugbank_map[molecule_chembl_id] = db_id
                return db_id

        # Try 2: Match molecule pref_name against drug names in our database
        pref_name = data.get("pref_name", "")
        if pref_name:
            result = await session.execute(
                select(Drug.drugbank_id).where(
                    Drug.name.ilike(pref_name)
                ).limit(1)
            )
            matched_id = result.scalar_one_or_none()
            if matched_id:
                self._molecule_drugbank_map[molecule_chembl_id] = matched_id
                return matched_id

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
            if not isinstance(mol, dict):
                continue
            # Check cross-references for matching DrugBank ID
            xrefs = mol.get("cross_references", [])
            for xref in xrefs:
                if not isinstance(xref, dict):
                    continue
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
