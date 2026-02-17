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


# ------------------------------------------------------------------
# Pathway & protein interaction ingestion tasks
# ------------------------------------------------------------------


@celery_app.task(bind=True, max_retries=3, name="app.tasks.ingest.ingest_kegg")
def ingest_kegg(self):
    """Ingest KEGG human pathway data (~340 pathways)."""
    logger.info("Starting KEGG ingestion task")
    try:
        from app.services.ingestion.kegg import KEGGConnector

        result = _run_async(_run_connector(KEGGConnector))
        logger.info(
            "KEGG ingestion complete: %d records, %d errors",
            result["records_processed"], result["errors_count"],
        )
        return result

    except Exception as exc:
        logger.error("KEGG ingestion failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(bind=True, max_retries=3, name="app.tasks.ingest.ingest_reactome")
def ingest_reactome(self):
    """Ingest Reactome pathway data with hierarchy."""
    logger.info("Starting Reactome ingestion task")
    try:
        from app.services.ingestion.reactome import ReactomeConnector

        result = _run_async(_run_connector(ReactomeConnector))
        logger.info(
            "Reactome ingestion complete: %d records, %d errors",
            result["records_processed"], result["errors_count"],
        )
        return result

    except Exception as exc:
        logger.error("Reactome ingestion failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(bind=True, max_retries=3, name="app.tasks.ingest.ingest_string")
def ingest_string(self):
    """Ingest STRING protein-protein interactions for all drug targets."""
    logger.info("Starting STRING ingestion task")
    try:
        from app.services.ingestion.string_db import STRINGConnector

        result = _run_async(_run_connector(STRINGConnector))
        logger.info(
            "STRING ingestion complete: %d records, %d errors",
            result["records_processed"], result["errors_count"],
        )
        return result

    except Exception as exc:
        logger.error("STRING ingestion failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(bind=True, max_retries=3, name="app.tasks.ingest.ingest_uniprot")
def ingest_uniprot(self):
    """Enrich target records with UniProt protein data."""
    logger.info("Starting UniProt ingestion task")
    try:
        from app.services.ingestion.uniprot import UniProtConnector

        result = _run_async(_run_connector(UniProtConnector))
        logger.info(
            "UniProt ingestion complete: %d records, %d errors",
            result["records_processed"], result["errors_count"],
        )
        return result

    except Exception as exc:
        logger.error("UniProt ingestion failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(bind=True, max_retries=3, name="app.tasks.ingest.ingest_opentargets")
def ingest_opentargets(self):
    """Fetch OpenTargets target-disease associations."""
    logger.info("Starting OpenTargets ingestion task")
    try:
        from app.services.ingestion.opentargets import OpenTargetsConnector

        result = _run_async(_run_connector(OpenTargetsConnector))
        logger.info(
            "OpenTargets ingestion complete: %d records, %d errors",
            result["records_processed"], result["errors_count"],
        )
        return result

    except Exception as exc:
        logger.error("OpenTargets ingestion failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(name="app.tasks.ingest.ingest_all_pathways")
def ingest_all_pathways():
    """Run all pathway/interaction ingestion.

    Order matters:
    1. UniProt first (enriches targets with Ensembl IDs needed by OpenTargets)
    2. KEGG + Reactome in parallel (pathways)
    3. STRING (needs complete target list)
    4. OpenTargets (needs Ensembl IDs from UniProt)
    """
    logger.info("Starting full pathway/interaction ingestion pipeline")

    # Step 1: UniProt enrichment first
    uniprot_result = ingest_uniprot.apply()
    uniprot_result.get(timeout=3600)

    # Step 2: KEGG + Reactome in parallel
    pathway_tasks = chord(
        [ingest_kegg.s(), ingest_reactome.s()],
        _pathway_phase_complete.s(),
    )
    pathway_result = pathway_tasks.apply_async()
    pathway_result.get(timeout=7200)

    # Step 3: STRING interactions
    string_result = ingest_string.apply()
    string_result.get(timeout=3600)

    # Step 4: OpenTargets (needs Ensembl IDs from step 1)
    ot_result = ingest_opentargets.apply()
    ot_result.get(timeout=3600)

    return {
        "status": "completed",
        "uniprot_task_id": uniprot_result.id,
        "pathway_task_id": pathway_result.id,
        "string_task_id": string_result.id,
        "opentargets_task_id": ot_result.id,
    }


@celery_app.task(name="app.tasks.ingest.pathway_phase_complete")
def _pathway_phase_complete(results):
    """Callback after KEGG and Reactome ingestion complete."""
    logger.info("Pathway ingestion phase complete. Results: %s", results)
    return {"status": "completed", "source_results": results}
