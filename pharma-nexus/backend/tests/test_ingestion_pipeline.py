"""Diagnostic integration tests for the ingestion pipeline.

Tests each connector's core logic against live external APIs using a
real (test) database session.  Run with:

    docker compose exec backend python -m pytest tests/test_ingestion_pipeline.py -v -s

Each test validates:
  1. External API is reachable and returns usable data
  2. Connector correctly transforms API responses
  3. Data is actually committed to the database
  4. Progress tracking (total_expected / records_processed) works
  5. Downstream connectors can find upstream data

These are slow tests (network I/O) — they're diagnostic, not CI.
"""

import asyncio
import logging
from datetime import datetime, timezone

import httpx
import pytest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(coro):
    """Run an async coroutine in a fresh event loop (like Celery tasks do)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def make_session():
    """Create a real async database session for testing."""
    from app.tasks.utils import task_session
    return task_session()


async def row_count(session, model):
    """Return the row count for a SQLAlchemy model."""
    from sqlalchemy import func, select
    result = await session.execute(select(func.count(model.id)))
    return result.scalar() or 0


# ---------------------------------------------------------------------------
# 1. DATABASE CONNECTIVITY
# ---------------------------------------------------------------------------

class TestDatabaseConnectivity:
    """Verify we can connect to PostgreSQL and the schema is correct."""

    def test_can_connect_and_query(self):
        """Basic SELECT 1 to verify DB is reachable."""
        async def _test():
            from sqlalchemy import text
            async with await make_session() as session:
                result = await session.execute(text("SELECT 1"))
                assert result.scalar() == 1
        run(_test())

    def test_drugs_table_exists_with_all_columns(self):
        """Verify the drugs table has all columns the model expects."""
        async def _test():
            from sqlalchemy import text
            async with await make_session() as session:
                result = await session.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'drugs' ORDER BY ordinal_position"
                ))
                columns = {row[0] for row in result.fetchall()}

                expected = {
                    "id", "drugbank_id", "name", "generic_name", "description",
                    "mechanism_of_action", "pharmacodynamics", "indication",
                    "status", "molecular_formula", "smiles", "inchi_key",
                    "cas_number", "categories", "mechanism_embedding",
                    "created_at", "updated_at",
                }
                missing = expected - columns
                assert not missing, f"Missing columns in drugs table: {missing}"
        run(_test())

    def test_ingestion_logs_table_has_new_columns(self):
        """Verify migration 015 columns exist."""
        async def _test():
            from sqlalchemy import text
            async with await make_session() as session:
                result = await session.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'ingestion_logs' ORDER BY ordinal_position"
                ))
                columns = {row[0] for row in result.fetchall()}

                expected_new = {
                    "data_source_version", "data_downloaded_at", "api_url",
                    "records_filtered", "checksum", "duration_seconds",
                }
                missing = expected_new - columns
                assert not missing, (
                    f"Missing columns in ingestion_logs (migration 015 not applied?): {missing}"
                )
        run(_test())

    def test_alembic_version_is_015(self):
        """Verify all migrations have been applied."""
        async def _test():
            from sqlalchemy import text
            async with await make_session() as session:
                result = await session.execute(
                    text("SELECT version_num FROM alembic_version")
                )
                version = result.scalar()
                assert version == "015", (
                    f"Alembic version is {version!r}, expected '015'. "
                    f"Run: alembic upgrade head"
                )
        run(_test())


# ---------------------------------------------------------------------------
# 2. EXTERNAL API REACHABILITY
# ---------------------------------------------------------------------------

class TestExternalAPIs:
    """Verify every external data source API is reachable."""

    @pytest.fixture(autouse=True)
    def _client(self):
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
        yield
        run(self.client.aclose())

    def test_pubchem_api(self):
        async def _test():
            resp = await self.client.get(
                "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
                "Aspirin/property/MolecularFormula/JSON"
            )
            assert resp.status_code == 200, f"PubChem returned {resp.status_code}"
            data = resp.json()
            props = data.get("PropertyTable", {}).get("Properties", [])
            assert len(props) > 0, "PubChem returned no properties for Aspirin"
            logger.info("PubChem OK: Aspirin formula = %s", props[0].get("MolecularFormula"))
        run(_test())

    def test_chembl_api(self):
        async def _test():
            resp = await self.client.get(
                "https://www.ebi.ac.uk/chembl/api/data/mechanism.json",
                params={"max_phase": 4, "limit": 1},
            )
            assert resp.status_code == 200, f"ChEMBL returned {resp.status_code}"
            data = resp.json()
            assert "mechanisms" in data, f"Unexpected ChEMBL response keys: {list(data.keys())}"
            logger.info("ChEMBL OK: %d mechanisms in sample", len(data["mechanisms"]))
        run(_test())

    def test_cbioportal_api(self):
        async def _test():
            resp = await self.client.get(
                "https://www.cbioportal.org/api/studies",
                params={"projection": "SUMMARY", "pageSize": 5},
            )
            assert resp.status_code == 200, f"cBioPortal returned {resp.status_code}"
            studies = resp.json()
            assert len(studies) > 0, "cBioPortal returned no studies"
            logger.info("cBioPortal OK: %d studies in sample", len(studies))
        run(_test())

    def test_kegg_api(self):
        async def _test():
            resp = await self.client.get("https://rest.kegg.jp/list/pathway/hsa")
            assert resp.status_code == 200, f"KEGG returned {resp.status_code}"
            lines = resp.text.strip().split("\n")
            assert len(lines) > 100, f"KEGG returned only {len(lines)} pathways"
            logger.info("KEGG OK: %d human pathways", len(lines))
        run(_test())

    def test_reactome_api(self):
        async def _test():
            resp = await self.client.get(
                "https://reactome.org/ContentService/data/pathways/top/9606"
            )
            assert resp.status_code == 200, f"Reactome returned {resp.status_code}"
            pathways = resp.json()
            assert len(pathways) > 0, "Reactome returned no top-level pathways"
            logger.info("Reactome OK: %d top-level pathways", len(pathways))
        run(_test())

    def test_string_api(self):
        async def _test():
            resp = await self.client.post(
                "https://string-db.org/api/json/network",
                data={"identifiers": "TP53\nEGFR", "species": 9606},
            )
            assert resp.status_code == 200, f"STRING returned {resp.status_code}"
            interactions = resp.json()
            assert len(interactions) > 0, "STRING returned no interactions for TP53/EGFR"
            logger.info("STRING OK: %d interactions", len(interactions))
        run(_test())

    def test_uniprot_api(self):
        async def _test():
            resp = await self.client.get(
                "https://rest.uniprot.org/uniprotkb/P04637.json"
            )
            assert resp.status_code == 200, f"UniProt returned {resp.status_code}"
            data = resp.json()
            assert data.get("primaryAccession") == "P04637", "Unexpected UniProt response"
            logger.info("UniProt OK: %s (%s)", data.get("primaryAccession"), data.get("proteinDescription", {}).get("recommendedName", {}).get("fullName", {}).get("value", "?"))
        run(_test())

    def test_opentargets_api(self):
        async def _test():
            query = """
            query { target(ensemblId: "ENSG00000141510") {
              id approvedSymbol approvedName
            }}
            """
            resp = await self.client.post(
                "https://api.platform.opentargets.org/api/v4/graphql",
                json={"query": query},
            )
            assert resp.status_code == 200, f"OpenTargets returned {resp.status_code}"
            data = resp.json()
            target = data.get("data", {}).get("target", {})
            assert target.get("approvedSymbol") == "TP53", f"Unexpected: {target}"
            logger.info("OpenTargets OK: %s = %s", target.get("id"), target.get("approvedSymbol"))
        run(_test())


# ---------------------------------------------------------------------------
# 3. DRUGBANK CONNECTOR (PubChem fallback)
# ---------------------------------------------------------------------------

class TestDrugBankConnector:
    """Test DrugBank PubChem fallback inserts drugs and they're queryable."""

    def test_pubchem_fetch_single_drug(self):
        """Verify we can fetch and transform a single drug from PubChem."""
        async def _test():
            from app.services.ingestion.drugbank import DrugBankConnector
            connector = DrugBankConnector()
            client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
            try:
                record = await connector._fetch_pubchem_compound(client, "Metformin")
                assert record is not None, "PubChem returned None for Metformin"
                assert record["name"] == "Metformin"
                assert record["drugbank_id"].startswith("PC")
                assert record["molecular_formula"] is not None

                transformed = connector.transform_record(record)
                assert transformed["drugbank_id"] == record["drugbank_id"]
                assert transformed["name"] == "Metformin"
                assert transformed["status"] == "approved"
                logger.info(
                    "DrugBank transform OK: %s -> %s (formula=%s)",
                    transformed["name"], transformed["drugbank_id"],
                    transformed["molecular_formula"],
                )
            finally:
                await client.aclose()
        run(_test())

    def test_batch_upsert_inserts_drugs(self):
        """Verify batch_upsert actually commits drugs to the database."""
        async def _test():
            from app.models.drug import Drug
            from app.services.ingestion.drugbank import DrugBankConnector
            from sqlalchemy import select

            async with await make_session() as session:
                connector = DrugBankConnector(db_session=session)

                # Insert a test drug
                test_records = [{
                    "drugbank_id": "PCTEST001",
                    "name": "DiagnosticTestDrug",
                    "generic_name": "test-compound",
                    "description": None,
                    "mechanism_of_action": None,
                    "pharmacodynamics": None,
                    "indication": None,
                    "status": "approved",
                    "molecular_formula": "C4H11N5",
                    "smiles": "CN(C)C(=N)NC(=N)N",
                    "inchi_key": "TEST-INCHI-KEY",
                    "cas_number": None,
                    "categories": [],
                }]

                count = await connector.batch_upsert(
                    session, Drug, test_records,
                    conflict_column="drugbank_id",
                    update_columns=["name", "status"],
                )
                await session.commit()
                assert count == 1, f"batch_upsert returned {count}, expected 1"

                # Read it back
                result = await session.execute(
                    select(Drug).where(Drug.drugbank_id == "PCTEST001")
                )
                drug = result.scalar_one_or_none()
                assert drug is not None, "Drug not found after batch_upsert + commit!"
                assert drug.name == "DiagnosticTestDrug"
                assert drug.status == "approved"
                logger.info(
                    "batch_upsert OK: drug.id=%d, name=%s, status=%s",
                    drug.id, drug.name, drug.status,
                )

                # Cleanup
                await session.delete(drug)
                await session.commit()
        run(_test())

    def test_full_connector_run_small(self):
        """Run the DrugBank connector with the full PubChem fallback.

        After completion, verify:
        - Ingestion log was created with status=completed
        - records_processed > 0
        - Actual drug rows exist in the database
        """
        async def _test():
            from app.models.drug import Drug
            from app.models.ingestion_log import IngestionLog
            from sqlalchemy import select, func

            async with await make_session() as session:
                # Clear any stale running/completed logs that would trigger the guard
                from sqlalchemy import delete
                await session.execute(
                    delete(IngestionLog).where(IngestionLog.source == "drugbank")
                )
                await session.commit()

                from app.services.ingestion.drugbank import DrugBankConnector
                connector = DrugBankConnector(db_session=session)
                result = await connector.run()

                assert result["status"] == "completed", (
                    f"Connector status={result['status']}, "
                    f"errors={result.get('errors', [])[:3]}"
                )
                assert result["records_processed"] > 0, (
                    "records_processed is 0 — PubChem fallback fetched nothing"
                )

                # Verify drugs exist in DB
                count_result = await session.execute(
                    select(func.count(Drug.id)).where(Drug.status == "approved")
                )
                drug_count = count_result.scalar() or 0
                assert drug_count > 0, (
                    f"0 approved drugs in DB after DrugBank ingestion! "
                    f"Connector reported {result['records_processed']} processed."
                )

                # Verify ingestion log
                log_result = await session.execute(
                    select(IngestionLog)
                    .where(IngestionLog.source == "drugbank")
                    .order_by(IngestionLog.id.desc())
                    .limit(1)
                )
                log = log_result.scalar_one_or_none()
                assert log is not None, "No ingestion log found for drugbank"
                assert log.status == "completed", f"Log status={log.status}"
                assert log.records_processed > 0, f"Log records_processed={log.records_processed}"
                assert log.total_expected is not None, "total_expected never set"

                logger.info(
                    "DrugBank full run OK: %d/%d records, %d drugs in DB",
                    log.records_processed, log.total_expected, drug_count,
                )
        run(_test())


