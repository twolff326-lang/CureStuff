"""Celery tasks for data ingestion from biomedical sources.

Each task:
  1. Creates an ingestion_log entry with status='running'
  2. Runs the corresponding connector
  3. Updates the log with completed/failed status and records_processed
  4. Is idempotent — re-running produces the same result via upserts
"""

import asyncio
import logging
from datetime import datetime, timezone

from celery import chord

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run an async coroutine from a sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _run_connector(connector_class, **kwargs):
    """Instantiate and run a connector, returning its result summary."""
    from app.database import async_session_factory

    async with async_session_factory() as session:
        connector = connector_class(db_session=session, **kwargs)
        return await connector.run()


@celery_app.task(bind=True, max_retries=3, name="app.tasks.ingest.ingest_drugbank")
def ingest_drugbank(self, xml_path=None):
    """Ingest drug data from DrugBank XML or PubChem API fallback."""
    logger.info("Starting DrugBank ingestion task")
    try:
        from app.services.ingestion.drugbank import DrugBankConnector

        kwargs = {}
        if xml_path:
            kwargs["xml_path"] = xml_path

        result = _run_async(_run_connector(DrugBankConnector, **kwargs))
        logger.info(
            "DrugBank ingestion complete: %d records, %d errors",
            result["records_processed"], result["errors_count"],
        )
        return result

    except Exception as exc:
        logger.error("DrugBank ingestion failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(bind=True, max_retries=3, name="app.tasks.ingest.ingest_pubchem")
def ingest_pubchem(self):
    """Ingest drug enrichment and bioassay data from PubChem."""
    logger.info("Starting PubChem ingestion task")
    try:
        from app.services.ingestion.pubchem import PubChemConnector

        result = _run_async(_run_connector(PubChemConnector))
        logger.info(
            "PubChem ingestion complete: %d records, %d errors",
            result["records_processed"], result["errors_count"],
        )
        return result

    except Exception as exc:
        logger.error("PubChem ingestion failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(bind=True, max_retries=3, name="app.tasks.ingest.ingest_chembl")
def ingest_chembl(self):
    """Ingest drug-target binding affinity data from ChEMBL."""
    logger.info("Starting ChEMBL ingestion task")
    try:
        from app.services.ingestion.chembl import ChEMBLConnector

        result = _run_async(_run_connector(ChEMBLConnector))
        logger.info(
            "ChEMBL ingestion complete: %d records, %d errors",
            result["records_processed"], result["errors_count"],
        )
        return result

    except Exception as exc:
        logger.error("ChEMBL ingestion failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(name="app.tasks.ingest.ingest_all_drugs")
def ingest_all_drugs(xml_path=None):
    """Run all drug ingestion tasks: DrugBank first, then PubChem + ChEMBL in parallel.

    DrugBank runs first because PubChem and ChEMBL enrich existing drug records.
    After DrugBank completes, PubChem and ChEMBL run in parallel.
    """
    logger.info("Starting full drug ingestion pipeline")

    # Run DrugBank first (other sources depend on these records existing)
    drugbank_result = ingest_drugbank.apply(kwargs={"xml_path": xml_path})
    drugbank_result.get(timeout=3600)

    # Then run PubChem and ChEMBL in parallel via chord
    parallel_tasks = chord(
        [ingest_pubchem.s(), ingest_chembl.s()],
        _drug_ingestion_complete.s(),
    )
    result = parallel_tasks.apply_async()
    return {
        "status": "pipeline_started",
        "drugbank_task_id": drugbank_result.id,
        "parallel_task_id": result.id,
    }


@celery_app.task(name="app.tasks.ingest.drug_ingestion_complete")
def _drug_ingestion_complete(results):
    """Callback after PubChem and ChEMBL ingestion complete."""
    logger.info("Drug ingestion pipeline complete. Results: %s", results)
    return {
        "status": "completed",
        "source_results": results,
    }


# ------------------------------------------------------------------
# Cancer genomics ingestion tasks
# ------------------------------------------------------------------


@celery_app.task(bind=True, max_retries=3, name="app.tasks.ingest.ingest_cbioportal")
def ingest_cbioportal(self):
    """Ingest TCGA cancer genomics data from cBioPortal (primary source)."""
    logger.info("Starting cBioPortal ingestion task")
    try:
        from app.services.ingestion.cbioportal import CBioPortalConnector

        result = _run_async(_run_connector(CBioPortalConnector))
        logger.info(
            "cBioPortal ingestion complete: %d records, %d errors",
            result["records_processed"], result["errors_count"],
        )
        return result

    except Exception as exc:
        logger.error("cBioPortal ingestion failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(bind=True, max_retries=3, name="app.tasks.ingest.ingest_tcga")
def ingest_tcga(self):
    """Ingest supplementary TCGA data from GDC (Genomic Data Commons)."""
    logger.info("Starting TCGA/GDC ingestion task")
    try:
        from app.services.ingestion.tcga import TCGAConnector

        result = _run_async(_run_connector(TCGAConnector))
        logger.info(
            "TCGA/GDC ingestion complete: %d records, %d errors",
            result["records_processed"], result["errors_count"],
        )
        return result

    except Exception as exc:
        logger.error("TCGA/GDC ingestion failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(bind=True, max_retries=3, name="app.tasks.ingest.ingest_cosmic")
def ingest_cosmic(self, census_tsv_path=None):
    """Ingest COSMIC cancer gene census and driver mutation data."""
    logger.info("Starting COSMIC ingestion task")
    try:
        from app.services.ingestion.cosmic import COSMICConnector

        kwargs = {}
        if census_tsv_path:
            kwargs["census_tsv_path"] = census_tsv_path

        result = _run_async(_run_connector(COSMICConnector, **kwargs))
        logger.info(
            "COSMIC ingestion complete: %d records, %d errors",
            result["records_processed"], result["errors_count"],
        )
        return result

    except Exception as exc:
        logger.error("COSMIC ingestion failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(name="app.tasks.ingest.ingest_all_cancer_data")
def ingest_all_cancer_data(census_tsv_path=None):
    """Run all cancer data ingestion: cBioPortal first, then TCGA + COSMIC in parallel.

    cBioPortal runs first because it creates the cancer_types records that
    TCGA and COSMIC depend on. After cBioPortal completes, TCGA and COSMIC
    run in parallel via chord.
    """
    logger.info("Starting full cancer data ingestion pipeline")

    # cBioPortal first (creates cancer types + primary mutation/expression data)
    cbio_result = ingest_cbioportal.apply()
    cbio_result.get(timeout=7200)  # 2h timeout for large datasets

    # Then TCGA and COSMIC in parallel
    parallel_tasks = chord(
        [
            ingest_tcga.s(),
            ingest_cosmic.s(census_tsv_path=census_tsv_path),
        ],
        _cancer_ingestion_complete.s(),
    )
    result = parallel_tasks.apply_async()
    return {
        "status": "pipeline_started",
        "cbioportal_task_id": cbio_result.id,
        "parallel_task_id": result.id,
    }


@celery_app.task(name="app.tasks.ingest.cancer_ingestion_complete")
def _cancer_ingestion_complete(results):
    """Callback after TCGA and COSMIC ingestion complete."""
    logger.info("Cancer data ingestion pipeline complete. Results: %s", results)
    return {
        "status": "completed",
        "source_results": results,
    }
