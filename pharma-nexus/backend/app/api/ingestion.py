import logging

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query
from neo4j import AsyncGraphDatabase
from pydantic import BaseModel
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.ingestion_log import IngestionLog
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

router = APIRouter()

VALID_SOURCES = {
    "drugbank", "pubchem", "chembl", "all_drugs",
    "cbioportal", "tcga", "cosmic", "all_cancer_data",
    "kegg", "reactome", "string", "uniprot", "opentargets", "all_pathways",
    "depmap", "prism",
    "literature", "clinical_trials", "embeddings", "literature_analysis",
    "all_literature", "knowledge_graph",
    "differential_expression", "expression_scores", "pathway_activity",
    "full_expression_analysis",
    "hypotheses_cancer", "hypotheses_drug", "hypotheses_all", "rescore_hypotheses",
    "combinations_cancer", "combinations_all",
    "llm_narratives", "llm_full_analysis", "llm_comparative", "llm_single_analysis",
    # GNN and monitoring
    "gnn_export", "gnn_train", "gnn_cache", "gnn_pipeline",
    "literature_check", "auto_monitor",
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
async def start_ingestion(
    request: IngestionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Kick off a data ingestion Celery task.

    Body: {"source": "drugbank"|"pubchem"|"chembl"|"all_drugs", "xml_path": "..."}
    Returns: {"task_id": "...", "status": "queued"}

    If the source already has a running task, returns 409 instead of
    spawning a duplicate.
    """
    source = request.source.lower()
    if source not in VALID_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source '{source}'. Must be one of: {', '.join(sorted(VALID_SOURCES))}",
        )

    # Guard: reject if this source already has a task in "running" state.
    # This prevents stale Celery retries (surviving Redis restarts) from
    # piling up duplicate runs.
    running_check = await db.execute(
        select(IngestionLog.id)
        .where(IngestionLog.source == source, IngestionLog.status == "running")
        .limit(1)
    )
    if running_check.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Source '{source}' already has a running task.",
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
        "depmap": "app.tasks.ingest.ingest_depmap",
        "prism": "app.tasks.ingest.ingest_prism",
        "combinations_cancer": "app.tasks.generate.generate_combinations_for_cancer",
        "combinations_all": "app.tasks.generate.generate_all_combinations",
        "llm_narratives": "app.tasks.analyze.generate_narratives_batch",
        "llm_full_analysis": "app.tasks.analyze.generate_full_analysis_batch",
        "llm_comparative": "app.tasks.analyze.generate_comparative_analyses",
        "llm_single_analysis": "app.tasks.analyze.generate_single_analysis",
        # GNN and monitoring
        "gnn_export": "app.tasks.gnn.export_graph",
        "gnn_train": "app.tasks.gnn.train_gnn",
        "gnn_cache": "app.tasks.gnn.cache_predictions",
        "gnn_pipeline": "app.tasks.gnn.full_gnn_pipeline",
        "literature_check": "app.tasks.monitor.check_literature",
        "auto_monitor": "app.tasks.monitor.auto_monitor_top",
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
    if source == "combinations_cancer" and request.cancer_type_id:
        kwargs["cancer_type_id"] = request.cancer_type_id
    if source in ("combinations_cancer", "combinations_all") and request.min_score:
        kwargs["min_single_score"] = request.min_score
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
    progress = None

    if task_state == "PROGRESS" and isinstance(result.info, dict):
        progress = result.info
    elif result.ready():
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
            "total_expected": log.total_expected,
            "errors": log.errors,
            "started_at": log.started_at.isoformat() if log.started_at else None,
            "completed_at": log.completed_at.isoformat() if log.completed_at else None,
        }

    return {
        "task_id": task_id,
        "task_state": task_state,
        "task_result": task_result,
        "progress": progress,
        "ingestion_log": log_entry,
    }


@router.post("/preflight")
async def run_preflight_checks():
    """Probe every external data source API with a single lightweight request.

    Returns connectivity status, response time, and sample data for each
    source — lets you verify all APIs are reachable and returning data
    before committing to a full ingestion run.
    """
    from app.services.ingestion.preflight import run_preflight_checks as _run

    results = await _run()

    total = len(results)
    reachable = sum(1 for r in results if r["reachable"])
    has_data = sum(1 for r in results if r["has_data"])
    failed = sum(1 for r in results if r["error"])

    return {
        "summary": {
            "total_sources": total,
            "reachable": reachable,
            "returning_data": has_data,
            "failed": failed,
            "all_ok": has_data == total,
        },
        "results": results,
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
    from app.models.gene_dependency import GeneDependency, CombinationHypothesis

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
        "gene_dependencies": GeneDependency,
        "combination_hypotheses": CombinationHypothesis,
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
            "total_expected": log.total_expected,
            "errors": log.errors,
            "started_at": log.started_at.isoformat() if log.started_at else None,
            "completed_at": log.completed_at.isoformat() if log.completed_at else None,
            "data_source_version": log.data_source_version,
            "data_downloaded_at": log.data_downloaded_at.isoformat() if log.data_downloaded_at else None,
            "records_filtered": log.records_filtered,
            "duration_seconds": log.duration_seconds,
        }

    return {"sources": sources}


@router.get("/diagnostic-report/{source}")
async def get_diagnostic_report(
    source: str,
    db: AsyncSession = Depends(get_db),
):
    """Generate a full diagnostic report for a source's most recent run.

    Returns a structured report with:
    - Run metadata (status, timing, record counts)
    - Errors grouped by category with full tracebacks
    - Suggested fixes per error category
    - A pre-formatted plain-text report suitable for pasting into
      Claude Code or an issue tracker
    """
    # Find latest log for this source
    result = await db.execute(
        select(IngestionLog)
        .where(IngestionLog.source == source)
        .order_by(IngestionLog.id.desc())
        .limit(1)
    )
    log = result.scalar_one_or_none()
    if log is None:
        raise HTTPException(status_code=404, detail=f"No ingestion log found for source '{source}'")

    errors = log.errors or []

    # Group errors by category
    by_category: dict[str, list] = {}
    for err in errors:
        cat = err.get("category", "unknown")
        by_category.setdefault(cat, []).append(err)

    # Group by context
    by_context: dict[str, list] = {}
    for err in errors:
        ctx = err.get("context", "unknown")
        by_context.setdefault(ctx, []).append(err)

    # Build the copy-paste plain-text report
    duration = ""
    if log.started_at and log.completed_at:
        secs = (log.completed_at - log.started_at).total_seconds()
        mins = int(secs // 60)
        rem = int(secs % 60)
        duration = f"{mins}m {rem}s" if mins else f"{rem}s"
    elif log.started_at:
        from datetime import timezone as tz
        secs = (datetime.now(tz.utc) - log.started_at).total_seconds()
        mins = int(secs // 60)
        rem = int(secs % 60)
        duration = f"{mins}m {rem}s (still running)" if mins else f"{rem}s (still running)"

    lines = [
        f"## Ingestion Diagnostic Report: {source}",
        f"",
        f"Status: {log.status}",
        f"Records: {log.records_processed}" + (f" / {log.total_expected}" if log.total_expected else ""),
        f"Duration: {duration}",
        f"Started: {log.started_at.isoformat() if log.started_at else 'N/A'}",
        f"Completed: {log.completed_at.isoformat() if log.completed_at else 'N/A'}",
        f"Total errors: {len(errors)}",
        f"",
    ]

    if errors:
        lines.append("### Error Summary by Category")
        lines.append("")
        for cat, cat_errors in sorted(by_category.items()):
            lines.append(f"**{cat}** ({len(cat_errors)}x)")
            # Show the suggested fix from the first error in this category
            fix = cat_errors[0].get("suggested_fix", "")
            if fix:
                lines.append(f"  Suggested fix: {fix}")
            lines.append("")

        lines.append("### Error Details")
        lines.append("")
        for i, err in enumerate(errors[:100], 1):  # Cap at 100 for the report
            lines.append(f"#### Error {i}: [{err.get('category', '?')}] {err.get('context', '?')}")
            lines.append(f"```")
            lines.append(f"Type: {err.get('type', '?')}")
            lines.append(f"Message: {err.get('error', '?')}")
            if err.get("record_id"):
                lines.append(f"Record ID: {err['record_id']}")
            http_info = err.get("http", {})
            if http_info:
                lines.append(f"URL: {http_info.get('url', '?')}")
                lines.append(f"HTTP Status: {http_info.get('status_code', '?')}")
                resp_body = http_info.get("response_body", "")
                if resp_body:
                    lines.append(f"Response: {resp_body[:500]}")
            tb = err.get("traceback", "")
            if tb:
                lines.append(f"")
                lines.append(f"Traceback:")
                lines.append(tb.rstrip())
            lines.append(f"```")
            lines.append("")

    plain_text_report = "\n".join(lines)

    return {
        "source": source,
        "log_id": log.id,
        "status": log.status,
        "records_processed": log.records_processed,
        "total_expected": log.total_expected,
        "started_at": log.started_at.isoformat() if log.started_at else None,
        "completed_at": log.completed_at.isoformat() if log.completed_at else None,
        "duration": duration,
        "total_errors": len(errors),
        "errors_by_category": {
            cat: {
                "count": len(errs),
                "suggested_fix": errs[0].get("suggested_fix", "") if errs else "",
                "sample_error": errs[0].get("error", "") if errs else "",
                "sample_type": errs[0].get("type", "") if errs else "",
            }
            for cat, errs in by_category.items()
        },
        "errors_by_context": {
            ctx: {
                "count": len(errs),
                "category": errs[0].get("category", "unknown"),
                "sample_error": errs[0].get("error", "") if errs else "",
            }
            for ctx, errs in by_context.items()
        },
        "errors": errors[:200],  # Full detail, capped at 200
        "plain_text_report": plain_text_report,
    }


@router.get("/full-report/{source}")
async def get_full_report(
    source: str,
    log_id: int | None = Query(None, description="Specific log ID; defaults to latest"),
    db: AsyncSession = Depends(get_db),
):
    """Comprehensive execution report for any ingestion run.

    Unlike /diagnostic-report (which focuses on errors), this returns
    everything that happened during the run — phases, HTTP call metrics,
    data quality stats, cache performance, timeline of events, warnings,
    and guard decisions — regardless of whether the run had errors.

    Returns both structured JSON and a pre-formatted plain-text report.
    """
    if log_id is not None:
        result = await db.execute(
            select(IngestionLog).where(
                IngestionLog.id == log_id,
                IngestionLog.source == source,
            )
        )
    else:
        result = await db.execute(
            select(IngestionLog)
            .where(IngestionLog.source == source)
            .order_by(IngestionLog.id.desc())
            .limit(1)
        )
    log = result.scalar_one_or_none()
    if log is None:
        raise HTTPException(
            status_code=404,
            detail=f"No ingestion log found for source '{source}'"
            + (f" with id={log_id}" if log_id else ""),
        )

    report = log.report or {}
    errors = log.errors or []

    # ── Duration calculation ──
    duration = ""
    if log.duration_seconds:
        mins = int(log.duration_seconds // 60)
        rem = int(log.duration_seconds % 60)
        duration = f"{mins}m {rem}s" if mins else f"{rem}s"
    elif log.started_at and log.completed_at:
        secs = (log.completed_at - log.started_at).total_seconds()
        mins = int(secs // 60)
        rem = int(secs % 60)
        duration = f"{mins}m {rem}s" if mins else f"{rem}s"
    elif log.started_at:
        from datetime import timezone as tz
        secs = (datetime.now(tz.utc) - log.started_at).total_seconds()
        mins = int(secs // 60)
        rem = int(secs % 60)
        duration = f"{mins}m {rem}s (still running)" if mins else f"{rem}s (still running)"

    # ── Build the plain-text report ──
    lines = [
        f"{'=' * 72}",
        f"  FULL INGESTION REPORT: {source.upper()}",
        f"{'=' * 72}",
        f"",
        f"Log ID:     {log.id}",
        f"Status:     {log.status}",
        f"Records:    {log.records_processed}"
        + (f" / {log.total_expected}" if log.total_expected else ""),
        f"Duration:   {duration}",
        f"Started:    {log.started_at.isoformat() if log.started_at else 'N/A'}",
        f"Completed:  {log.completed_at.isoformat() if log.completed_at else 'N/A'}",
        f"Errors:     {len(errors)}",
        f"Warnings:   {len(report.get('warnings', []))}",
        f"",
    ]

    # Summary
    if report.get("summary"):
        lines.append(f"Summary: {report['summary']}")
        lines.append("")

    # ── Guard Decisions ──
    guards = report.get("guard_decisions", {})
    if guards:
        lines.append(f"{'─' * 72}")
        lines.append("GUARD DECISIONS")
        lines.append(f"{'─' * 72}")
        lines.append(f"  Stale logs expired:    {guards.get('stale_logs_expired', 'N/A')}")
        lines.append(f"  Duplicate check:       {guards.get('duplicate_check_result', 'N/A')}")
        cp = guards.get("checkpoint_loaded")
        lines.append(f"  Checkpoint loaded:     {cp if cp else 'None'}")
        lines.append("")

    # ── Phases ──
    phases = report.get("phases", [])
    if phases:
        lines.append(f"{'─' * 72}")
        lines.append("PHASES")
        lines.append(f"{'─' * 72}")
        for i, phase in enumerate(phases, 1):
            lines.append(f"  [{i}] {phase.get('name', '?')}")
            lines.append(f"      Status:      {phase.get('status', '?')}")
            lines.append(f"      Duration:    {phase.get('duration_s', '?')}s")
            lines.append(f"      Records in:  {phase.get('records_in', '?')}")
            lines.append(f"      Records out: {phase.get('records_out', '?')}")
            if phase.get("detail"):
                lines.append(f"      Detail:      {phase['detail']}")
            lines.append("")

    # ── HTTP Stats ──
    http = report.get("http", {})
    if http:
        lines.append(f"{'─' * 72}")
        lines.append("HTTP STATS")
        lines.append(f"{'─' * 72}")
        lines.append(f"  Total requests:  {http.get('total_requests', 0)}")
        lines.append(f"  Successful:      {http.get('successful', 0)}")
        lines.append(f"  Retries:         {http.get('retries', 0)}")
        lines.append(f"  Failures:        {http.get('failures', 0)}")
        lines.append(f"  Latency (avg):   {http.get('latency_avg_ms', 'N/A')} ms")
        lines.append(f"  Latency (p50):   {http.get('latency_p50_ms', 'N/A')} ms")
        lines.append(f"  Latency (p95):   {http.get('latency_p95_ms', 'N/A')} ms")
        lines.append(f"  Latency (p99):   {http.get('latency_p99_ms', 'N/A')} ms")
        lines.append(f"  Latency (max):   {http.get('latency_max_ms', 'N/A')} ms")
        slowest = http.get("slowest_call")
        if slowest:
            lines.append(f"  Slowest call:    {slowest.get('latency_ms', '?')}ms "
                         f"({slowest.get('status_code', '?')}) {slowest.get('url', '?')}")

        by_status = http.get("by_status_code", {})
        if by_status:
            lines.append(f"  By status code:  {by_status}")

        by_domain = http.get("by_domain", {})
        if by_domain:
            lines.append(f"  By domain:")
            for domain, stats in by_domain.items():
                avg = round(stats["total_ms"] / stats["requests"], 1) if stats["requests"] else 0
                lines.append(f"    {domain}: {stats['requests']} reqs, avg {avg}ms")
        lines.append("")

    # ── Data Quality ──
    dq = report.get("data_quality", {})
    if dq:
        lines.append(f"{'─' * 72}")
        lines.append("DATA QUALITY")
        lines.append(f"{'─' * 72}")
        lines.append(f"  API records received:     {dq.get('total_api_records_received', 0)}")
        lines.append(f"  Skipped (non-dict):       {dq.get('skipped_non_dict', 0)}")
        lines.append(f"  Skipped (missing field):  {dq.get('skipped_missing_field', 0)}")
        lines.append(f"  Skipped (filtered):       {dq.get('skipped_filtered', 0)}")
        lines.append(f"  Skipped (duplicate):      {dq.get('skipped_duplicate', 0)}")
        lines.append(f"  Records upserted:         {dq.get('records_upserted', 0)}")
        lines.append(f"  Records inserted:         {dq.get('records_inserted', 0)}")
        lines.append("")

    # ── DB Operations ──
    db_ops = report.get("db_operations", {})
    if db_ops:
        lines.append(f"{'─' * 72}")
        lines.append("DB OPERATIONS")
        lines.append(f"{'─' * 72}")
        lines.append(f"  Batch upsert calls:  {db_ops.get('batch_upsert_calls', 0)}")
        lines.append(f"  Batch insert calls:  {db_ops.get('batch_insert_calls', 0)}")
        lines.append(f"  Total rows written:  {db_ops.get('total_rows_written', 0)}")
        lines.append("")

    # ── Caches ──
    caches = report.get("caches", {})
    if caches:
        lines.append(f"{'─' * 72}")
        lines.append("CACHES")
        lines.append(f"{'─' * 72}")
        for name, stats in caches.items():
            parts = [f"{k}={v}" for k, v in stats.items()]
            lines.append(f"  {name}: {', '.join(parts)}")
        lines.append("")

    # ── Warnings ──
    warnings = report.get("warnings", [])
    if warnings:
        lines.append(f"{'─' * 72}")
        lines.append(f"WARNINGS ({len(warnings)})")
        lines.append(f"{'─' * 72}")
        for w in warnings:
            lines.append(f"  [{w.get('elapsed_s', '?')}s] [{w.get('context', '?')}] {w.get('message', '')}")
        lines.append("")

    # ── Errors (summary) ──
    if errors:
        by_category: dict[str, int] = {}
        for err in errors:
            cat = err.get("category", "unknown")
            by_category[cat] = by_category.get(cat, 0) + 1

        lines.append(f"{'─' * 72}")
        lines.append(f"ERRORS ({len(errors)})")
        lines.append(f"{'─' * 72}")
        for cat, count in sorted(by_category.items(), key=lambda x: -x[1]):
            lines.append(f"  {cat}: {count}x")
        lines.append("")

        # Show first 20 error details
        for i, err in enumerate(errors[:20], 1):
            lines.append(f"  [{i}] [{err.get('category', '?')}] {err.get('context', '?')}: "
                         f"{err.get('error', '?')[:200]}")
            if err.get("record_id"):
                lines.append(f"      Record: {err['record_id']}")
        if len(errors) > 20:
            lines.append(f"  ... and {len(errors) - 20} more errors")
        lines.append("")

    # ── Timeline ──
    timeline = report.get("timeline", [])
    if timeline:
        lines.append(f"{'─' * 72}")
        lines.append(f"TIMELINE ({len(timeline)} events)")
        lines.append(f"{'─' * 72}")
        for entry in timeline:
            lines.append(
                f"  [{entry.get('elapsed_s', '?'):>8}s] "
                f"{entry.get('event', '?')}"
                + (f"  {entry.get('detail', '')}" if entry.get("detail") else "")
            )
        lines.append("")

    lines.append(f"{'=' * 72}")
    plain_text_report = "\n".join(lines)

    return {
        "source": source,
        "log_id": log.id,
        "status": log.status,
        "records_processed": log.records_processed,
        "total_expected": log.total_expected,
        "started_at": log.started_at.isoformat() if log.started_at else None,
        "completed_at": log.completed_at.isoformat() if log.completed_at else None,
        "duration": duration,
        "total_errors": len(errors),
        "total_warnings": len(report.get("warnings", [])),
        "report": report,
        "errors": errors[:200],
        "plain_text_report": plain_text_report,
    }


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
    total = total_result.scalar() or 0

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
                "total_expected": log.total_expected,
                "errors": log.errors,
                "started_at": log.started_at.isoformat() if log.started_at else None,
                "completed_at": log.completed_at.isoformat() if log.completed_at else None,
                "data_source_version": log.data_source_version,
                "data_downloaded_at": log.data_downloaded_at.isoformat() if log.data_downloaded_at else None,
                "records_filtered": log.records_filtered,
                "duration_seconds": log.duration_seconds,
            }
            for log in logs
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


# ------------------------------------------------------------------
# Table deletion
# ------------------------------------------------------------------

# Map of deletable table names to their SQLAlchemy models.
# Junction/child tables are listed before parent tables so that
# CASCADE deletes work correctly even without ON DELETE CASCADE.
_DELETABLE_TABLES: dict[str, list[str]] = {
    # Ingestion data tables — cascade lists name child tables to delete
    # before the parent.  DB-level ON DELETE CASCADE handles indirect
    # children, but listing them here gives accurate per-table counts.
    "drugs": [
        "gnn_predictions", "combination_hypotheses",
        "literature_drugs", "trial_drugs", "drug_targets", "bioassays",
        "drugs",
    ],
    "targets": [
        "target_disease_associations", "literature_targets",
        "pathway_targets", "drug_targets", "protein_interactions",
        "targets",
    ],
    "drug_targets": ["drug_targets"],
    "cancer_types": [
        "gnn_predictions", "gene_expression", "combination_hypotheses",
        "literature_cancers", "mutations", "molecular_profiles",
        "cancer_types",
    ],
    "molecular_profiles": ["molecular_profiles"],
    "mutations": ["mutations"],
    "pathways": ["pathway_targets", "pathways"],
    "pathway_targets": ["pathway_targets"],
    "protein_interactions": ["protein_interactions"],
    "literature": ["literature_drugs", "literature_targets", "literature_cancers", "literature"],
    "clinical_trials": ["trial_drugs", "clinical_trials"],
    "bioassays": ["bioassays"],
    "gene_dependencies": ["gene_dependencies"],
    "gene_expression": ["gene_expression"],
    "target_disease_associations": ["target_disease_associations"],
    "hypotheses": [
        "score_history", "literature_alerts", "monitoring_config",
        "tallula_discoveries", "hypothesis_evidence", "hypothesis_analyses",
        "hypotheses",
    ],
    "hypothesis_analyses": ["hypothesis_analyses"],
    "combination_hypotheses": ["combination_hypotheses"],
    "gnn_predictions": ["gnn_predictions"],
    "gnn_training_runs": ["gnn_predictions", "gnn_training_runs"],
    "score_history": ["score_history"],
    "literature_alerts": ["literature_alerts"],
    "monitoring_config": ["monitoring_config"],
    "ingestion_logs": ["ingestion_logs"],
}

# Map of table name used in the API → SQLAlchemy model.
# Lazy-loaded to avoid circular imports at module level.
def _get_table_model(table_name: str):
    """Return the SQLAlchemy model class for a table name."""
    from app.models.drug import Drug, DrugTarget, LiteratureDrug, TrialDrug
    from app.models.target import Target, ProteinInteraction
    from app.models.cancer_type import CancerType, CancerMolecularProfile
    from app.models.mutation import Mutation
    from app.models.pathway import Pathway, PathwayTarget
    from app.models.literature import Literature, LiteratureTarget, LiteratureCancer
    from app.models.clinical_trial import ClinicalTrial
    from app.models.evidence import Bioassay, GeneExpression
    from app.models.hypothesis import Hypothesis, HypothesisEvidence
    from app.models.llm_analysis import HypothesisAnalysis
    from app.models.tallula import TallulaDiscovery
    from app.models.gene_dependency import GeneDependency, CombinationHypothesis
    from app.models.gnn_prediction import GNNTrainingRun, GNNPrediction
    from app.models.literature_alert import ScoreHistory, LiteratureAlert, MonitoringConfig
    from app.models.target_disease import TargetDiseaseAssociation

    mapping = {
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
        "literature_drugs": LiteratureDrug,
        "literature_targets": LiteratureTarget,
        "literature_cancers": LiteratureCancer,
        "clinical_trials": ClinicalTrial,
        "trial_drugs": TrialDrug,
        "bioassays": Bioassay,
        "gene_expression": GeneExpression,
        "gene_dependencies": GeneDependency,
        "target_disease_associations": TargetDiseaseAssociation,
        "hypotheses": Hypothesis,
        "hypothesis_evidence": HypothesisEvidence,
        "hypothesis_analyses": HypothesisAnalysis,
        "tallula_discoveries": TallulaDiscovery,
        "combination_hypotheses": CombinationHypothesis,
        "gnn_predictions": GNNPrediction,
        "gnn_training_runs": GNNTrainingRun,
        "score_history": ScoreHistory,
        "literature_alerts": LiteratureAlert,
        "monitoring_config": MonitoringConfig,
        "ingestion_logs": IngestionLog,
    }
    return mapping.get(table_name)


@router.delete("/table/{table_name}")
async def delete_table_data(
    table_name: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete all rows from a specific data table.

    Also removes dependent junction rows (e.g. deleting 'drugs' also
    clears drug_targets, literature_drugs, trial_drugs, and bioassays).

    Valid table names: drugs, targets, drug_targets, cancer_types,
    molecular_profiles, mutations, pathways, pathway_targets,
    protein_interactions, literature, clinical_trials, bioassays,
    gene_dependencies, combination_hypotheses, ingestion_logs
    """
    if table_name not in _DELETABLE_TABLES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid table '{table_name}'. "
                f"Must be one of: {', '.join(sorted(_DELETABLE_TABLES))}"
            ),
        )

    tables_to_clear = _DELETABLE_TABLES[table_name]
    deleted_counts: dict[str, int] = {}

    for tbl in tables_to_clear:
        model = _get_table_model(tbl)
        if model is None:
            continue
        result = await db.execute(delete(model))
        deleted_counts[tbl] = result.rowcount

    await db.commit()

    total_deleted = sum(deleted_counts.values())
    logger.info(
        "Deleted table data for '%s': %s (total: %d rows)",
        table_name, deleted_counts, total_deleted,
    )

    return {
        "table": table_name,
        "deleted": deleted_counts,
        "total_deleted": total_deleted,
    }