# ---------------------------------------------------------------------------
# 4. PUBCHEM CONNECTOR (depends on DrugBank data)
# ---------------------------------------------------------------------------

class TestPubChemConnector:
    """Test PubChem enrichment finds existing drugs."""

    def test_finds_existing_drugs(self):
        """After DrugBank runs, PubChem should find approved drugs to enrich."""
        async def _test():
            from app.models.drug import Drug
            from sqlalchemy import select

            async with await make_session() as session:
                result = await session.execute(
                    select(Drug.id, Drug.name, Drug.drugbank_id, Drug.smiles)
                    .where(Drug.status == "approved")
                )
                drugs = result.all()

                assert len(drugs) > 0, (
                    "PubChem enrichment will find 0 drugs! "
                    "DrugBank must run first. Check that DrugBank actually "
                    "committed drug rows (test_full_connector_run_small)."
                )
                logger.info(
                    "PubChem dependency OK: %d approved drugs available for enrichment",
                    len(drugs),
                )
        run(_test())


# ---------------------------------------------------------------------------
# 5. CHEMBL CONNECTOR (depends on DrugBank data)
# ---------------------------------------------------------------------------

class TestChEMBLConnector:
    """Test ChEMBL can resolve drugs via DrugBank IDs."""

    def test_resolve_molecule_to_drugbank_id(self):
        """Verify ChEMBL molecule -> DrugBank ID resolution works."""
        async def _test():
            from app.services.ingestion.chembl import ChEMBLConnector
            connector = ChEMBLConnector()
            client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
            try:
                # CHEMBL941 = Metformin
                drugbank_id = await connector._resolve_molecule_to_drugbank(
                    client, "CHEMBL1431"
                )
                logger.info("ChEMBL molecule CHEMBL1431 -> DrugBank ID: %s", drugbank_id)
                # May return None if ChEMBL doesn't map to DrugBank.
                # Key test: does the API call succeed without errors?
            finally:
                await client.aclose()
        run(_test())

    def test_finds_existing_drugs_for_affinity(self):
        """After DrugBank runs, ChEMBL should find drugs for binding affinity fetch."""
        async def _test():
            from app.models.drug import Drug
            from sqlalchemy import select

            async with await make_session() as session:
                result = await session.execute(
                    select(Drug.id, Drug.name, Drug.drugbank_id)
                    .where(Drug.status == "approved")
                    .limit(5)
                )
                drugs = result.all()
                assert len(drugs) > 0, (
                    "ChEMBL Phase 2 (binding affinities) will find 0 drugs! "
                    "DrugBank must insert drugs first."
                )
                logger.info(
                    "ChEMBL dependency OK: %d approved drugs (sample: %s)",
                    len(drugs),
                    [d.name for d in drugs[:3]],
                )
        run(_test())


