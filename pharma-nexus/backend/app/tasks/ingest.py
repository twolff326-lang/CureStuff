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