# ------------------------------------------------------------------
# Task cancellation
# ------------------------------------------------------------------

@router.post("/cancel/{task_id}")
async def cancel_task(task_id: str):
    """Cancel a running or queued ingestion task.

    Sends a revoke signal to the Celery worker. If the task is already
    running, ``terminate=True`` sends SIGTERM to the worker process
    handling it.  Queued tasks are simply removed from the queue.

    Returns the task state after the revoke signal is sent.
    """
    result = celery_app.AsyncResult(task_id)
    task_state = result.state

    if task_state in ("SUCCESS", "FAILURE"):
        raise HTTPException(
            status_code=409,
            detail=f"Task {task_id} has already finished (state: {task_state})",
        )

    # Revoke with terminate=True so running tasks get a SIGTERM
    celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")

    logger.info("Revoked task %s (previous state: %s)", task_id, task_state)

    return {
        "task_id": task_id,
        "previous_state": task_state,
        "action": "revoked",
    }


# ------------------------------------------------------------------
# Kill switch — full data reset
# ------------------------------------------------------------------

# Ordered so that child/junction tables are truncated before parents.
_ALL_TABLES = [
    "software_versions",
    "tallula_discoveries",
    "tallula_runs",
    "validation_results",
    "literature_alerts",
    "monitoring_config",
    "score_history",
    "hypothesis_evidence",
    "hypothesis_analyses",
    "llm_usage_logs",
    "combination_hypotheses",
    "gene_dependencies",
    "expression_score_cache",
    "gene_expression",
    "gnn_predictions",
    "gnn_training_runs",
    "bioassays",
    "trial_drugs",
    "clinical_trials",
    "literature_drugs",
    "literature_targets",
    "literature_cancers",
    "literature",
    "drug_targets",
    "pathway_targets",
    "protein_interactions",
    "target_disease_associations",
    "pathways",
    "mutations",
    "cancer_molecular_profiles",
    "hypotheses",
    "cancer_types",
    "targets",
    "drugs",
    "scoring_weights",
    "ingestion_logs",
    "pipeline_runs",
]