# ---------------------------------------------------------------------------
# 6. CBIOPORTAL CONNECTOR
# ---------------------------------------------------------------------------

class TestCBioPortalConnector:
    """Test cBioPortal can discover cancer types and fetch mutations."""

    def test_fetch_studies_and_filter_tcga(self):
        """Verify cBioPortal study discovery returns TCGA studies."""
        async def _test():
            client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
            try:
                resp = await client.get(
                    "https://www.cbioportal.org/api/studies",
                    params={"projection": "DETAILED", "pageSize": 1000},
                )
                assert resp.status_code == 200
                studies = resp.json()

                tcga = [s for s in studies if s.get("studyId", "").endswith("_tcga")]
                assert len(tcga) > 0, "No TCGA studies found"
                logger.info(
                    "cBioPortal OK: %d total studies, %d TCGA studies. "
                    "Sample: %s",
                    len(studies), len(tcga),
                    [s["studyId"] for s in tcga[:5]],
                )
            finally:
                await client.aclose()
        run(_test())

    def test_fetch_mutations_for_single_study(self):
        """Fetch mutations for one TCGA study to verify pagination works."""
        async def _test():
            client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
            try:
                # First get molecular profiles for brca_tcga
                resp = await client.get(
                    "https://www.cbioportal.org/api/studies/brca_tcga/molecular-profiles"
                )
                assert resp.status_code == 200
                profiles = resp.json()
                mut_profiles = [
                    p for p in profiles
                    if p.get("molecularAlterationType") == "MUTATION_EXTENDED"
                ]
                assert len(mut_profiles) > 0, "No mutation profiles for brca_tcga"

                profile_id = mut_profiles[0]["molecularProfileId"]

                # Fetch first page of mutations
                resp = await client.get(
                    f"https://www.cbioportal.org/api/molecular-profiles/{profile_id}/mutations",
                    params={
                        "sampleListId": "brca_tcga_all",
                        "projection": "SUMMARY",
                        "pageSize": 100,
                        "pageNumber": 0,
                    },
                )
                assert resp.status_code == 200
                mutations = resp.json()
                assert len(mutations) > 0, "No mutations returned for brca_tcga"
                logger.info(
                    "cBioPortal mutations OK: %d mutations on page 0 for %s",
                    len(mutations), profile_id,
                )
            finally:
                await client.aclose()
        run(_test())


