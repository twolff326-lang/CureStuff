from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.ingestion_log import IngestionLog
from app.tasks.celery_app import celery_app

router = APIRouter()

VALID_SOURCES = {
    "drugbank", "pubchem", "chembl", "all_drugs",
    "cbioportal", "tcga", "cosmic", "all_cancer_data",
    "kegg", "reactome", "string", "uniprot", "opentargets", "all_pathways",
    "literature", "clinical_trials", "embeddings", "literature_analysis",
    "all_literature", "knowledge_graph",
    "differential_expression", "expression_scores", "pathway_activity",
    "full_expression_analysis",
    "hypotheses_cancer", "hypotheses_drug", "hypotheses_all", "rescore_hypotheses",
    "llm_narratives", "llm_full_analysis", "llm_comparative", "llm_single_analysis",
}


class IngestionRequest(BaseModel):
    source: str
    xml_path: str | None = None
    census_tsv_path: str | None = None
    phase: str | None = None  # For literature ingestion: "phase1", "phase2", "phase3", or "all"
    pmid_list: list[str] | None = None  # For literature_analysis
    limit: int | None = None  # For literature_analysis batch size
    cancer_type_id: int | None = None  # For expression analysis tasks
    drug_id: int | None = None  # For hypothesis generation per drug
    min_score: float | None = None  # For hypothesis generation threshold
    preset_name: str | None = None  # For hypothesis rescoring
    hypothesis_id: int | None = None  # For single LLM analysis
    analysis_type: str | None = None  # For single LLM analysis type


@router.post("/start")
async def start_ingestion(request: IngestionRequest):
    """Kick off a data ingestion Celery task.

    Body: {"source": "drugbank"|"pubchem"|"chembl"|"all_drugs", "xml_path": "..."}
    Returns: {"task_id": "...", "status": "queued"}
    """
    source = request.source.lower()
    if source not in VALID_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source '{source}'. Must be one of: {', '.join(sorted(VALID_SOURCES))}",
        )

    task_map = {
        "drugbank": "app.tasks.ingest.ingest_drugbank",
        "pubchem": "app.tasks.ingest.ingest_pubchem",
        "chembl": "app.tasks.ingest.ingest_chembl",
        "all_drugs": "app.tasks.ingest.ingest_all_drugs",
        "cbioportal": "app.tasks.ingest.ingest_cbioportal",
        "tcga": "app.tasks.ingest.ingest_tcga",
        "cosmic": "app.tasks.ingest.ingest_cosmic",
        "all_cancer_data": "app.tasks.ingest.ingest_all_cancer_data",
        "kegg": "app.tasks.ingest.ingest_kegg",
        "reactome": "app.tasks.ingest.ingest_reactome",
        "string": "app.tasks.ingest.ingest_string",
        "uniprot": "app.tasks.ingest.ingest_uniprot",
        "opentargets": "app.tasks.ingest.ingest_opentargets",
        "all_pathways": "app.tasks.ingest.ingest_all_pathways",
        "literature": "app.tasks.ingest.ingest_literature",
        "clinical_trials": "app.tasks.ingest.ingest_clinical_trials",
        "embeddings": "app.tasks.ingest.generate_embeddings",
        "literature_analysis": "app.tasks.ingest.analyze_literature_batch",
        "all_literature": "app.tasks.ingest.ingest_all_literature",
        "knowledge_graph": "app.tasks.ingest.sync_knowledge_graph",
        "differential_expression": "app.tasks.analyze.compute_all_differential_expression",
        "expression_scores": "app.tasks.analyze.compute_drug_expression_scores",
        "pathway_activity": "app.tasks.analyze.compute_pathway_activities",
        "full_expression_analysis": "app.tasks.analyze.run_full_expression_analysis",
        "hypotheses_cancer": "app.tasks.generate.generate_hypotheses_for_cancer",
        "hypotheses_drug": "app.tasks.generate.generate_hypotheses_for_drug",
        "hypotheses_all": "app.tasks.generate.generate_all_hypotheses",
        "rescore_hypotheses": "app.tasks.generate.rescore_hypotheses",
        "llm_narratives": "app.tasks.analyze.generate_narratives_batch",
        "llm_full_analysis": "app.tasks.analyze.generate_full_analysis_batch",
        "llm_comparative": "app.tasks.analyze.generate_comparative_analyses",
        "llm_single_analysis": "app.tasks.analyze.generate_single_analysis",
    }

    task_name = task_map[source]
    kwargs = {}
    if source in ("drugbank", "all_drugs") and request.xml_path:
        kwargs["xml_path"] = request.xml_path
    if source in ("cosmic", "all_cancer_data") and request.census_tsv_path:
        kwargs["census_tsv_path"] = request.census_tsv_path
    if source == "literature" and request.phase:
        kwargs["phase"] = request.phase
    if source == "literature_analysis":
        if request.pmid_list:
            kwargs["pmid_list"] = request.pmid_list
        if request.limit:
            kwargs["limit"] = request.limit
    if source in (
        "differential_expression", "expression_scores",
        "pathway_activity", "full_expression_analysis",
    ) and request.cancer_type_id:
        kwargs["cancer_type_id"] = request.cancer_type_id
    if source == "hypotheses_cancer" and request.cancer_type_id:
        kwargs["cancer_type_id"] = request.cancer_type_id
    if source == "hypotheses_drug" and request.drug_id:
        kwargs["drug_id"] = request.drug_id
    if source in ("hypotheses_cancer", "hypotheses_drug", "hypotheses_all") and request.min_score:
        kwargs["min_score"] = request.min_score
    if source == "rescore_hypotheses" and request.preset_name:
        kwargs["preset_name"] = request.preset_name
    if source in ("llm_narratives", "llm_full_analysis") and request.min_score:
        kwargs["min_score"] = request.min_score
    if source in ("llm_narratives", "llm_full_analysis") and request.limit:
        kwargs["limit"] = request.limit
    if source == "llm_comparative":
        if request.cancer_type_id:
            kwargs["cancer_type_id"] = request.cancer_type_id
        if request.min_score:
            kwargs["min_score"] = request.min_score
    if source == "llm_single_analysis":
        if request.hypothesis_id:
            kwargs["hypothesis_id"] = request.hypothesis_id
        if request.analysis_type:
            kwargs["analysis_type"] = request.analysis_type

    task = celery_app.send_task(task_name, kwargs=kwargs)
    return {"task_id": task.id, "status": "queued", "source": source}