@router.post("/reset-all")
async def reset_all_data(db: AsyncSession = Depends(get_db)):
    """Kill switch: cancel every running task, then wipe PostgreSQL,
    Redis, and Neo4j so the app is back to a clean-slate state.

    Returns a summary of what was cleared in each subsystem.
    """
    report: dict[str, object] = {}

    # 1. Cancel all active/reserved Celery tasks and purge queues ----
    try:
        inspect = celery_app.control.inspect()
        active = inspect.active() or {}
        reserved = inspect.reserved() or {}
        revoked_ids: list[str] = []
        for worker_tasks in [*active.values(), *reserved.values()]:
            for task_info in worker_tasks:
                tid = task_info.get("id")
                if tid:
                    celery_app.control.revoke(tid, terminate=True, signal="SIGTERM")
                    revoked_ids.append(tid)
        celery_app.control.purge()
        report["celery"] = {
            "tasks_revoked": len(revoked_ids),
            "queues_purged": True,
        }
    except Exception as exc:
        logger.warning("Celery reset partial failure: %s", exc)
        report["celery"] = {"error": str(exc)}

    # 2. Truncate all PostgreSQL tables --------------------------------
    # Safety: _ALL_TABLES is a hardcoded constant defined in this module
    # (not derived from user input), so the f-string in the TRUNCATE
    # statement is safe from SQL injection.  The validation below is a
    # defence-in-depth check to ensure table names contain only
    # alphanumeric characters and underscores.
    import re
    for t in _ALL_TABLES:
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", t):
            raise ValueError(f"Invalid table name in _ALL_TABLES: {t!r}")

    # First, discover which tables actually exist in the database so we
    # don't fail the entire operation because of a missing table.
    try:
        existing_result = await db.execute(
            text(
                "SELECT tablename FROM pg_catalog.pg_tables "
                "WHERE schemaname = 'public'"
            )
        )
        existing_tables = {row[0] for row in existing_result.fetchall()}
    except Exception as exc:
        logger.error("Failed to query existing tables: %s", exc)
        existing_tables = set(_ALL_TABLES)  # fall back to attempting all

    tables_to_truncate = [t for t in _ALL_TABLES if t in existing_tables]
    skipped_tables = [t for t in _ALL_TABLES if t not in existing_tables]

    if skipped_tables:
        logger.warning(
            "Tables not found in database (skipping): %s", skipped_tables
        )

    truncated: list[str] = []
    failed: dict[str, str] = {}

    if tables_to_truncate:
        try:
            table_list = ", ".join(tables_to_truncate)
            await db.execute(
                text(f"TRUNCATE TABLE {table_list} CASCADE")
            )
            await db.commit()
            truncated = tables_to_truncate
        except Exception as exc:
            await db.rollback()
            logger.warning(
                "Bulk TRUNCATE failed (%s), falling back to per-table truncation",
                exc,
            )
            # Fall back: truncate each table individually so one bad table
            # doesn't prevent the rest from being cleared.
            for t in tables_to_truncate:
                try:
                    await db.execute(
                        text(f"TRUNCATE TABLE {t} CASCADE")
                    )
                    await db.commit()
                    truncated.append(t)
                except Exception as table_exc:
                    await db.rollback()
                    failed[t] = str(table_exc)
                    logger.error("TRUNCATE %s failed: %s", t, table_exc)

    report["postgres"] = {
        "tables_truncated": truncated,
        "count": len(truncated),
        "tables_skipped_not_found": skipped_tables,
        "tables_failed": failed,
    }

    # 3. Flush Redis (all 3 logical databases) -------------------------
    from urllib.parse import urlparse, urlunparse

    redis_dbs_flushed: list[int] = []
    for db_index in (0, 1, 2):
        try:
            parsed = urlparse(settings.redis_url)
            redis_url = urlunparse(parsed._replace(path=f"/{db_index}"))
            r = aioredis.from_url(redis_url)
            await r.flushdb()
            await r.aclose()
            redis_dbs_flushed.append(db_index)
        except Exception as exc:
            logger.warning("Redis flush db/%d failed: %s", db_index, exc)
    report["redis"] = {"databases_flushed": redis_dbs_flushed}

    # 4. Clear Neo4j knowledge graph -----------------------------------
    try:
        driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
        async with driver.session() as neo_session:
            result = await neo_session.run(
                "MATCH (n) DETACH DELETE n RETURN count(n) AS deleted"
            )
            record = await result.single()
            neo4j_deleted = record["deleted"] if record else 0
        await driver.close()
        report["neo4j"] = {"nodes_deleted": neo4j_deleted}
    except Exception as exc:
        logger.warning("Neo4j clear failed: %s", exc)
        report["neo4j"] = {"error": str(exc)}

    # Determine overall status based on whether critical steps succeeded.
    pg_info = report.get("postgres", {})
    pg_ok = pg_info.get("count", 0) > 0 and not pg_info.get("tables_failed")
    has_errors = (
        "error" in report.get("celery", {})
        or not pg_ok
        or "error" in report.get("redis", {})
        or "error" in report.get("neo4j", {})
    )

    if not pg_ok:
        status = "reset_failed"
        logger.error("Kill switch: PostgreSQL wipe FAILED — %s", report)
    elif has_errors:
        status = "reset_partial"
        logger.warning("Kill switch: partial success — %s", report)
    else:
        status = "reset_complete"
        logger.info("Kill switch activated — full reset complete: %s", report)

    return {"status": status, "details": report}