# ---------------------------------------------------------------------------
# 7. PROGRESS TRACKING
# ---------------------------------------------------------------------------

class TestProgressTracking:
    """Verify BaseConnector progress tracking writes to DB correctly."""

    def test_create_log_and_flush_progress(self):
        """Verify ingestion log creation and progress flushing."""
        async def _test():
            from app.models.ingestion_log import IngestionLog
            from app.services.ingestion.base import BaseConnector
            from sqlalchemy import select, delete

            # Create a minimal concrete connector for testing
            class DummyConnector(BaseConnector):
                def get_source_name(self):
                    return "_diagnostic_test"
                async def fetch_data(self, session):
                    return []
                def transform_record(self, raw):
                    return raw

            async with await make_session() as session:
                # Cleanup old test logs
                await session.execute(
                    delete(IngestionLog).where(
                        IngestionLog.source == "_diagnostic_test"
                    )
                )
                await session.commit()

                connector = DummyConnector(db_session=session)

                # Create log
                log_id = await connector._create_log(session, "test")
                await session.commit()
                assert log_id is not None

                # Set total expected
                connector._log_id = log_id
                await connector.set_total_expected(session, 100)

                # Flush progress
                connector._records_processed = 42
                await connector._flush_progress(session)

                # Read back
                result = await session.execute(
                    select(IngestionLog).where(IngestionLog.id == log_id)
                )
                log = result.scalar_one()
                assert log.status == "running"
                assert log.total_expected == 100, f"total_expected={log.total_expected}"
                assert log.records_processed == 42, f"records_processed={log.records_processed}"

                logger.info(
                    "Progress tracking OK: log_id=%d, %d/%d",
                    log_id, log.records_processed, log.total_expected,
                )

                # Cleanup
                await session.execute(
                    delete(IngestionLog).where(IngestionLog.id == log_id)
                )
                await session.commit()
        run(_test())

    def test_duplicate_guard_blocks_recent_completion(self):
        """Verify the duplicate guard skips if source completed within 10 min."""
        async def _test():
            from app.models.ingestion_log import IngestionLog
            from app.services.ingestion.base import BaseConnector
            from sqlalchemy import delete

            class DummyConnector(BaseConnector):
                def get_source_name(self):
                    return "_guard_test"
                async def fetch_data(self, session):
                    return []
                def transform_record(self, raw):
                    return raw

            async with await make_session() as session:
                # Cleanup
                await session.execute(
                    delete(IngestionLog).where(
                        IngestionLog.source == "_guard_test"
                    )
                )
                await session.commit()

                # Insert a "completed" log from 2 minutes ago
                recent_log = IngestionLog(
                    source="_guard_test",
                    task_type="test",
                    status="completed",
                    records_processed=10,
                    errors=[],
                    started_at=datetime.now(timezone.utc),
                    completed_at=datetime.now(timezone.utc),
                )
                session.add(recent_log)
                await session.commit()

                # Now try to run — should be skipped
                connector = DummyConnector(db_session=session)
                result = await connector.run()
                assert result["status"] == "skipped_duplicate", (
                    f"Guard did NOT block: status={result['status']}"
                )
                logger.info("Duplicate guard OK: correctly blocked re-run")

                # Cleanup
                await session.execute(
                    delete(IngestionLog).where(
                        IngestionLog.source == "_guard_test"
                    )
                )
                await session.commit()
        run(_test())