@router.get("/status/{task_id}")
async def get_ingestion_status(
    task_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get status of a running ingestion task.

    Returns Celery task state plus matching ingestion_log entry.
    """
    result = celery_app.AsyncResult(task_id)
    task_state = result.state
    task_result = None

    if result.ready():
        try:
            task_result = result.result
        except Exception:
            task_result = {"error": str(result.result)}

    # Try to find matching ingestion log (most recent for the task)
    log_entry = None
    log_result = await db.execute(
        select(IngestionLog)
        .order_by(IngestionLog.started_at.desc())
        .limit(1)
    )
    log = log_result.scalar_one_or_none()
    if log:
        log_entry = {
            "id": log.id,
            "source": log.source,
            "task_type": log.task_type,
            "status": log.status,
            "records_processed": log.records_processed,
            "errors": log.errors,
            "started_at": log.started_at.isoformat() if log.started_at else None,
            "completed_at": log.completed_at.isoformat() if log.completed_at else None,
        }

    return {
        "task_id": task_id,
        "task_state": task_state,
        "task_result": task_result,
        "ingestion_log": log_entry,
    }


@router.get("/record-counts")
async def get_record_counts(
    db: AsyncSession = Depends(get_db),
):
    """Return row counts for every major data table.

    Used by the Ingested Data page to show tab header counts and give
    an at-a-glance view of how much data has been ingested.
    """
    from app.models.drug import Drug, DrugTarget
    from app.models.target import Target, ProteinInteraction
    from app.models.cancer_type import CancerType, CancerMolecularProfile
    from app.models.mutation import Mutation
    from app.models.pathway import Pathway, PathwayTarget
    from app.models.literature import Literature
    from app.models.clinical_trial import ClinicalTrial
    from app.models.evidence import Bioassay

    tables = {
        "drugs": Drug,
        "targets": Target,
        "drug_targets": DrugTarget,
        "cancer_types": CancerType,
        "molecular_profiles": CancerMolecularProfile,
        "mutations": Mutation,
        "pathways": Pathway,
        "pathway_targets": PathwayTarget,
        "protein_interactions": ProteinInteraction,
        "literature": Literature,
        "clinical_trials": ClinicalTrial,
        "bioassays": Bioassay,
    }

    counts: dict[str, int] = {}
    for key, model in tables.items():
        result = await db.execute(select(func.count(model.id)))
        counts[key] = result.scalar() or 0

    counts["total"] = sum(counts.values())
    return {"counts": counts}


@router.get("/live-status")
async def get_live_status(
    db: AsyncSession = Depends(get_db),
):
    """Return the most recent ingestion log for every source.

    Used by the frontend to poll for realtime progress across all data
    source cards simultaneously.  Running sources include the live
    ``records_processed`` count that gets flushed every batch.
    """
    # Subquery: latest log id per source
    latest_per_source = (
        select(
            IngestionLog.source,
            func.max(IngestionLog.id).label("max_id"),
        )
        .group_by(IngestionLog.source)
        .subquery()
    )

    result = await db.execute(
        select(IngestionLog)
        .join(
            latest_per_source,
            (IngestionLog.source == latest_per_source.c.source)
            & (IngestionLog.id == latest_per_source.c.max_id),
        )
    )
    logs = result.scalars().all()

    sources: dict[str, dict] = {}
    for log in logs:
        sources[log.source] = {
            "id": log.id,
            "source": log.source,
            "task_type": log.task_type,
            "status": log.status,
            "records_processed": log.records_processed,
            "errors": log.errors,
            "started_at": log.started_at.isoformat() if log.started_at else None,
            "completed_at": log.completed_at.isoformat() if log.completed_at else None,
        }

    return {"sources": sources}


@router.get("/logs")
async def get_ingestion_logs(
    source: str | None = Query(None, description="Filter by source name"),
    status: str | None = Query(None, description="Filter by status"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
):
    """Paginated list of all ingestion runs, sorted by started_at desc."""
    query = select(IngestionLog)
    count_query = select(func.count(IngestionLog.id))

    if source:
        query = query.where(IngestionLog.source == source)
        count_query = count_query.where(IngestionLog.source == source)
    if status:
        query = query.where(IngestionLog.status == status)
        count_query = count_query.where(IngestionLog.status == status)

    # Get total count
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Get paginated results
    offset = (page - 1) * per_page
    query = query.order_by(IngestionLog.started_at.desc()).offset(offset).limit(per_page)
    result = await db.execute(query)
    logs = result.scalars().all()

    return {
        "logs": [
            {
                "id": log.id,
                "source": log.source,
                "task_type": log.task_type,
                "status": log.status,
                "records_processed": log.records_processed,
                "errors": log.errors,
                "started_at": log.started_at.isoformat() if log.started_at else None,
                "completed_at": log.completed_at.isoformat() if log.completed_at else None,
            }
            for log in logs
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }
