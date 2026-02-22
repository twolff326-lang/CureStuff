"""Celery task for running the full drug repurposing pipeline.

Orchestrates all phases in order:
  1. Ingest Drugs (DrugBank -> PubChem + ChEMBL)
  2. Ingest Cancer Data (cBioPortal -> TCGA + COSMIC)
  3. Ingest Pathways (UniProt -> KEGG/Reactome -> STRING -> OpenTargets)
  4. Ingest Literature (PubMed + ClinicalTrials + Embeddings)  [optional]
  5. Sync Knowledge Graph (PostgreSQL -> Neo4j)
  6. Expression Analysis (DE -> Drug Scoring -> Pathway Activity)
  7. Generate Hypotheses (all cancer types)
  8. LLM Narratives (for top hypotheses)                        [optional]

Each phase updates the pipeline_runs table so the frontend can poll progress.
"""

import logging
import traceback
from datetime import datetime, timezone

from app.tasks.celery_app import celery_app
from app.tasks.utils import run_async

logger = logging.getLogger(__name__)

# Phase definitions: key, display label, celery task name, default enabled
PIPELINE_PHASES = [
    ("ingest_drugs", "Ingest Drugs", "app.tasks.ingest.ingest_all_drugs", True),
    ("ingest_cancer", "Ingest Cancer Data", "app.tasks.ingest.ingest_all_cancer_data", True),
    ("ingest_pathways", "Ingest Pathways", "app.tasks.ingest.ingest_all_pathways", True),
    ("ingest_literature", "Ingest Literature", "app.tasks.ingest.ingest_all_literature", False),
    ("sync_knowledge_graph", "Sync Knowledge Graph", "app.tasks.ingest.sync_knowledge_graph", True),
    ("expression_analysis", "Expression Analysis", "app.tasks.analyze.run_full_expression_analysis", True),
    ("generate_hypotheses", "Generate Hypotheses", "app.tasks.generate.generate_all_hypotheses", True),
    ("llm_narratives", "LLM Narratives", "app.tasks.analyze.generate_narratives_batch", False),
]


def _update_pipeline_run(run_id, **fields):
    """Update a pipeline_runs row synchronously (used from Celery worker)."""
    from sqlalchemy import update
    from app.tasks.utils import task_session
    from app.models.pipeline_run import PipelineRun

    async def _do():
        async with task_session() as session:
            await session.execute(
                update(PipelineRun).where(PipelineRun.id == run_id).values(**fields)
            )
            await session.commit()

    run_async(_do())


def _get_pipeline_phases(run_id):
    """Read the current phases JSON from a pipeline run."""
    from sqlalchemy import select
    from app.tasks.utils import task_session
    from app.models.pipeline_run import PipelineRun

    async def _do():
        async with task_session() as session:
            result = await session.execute(
                select(PipelineRun.phases).where(PipelineRun.id == run_id)
            )
            return result.scalar_one_or_none()

    return run_async(_do())


@celery_app.task(name="app.tasks.pipeline.run_full_pipeline")
def run_full_pipeline(run_id, enabled_phases=None, config=None):
    """Execute the full drug repurposing pipeline.

    Args:
        run_id: PipelineRun row ID (created by the API before dispatching)
        enabled_phases: List of phase keys to run. If None, uses defaults.
        config: Optional dict with phase-specific config (min_score, etc.)
    """
    config = config or {}
    if enabled_phases is None:
        enabled_phases = [key for key, _, _, default in PIPELINE_PHASES if default]

    phases_to_run = [
        (key, label, task_name)
        for key, label, task_name, _ in PIPELINE_PHASES
        if key in enabled_phases
    ]

    logger.info(
        "Starting full pipeline (run_id=%d, phases=%s)",
        run_id,
        [p[0] for p in phases_to_run],
    )

    for phase_idx, (phase_key, phase_label, task_name) in enumerate(phases_to_run):
        # Mark this phase as running
        phases = _get_pipeline_phases(run_id) or []
        for p in phases:
            if p["key"] == phase_key:
                p["status"] = "running"
                p["started_at"] = datetime.now(timezone.utc).isoformat()
                break

        _update_pipeline_run(run_id, current_phase=phase_key, phases=phases)

        logger.info(
            "Pipeline run %d: starting phase '%s' (%d/%d)",
            run_id, phase_key, phase_idx + 1, len(phases_to_run),
        )

        try:
            # Build kwargs for the subtask
            kwargs = {}
            if phase_key == "generate_hypotheses" and config.get("min_score"):
                kwargs["min_score"] = config["min_score"]
            if phase_key == "llm_narratives":
                kwargs["min_score"] = config.get("llm_min_score", 0.0)
                kwargs["limit"] = config.get("llm_limit", 100)

            # Dispatch subtask and wait for completion.
            # disable_sync_subtasks=False is required because Celery 5.x
            # raises RuntimeError when calling result.get() inside a task
            # with the prefork pool.  Our concurrency is 2+ so deadlock
            # is avoided (orchestrator holds one slot, subtask uses another).
            result = celery_app.send_task(task_name, kwargs=kwargs)
            task_result = result.get(
                timeout=43200,  # 12h max per phase
                disable_sync_subtasks=False,
            )

            # Mark phase completed
            phases = _get_pipeline_phases(run_id) or []
            for p in phases:
                if p["key"] == phase_key:
                    p["status"] = "completed"
                    p["completed_at"] = datetime.now(timezone.utc).isoformat()
                    p["result"] = _safe_result(task_result)
                    break
            _update_pipeline_run(run_id, phases=phases)

            logger.info("Pipeline run %d: phase '%s' completed", run_id, phase_key)

        except Exception as exc:
            logger.error(
                "Pipeline run %d: phase '%s' failed: %s",
                run_id, phase_key, exc,
            )

            # Mark phase as failed
            phases = _get_pipeline_phases(run_id) or []
            for p in phases:
                if p["key"] == phase_key:
                    p["status"] = "failed"
                    p["completed_at"] = datetime.now(timezone.utc).isoformat()
                    p["error"] = str(exc)[:2000]
                    p["traceback"] = traceback.format_exc()[-2000:]
                    break

            # Mark remaining phases as skipped
            remaining_keys = {pk for pk, _, _ in phases_to_run[phase_idx + 1:]}
            for p in phases:
                if p["key"] in remaining_keys:
                    p["status"] = "skipped"

            _update_pipeline_run(
                run_id,
                status="failed",
                current_phase=phase_key,
                phases=phases,
                completed_at=datetime.now(timezone.utc),
            )
            return {
                "status": "failed",
                "failed_phase": phase_key,
                "error": str(exc)[:2000],
                "traceback": traceback.format_exc()[-2000:],
            }

    # All phases completed
    _update_pipeline_run(
        run_id,
        status="completed",
        current_phase=None,
        completed_at=datetime.now(timezone.utc),
    )

    logger.info("Pipeline run %d: all phases completed", run_id)
    return {"status": "completed", "phases_completed": len(phases_to_run)}


def _safe_result(result):
    """Extract a JSON-safe summary from a task result."""
    if not isinstance(result, dict):
        return {"raw": str(result)[:200]}
    safe = {}
    for key in ("records_processed", "errors_count", "status",
                "total_generated", "hypotheses_generated", "completed", "total"):
        if key in result:
            safe[key] = result[key]
    return safe
