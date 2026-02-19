"""Celery tasks for data ingestion from biomedical sources.

Each task:
  1. Creates an ingestion_log entry with status='running'
  2. Runs the corresponding connector
  3. Updates the log with completed/failed status and records_processed
  4. Is idempotent — re-running produces the same result via upserts
"""

import logging

from celery import chord

from app.tasks.celery_app import celery_app
from app.tasks.utils import run_async, task_session

logger = logging.getLogger(__name__)


async def _run_connector(connector_class, **kwargs):
    """Instantiate and run a connector, returning its result summary.

    Uses task_session() to create a task-local engine + session bound
    to the current event loop.
    """
    async with task_session() as session:
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

        result = run_async(_run_connector(DrugBankConnector, **kwargs))
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

        result = run_async(_run_connector(PubChemConnector))
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

        result = run_async(_run_connector(ChEMBLConnector))
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

        result = run_async(_run_connector(CBioPortalConnector))
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

        result = run_async(_run_connector(TCGAConnector))
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

        result = run_async(_run_connector(COSMICConnector, **kwargs))
        logger.info(
            "COSMIC ingestion complete: %d records, %d errors",
            result["records_processed"], result["errors_count"],
        )
        return result

    except Exception as exc:
        logger.error("COSMIC ingestion failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(bind=True, max_retries=3, name="app.tasks.ingest.ingest_depmap")
def ingest_depmap(self, gene_effect_csv_path=None):
    """Ingest CRISPR gene dependency data from DepMap.

    Mode A: Parse a CRISPRGeneEffect.csv file (provide gene_effect_csv_path)
    Mode B: Use curated dependency data from published DepMap findings
    """
    logger.info("Starting DepMap ingestion task")
    try:
        from app.services.ingestion.depmap import DepMapConnector

        kwargs = {}
        if gene_effect_csv_path:
            kwargs["gene_effect_csv_path"] = gene_effect_csv_path

        result = run_async(_run_connector(DepMapConnector, **kwargs))
        logger.info(
            "DepMap ingestion complete: %d records, %d errors",
            result["records_processed"], result["errors_count"],
        )
        return result

    except Exception as exc:
        logger.error("DepMap ingestion failed: %s", exc)
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

        result = run_async(_run_connector(KEGGConnector))
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

        result = run_async(_run_connector(ReactomeConnector))
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

        result = run_async(_run_connector(STRINGConnector))
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

        result = run_async(_run_connector(UniProtConnector))
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

        result = run_async(_run_connector(OpenTargetsConnector))
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


# ------------------------------------------------------------------
# Literature mining tasks
# ------------------------------------------------------------------


@celery_app.task(bind=True, max_retries=3, name="app.tasks.ingest.ingest_literature")
def ingest_literature(self, phase="all"):
    """Ingest PubMed literature in 3 phases.

    This is a LONG task — potentially 6-12 hours for full ingestion.

    Phase 1: Targeted drug-cancer pairs (~10K papers) — ~1-2 hours
    Phase 2: Broader drug-cancer literature (~75K papers) — ~3-5 hours
    Phase 3: Target-focused literature (~20K papers) — ~1-2 hours

    Each phase checkpoints progress. If the task fails and restarts,
    it skips already-ingested papers (PMID uniqueness constraint).
    """
    logger.info("Starting PubMed literature ingestion (phase=%s)", phase)
    try:
        from app.services.ingestion.pubmed import PubMedConnector

        result = run_async(_run_connector(PubMedConnector, phase=phase))
        logger.info(
            "PubMed ingestion complete: %d records, %d errors",
            result["records_processed"], result["errors_count"],
        )
        return result

    except Exception as exc:
        logger.error("PubMed ingestion failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(bind=True, max_retries=3, name="app.tasks.ingest.ingest_clinical_trials")
def ingest_clinical_trials(self):
    """Fetch clinical trial data from ClinicalTrials.gov for all drugs."""
    logger.info("Starting ClinicalTrials.gov ingestion")
    try:
        from app.services.ingestion.clinicaltrials import ClinicalTrialsConnector

        result = run_async(_run_connector(ClinicalTrialsConnector))
        logger.info(
            "ClinicalTrials.gov ingestion complete: %d records, %d errors",
            result["records_processed"], result["errors_count"],
        )
        return result

    except Exception as exc:
        logger.error("ClinicalTrials.gov ingestion failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(bind=True, max_retries=3, name="app.tasks.ingest.generate_embeddings")
def generate_embeddings(self):
    """Generate vector embeddings for all un-embedded records.

    Processes:
    1. Literature abstracts without embeddings (~50-80K)
    2. Target descriptions without embeddings (~5K)
    3. Drug mechanisms without embeddings (~2.5K)

    After bulk insertion, creates/refreshes IVFFlat indexes.
    Runtime: ~15-30 minutes on CPU for all records.
    """
    logger.info("Starting embedding generation")
    try:

        async def _generate():
            from app.services.embedding import EmbeddingService

            svc = EmbeddingService()
            async with task_session() as session:
                lit_count = await svc.embed_literature(session)
                target_count = await svc.embed_targets(session)
                drug_count = await svc.embed_drugs(session)
                await svc.create_vector_indexes(session)
                return {
                    "literature_embedded": lit_count,
                    "targets_embedded": target_count,
                    "drugs_embedded": drug_count,
                }

        result = run_async(_generate())
        logger.info("Embedding generation complete: %s", result)
        return {"records_processed": sum(result.values()), "errors_count": 0, **result}

    except Exception as exc:
        logger.error("Embedding generation failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(bind=True, name="app.tasks.ingest.analyze_literature_batch")
def analyze_literature_batch(self, pmid_list=None, limit=1000):
    """Run Claude-based abstract extraction on un-analyzed papers.

    If pmid_list provided, analyze those specific papers.
    Otherwise, analyze the next {limit} un-analyzed papers, prioritizing:
    1. Papers linked to drug-cancer pairs (Phase 1 papers)
    2. Papers mentioning both a drug and cancer
    3. Remaining papers

    Rate limited to 50 Claude API calls/minute.
    Stores extracted findings in literature.extracted_findings JSONB column.
    """
    logger.info(
        "Starting literature analysis batch (pmids=%s, limit=%d)",
        len(pmid_list) if pmid_list else "auto",
        limit,
    )
    try:

        async def _analyze():
            from app.services.literature_analyzer import LiteratureAnalyzer

            analyzer = LiteratureAnalyzer()
            async with task_session() as session:
                return await analyzer.analyze_batch(
                    session, pmid_list=pmid_list, limit=limit
                )

        result = run_async(_analyze())
        logger.info(
            "Literature analysis complete: %d analyzed, %d failed",
            result["analyzed"], result["failed"],
        )
        return {
            "records_processed": result["analyzed"],
            "errors_count": result["failed"],
            **result,
        }

    except Exception as exc:
        logger.error("Literature analysis failed: %s", exc)
        return {"records_processed": 0, "errors_count": 1, "error": str(exc)}


@celery_app.task(name="app.tasks.ingest.ingest_all_literature")
def ingest_all_literature():
    """Run full literature pipeline:

    1. PubMed ingestion (all 3 phases)
    2. ClinicalTrials.gov ingestion
    3. Embedding generation
    4. Abstract analysis (top 1000 most relevant papers)
    """
    logger.info("Starting full literature ingestion pipeline")

    # Step 1: PubMed literature
    lit_result = ingest_literature.apply()
    lit_result.get(timeout=43200)  # 12h timeout

    # Step 2: Clinical trials
    ct_result = ingest_clinical_trials.apply()
    ct_result.get(timeout=7200)  # 2h timeout

    # Step 3: Embeddings
    emb_result = generate_embeddings.apply()
    emb_result.get(timeout=3600)  # 1h timeout

    # Step 4: Analyze top 1000 papers
    analysis_result = analyze_literature_batch.apply(kwargs={"limit": 1000})
    analysis_result.get(timeout=7200)  # 2h timeout

    return {
        "status": "completed",
        "literature_task_id": lit_result.id,
        "clinical_trials_task_id": ct_result.id,
        "embeddings_task_id": emb_result.id,
        "analysis_task_id": analysis_result.id,
    }


@celery_app.task(name="app.tasks.ingest.literature_pipeline_complete")
def _literature_pipeline_complete(results):
    """Callback after literature pipeline completes."""
    logger.info("Literature pipeline complete. Results: %s", results)
    return {"status": "completed", "source_results": results}


# ------------------------------------------------------------------
# Knowledge graph sync tasks
# ------------------------------------------------------------------


@celery_app.task(bind=True, max_retries=2, name="app.tasks.ingest.sync_knowledge_graph")
def sync_knowledge_graph(self):
    """Full PostgreSQL -> Neo4j synchronization.

    Reads all relevant data from Postgres and creates/updates
    nodes and edges in Neo4j. Idempotent — safe to run multiple times.
    Runtime: ~5-15 minutes depending on data volume.
    Should be run after any major data ingestion.
    """
    logger.info("Starting knowledge graph sync")
    try:

        async def _sync():
            from app.services.knowledge_graph import KnowledgeGraphService

            kg = KnowledgeGraphService()
            try:
                async with task_session() as session:
                    return await kg.full_sync(session)
            finally:
                await kg.close()

        result = run_async(_sync())
        logger.info("Knowledge graph sync complete: %s", result)
        return {
            "records_processed": result.get("total_nodes", 0) + result.get("total_edges", 0),
            "errors_count": 0,
            **result,
        }

    except Exception as exc:
        logger.error("Knowledge graph sync failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