# ---------------------------------------------------------------------------
# 8. PIPELINE ORCHESTRATION
# ---------------------------------------------------------------------------

class TestPipelineOrchestration:
    """Test that ingest_all_drugs sequencing is correct."""

    def test_all_drugs_task_runs_drugbank_then_parallel(self):
        """Verify ingest_all_drugs runs DrugBank first, then PubChem+ChEMBL.

        This is a structural test — it doesn't run the actual tasks, just
        verifies the orchestration code calls them in the right order.
        """
        from unittest.mock import patch, MagicMock

        with patch("app.tasks.ingest.ingest_drugbank") as mock_db, \
             patch("app.tasks.ingest.ingest_pubchem") as mock_pc, \
             patch("app.tasks.ingest.ingest_chembl") as mock_ch, \
             patch("app.tasks.ingest._drug_ingestion_complete") as mock_cb:

            # Mock DrugBank apply() result
            mock_result = MagicMock()
            mock_result.id = "test-id"
            mock_result.get.return_value = {"status": "completed", "records_processed": 165}
            mock_db.apply.return_value = mock_result

            # Mock chord
            mock_pc.s.return_value = MagicMock()
            mock_ch.s.return_value = MagicMock()
            mock_cb.s.return_value = MagicMock()

            with patch("app.tasks.ingest.chord") as mock_chord:
                mock_chord_result = MagicMock()
                mock_chord_result.id = "chord-id"
                mock_chord.return_value.apply_async.return_value = mock_chord_result

                from app.tasks.ingest import ingest_all_drugs
                result = ingest_all_drugs()

                # Verify DrugBank ran first
                mock_db.apply.assert_called_once()
                mock_result.get.assert_called_once()

                # Verify PubChem and ChEMBL dispatched in parallel
                mock_pc.s.assert_called_once()
                mock_ch.s.assert_called_once()
                mock_chord.assert_called_once()

                logger.info("Pipeline orchestration OK: DrugBank -> [PubChem, ChEMBL]")
