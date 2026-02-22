"""Publication readiness API — tools for generating paper-quality outputs.

Endpoints:
  GET  /readiness          Publication readiness checklist with pass/fail
  GET  /methods            Auto-generated Methods section with live numbers
  GET  /completeness       Data completeness metrics per source
  GET  /fairness           Stratified performance metrics by cancer type / drug class
  GET  /software-versions  Software version registry snapshots
  POST /software-versions  Capture current software versions
"""

import platform
import sys
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter()


# ------------------------------------------------------------------
# Readiness checklist
# ------------------------------------------------------------------


@router.get("/readiness")
async def publication_readiness(db: AsyncSession = Depends(get_db)):
    """Run the publication readiness checklist and return pass/fail per item.

    Each check reports:
      - key: machine-readable identifier
      - label: human description
      - passed: bool
      - detail: explanation of current state
    """
    from app.models.hypothesis import Hypothesis, ValidationResult
    from app.models.ingestion_log import IngestionLog
    from app.models.pipeline_run import PipelineRun
    from app.models.software_version import SoftwareVersion
    from app.models.llm_analysis import HypothesisAnalysis
    from app.models.scoring_config import ScoringWeight

    checks: list[dict] = []

    # 1. Hypotheses exist
    hyp_count = (await db.execute(select(func.count(Hypothesis.id)))).scalar() or 0
    checks.append({
        "key": "hypotheses_exist",
        "label": "Hypotheses generated",
        "passed": hyp_count > 0,
        "detail": f"{hyp_count:,} hypotheses in database",
    })

    # 2. Batch tracking
    batched = (await db.execute(
        select(func.count(Hypothesis.id)).where(Hypothesis.batch_id.isnot(None))
    )).scalar() or 0
    checks.append({
        "key": "batch_tracking",
        "label": "Batch IDs assigned to hypotheses",
        "passed": batched > 0 and batched == hyp_count,
        "detail": f"{batched:,} of {hyp_count:,} hypotheses have batch_id",
    })

    # 3. Data source versioning
    versioned_logs = (await db.execute(
        select(func.count(IngestionLog.id)).where(
            IngestionLog.data_source_version.isnot(None)
        )
    )).scalar() or 0
    total_logs = (await db.execute(select(func.count(IngestionLog.id)))).scalar() or 0
    checks.append({
        "key": "data_versioning",
        "label": "Ingestion logs have data source versions",
        "passed": versioned_logs > 0 and versioned_logs >= total_logs * 0.8,
        "detail": f"{versioned_logs} of {total_logs} logs have source version metadata",
    })

    # 4. Software version snapshot
    sw_count = (await db.execute(select(func.count(SoftwareVersion.id)))).scalar() or 0
    checks.append({
        "key": "software_versions",
        "label": "Software version snapshot captured",
        "passed": sw_count > 0,
        "detail": f"{sw_count} software version snapshot(s) recorded",
    })

    # 5. Validation run
    val_count = (await db.execute(
        select(func.count(ValidationResult.id))
    )).scalar() or 0
    checks.append({
        "key": "validation_run",
        "label": "Validation framework executed",
        "passed": val_count > 0,
        "detail": f"{val_count} validation result(s) stored",
    })

    # 6. LLM analyses present
    llm_count = (await db.execute(
        select(func.count(HypothesisAnalysis.id))
    )).scalar() or 0
    checks.append({
        "key": "llm_analyses",
        "label": "LLM narrative analyses generated",
        "passed": llm_count > 0,
        "detail": f"{llm_count} LLM analyses available",
    })

    # 7. Statistical significance
    sig_count = (await db.execute(
        select(func.count(Hypothesis.id)).where(Hypothesis.p_value.isnot(None))
    )).scalar() or 0
    checks.append({
        "key": "p_values",
        "label": "P-values computed for hypotheses",
        "passed": sig_count > 0,
        "detail": f"{sig_count:,} of {hyp_count:,} hypotheses have p-values",
    })

    # 8. Confidence intervals
    ci_count = (await db.execute(
        select(func.count(Hypothesis.id)).where(
            Hypothesis.confidence_interval.isnot(None)
        )
    )).scalar() or 0
    checks.append({
        "key": "confidence_intervals",
        "label": "Confidence intervals computed",
        "passed": ci_count > 0,
        "detail": f"{ci_count:,} hypotheses have composite score CIs",
    })

    # 9. Per-dimension CIs
    dim_ci_count = (await db.execute(
        select(func.count(Hypothesis.id)).where(
            Hypothesis.dimension_confidence_intervals.isnot(None)
        )
    )).scalar() or 0
    checks.append({
        "key": "dimension_cis",
        "label": "Per-dimension confidence intervals",
        "passed": dim_ci_count > 0,
        "detail": f"{dim_ci_count:,} hypotheses have per-dimension CIs",
    })

    # 10. Scoring weights configured
    weights_count = (await db.execute(
        select(func.count(ScoringWeight.id))
    )).scalar() or 0
    checks.append({
        "key": "scoring_weights",
        "label": "Scoring weight presets defined",
        "passed": weights_count > 0,
        "detail": f"{weights_count} scoring preset(s) configured",
    })

    # 11. Multiple cancer types covered
    cancer_count = (await db.execute(
        select(func.count(distinct(Hypothesis.cancer_type_id)))
    )).scalar() or 0
    checks.append({
        "key": "cancer_coverage",
        "label": "Multiple cancer types covered",
        "passed": cancer_count >= 3,
        "detail": f"{cancer_count} distinct cancer types with hypotheses",
    })

    # 12. Pipeline run completed
    completed_runs = (await db.execute(
        select(func.count(PipelineRun.id)).where(PipelineRun.status == "completed")
    )).scalar() or 0
    checks.append({
        "key": "pipeline_completed",
        "label": "At least one full pipeline run completed",
        "passed": completed_runs > 0,
        "detail": f"{completed_runs} completed pipeline run(s)",
    })

    passed = sum(1 for c in checks if c["passed"])
    total = len(checks)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "passed": passed,
            "total": total,
            "percentage": round(passed / total * 100) if total > 0 else 0,
            "ready": passed == total,
        },
        "checks": checks,
    }


# ------------------------------------------------------------------
# Auto-generated Methods section
# ------------------------------------------------------------------


@router.get("/methods")
async def generate_methods_section(db: AsyncSession = Depends(get_db)):
    """Auto-generate a Methods section with live numbers from the database.

    Returns structured data that can be used to populate a manuscript
    Methods section, with all numbers pulled directly from the data.
    """
    from app.models.hypothesis import Hypothesis
    from app.models.drug import Drug
    from app.models.cancer_type import CancerType
    from app.models.target import Target
    from app.models.pathway import Pathway
    from app.models.literature import Literature
    from app.models.clinical_trial import ClinicalTrial
    from app.models.gene_dependency import GeneDependency, CombinationHypothesis
    from app.models.mutation import Mutation
    from app.models.ingestion_log import IngestionLog
    from app.models.scoring_config import ScoringWeight
    from app.models.evidence import Bioassay
    from app.models.target import ProteinInteraction

    # Data source counts
    drug_count = (await db.execute(select(func.count(Drug.id)))).scalar() or 0
    target_count = (await db.execute(select(func.count(Target.id)))).scalar() or 0
    cancer_count = (await db.execute(select(func.count(CancerType.id)))).scalar() or 0
    pathway_count = (await db.execute(select(func.count(Pathway.id)))).scalar() or 0
    literature_count = (await db.execute(select(func.count(Literature.id)))).scalar() or 0
    trial_count = (await db.execute(select(func.count(ClinicalTrial.id)))).scalar() or 0
    mutation_count = (await db.execute(select(func.count(Mutation.id)))).scalar() or 0
    dependency_count = (await db.execute(select(func.count(GeneDependency.id)))).scalar() or 0
    interaction_count = (await db.execute(select(func.count(ProteinInteraction.id)))).scalar() or 0
    bioassay_count = (await db.execute(select(func.count(Bioassay.id)))).scalar() or 0

    # Hypothesis statistics
    hyp_count = (await db.execute(select(func.count(Hypothesis.id)))).scalar() or 0
    distinct_cancers = (await db.execute(
        select(func.count(distinct(Hypothesis.cancer_type_id)))
    )).scalar() or 0
    distinct_drugs = (await db.execute(
        select(func.count(distinct(Hypothesis.drug_id)))
    )).scalar() or 0

    # Scoring dimensions and weights
    active_weight = (await db.execute(
        select(ScoringWeight).where(ScoringWeight.is_default == 1).limit(1)
    )).scalar_one_or_none()

    weights_info = None
    if active_weight:
        weights_info = {
            "preset_name": active_weight.name,
            "weights": active_weight.weights,
            "created_at": active_weight.created_at.isoformat() if active_weight.created_at else None,
        }

    # Latest ingestion log per source (for version info)
    latest_logs = (
        select(
            IngestionLog.source,
            func.max(IngestionLog.id).label("max_id"),
        )
        .where(IngestionLog.status == "completed")
        .group_by(IngestionLog.source)
        .subquery()
    )
    log_result = await db.execute(
        select(IngestionLog).join(
            latest_logs,
            (IngestionLog.source == latest_logs.c.source)
            & (IngestionLog.id == latest_logs.c.max_id),
        )
    )
    logs = log_result.scalars().all()

    data_sources = []
    for log in logs:
        data_sources.append({
            "source": log.source,
            "version": log.data_source_version,
            "downloaded_at": log.data_downloaded_at.isoformat() if log.data_downloaded_at else None,
            "records_processed": log.records_processed,
            "records_filtered": log.records_filtered,
            "api_url": log.api_url,
        })

    # Combination hypotheses
    combo_count = (await db.execute(
        select(func.count(CombinationHypothesis.id))
    )).scalar() or 0

    # Score distribution
    score_stats = (await db.execute(
        select(
            func.avg(Hypothesis.composite_score),
            func.stddev(Hypothesis.composite_score),
            func.min(Hypothesis.composite_score),
            func.max(Hypothesis.composite_score),
            func.percentile_cont(0.25).within_group(Hypothesis.composite_score),
            func.percentile_cont(0.50).within_group(Hypothesis.composite_score),
            func.percentile_cont(0.75).within_group(Hypothesis.composite_score),
            func.percentile_cont(0.95).within_group(Hypothesis.composite_score),
        )
    )).first()

    strength_counts = dict(
        (await db.execute(
            select(Hypothesis.evidence_strength, func.count(Hypothesis.id))
            .group_by(Hypothesis.evidence_strength)
        )).all()
    )

    # Batch info
    batch_result = await db.execute(
        select(func.count(distinct(Hypothesis.batch_id))).where(
            Hypothesis.batch_id.isnot(None)
        )
    )
    batch_count = batch_result.scalar() or 0

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_sources": {
            "summary": {
                "drugs": drug_count,
                "targets": target_count,
                "cancer_types": cancer_count,
                "pathways": pathway_count,
                "literature_papers": literature_count,
                "clinical_trials": trial_count,
                "mutations": mutation_count,
                "gene_dependencies": dependency_count,
                "protein_interactions": interaction_count,
                "bioassays": bioassay_count,
            },
            "per_source": data_sources,
        },
        "hypothesis_generation": {
            "total_hypotheses": hyp_count,
            "distinct_cancer_types": distinct_cancers,
            "distinct_drugs": distinct_drugs,
            "combination_hypotheses": combo_count,
            "generation_batches": batch_count,
        },
        "scoring": {
            "dimensions": [
                "pathway_overlap", "expression_correlation", "literature_support",
                "clinical_evidence", "safety", "novelty", "causal_dependency",
                "gnn_link", "mutation_context", "polypharmacology",
                "pharmacological_response",
            ],
            "active_weights": weights_info,
            "score_distribution": {
                "mean": round(score_stats[0], 2) if score_stats and score_stats[0] else None,
                "std": round(score_stats[1], 2) if score_stats and score_stats[1] else None,
                "min": round(score_stats[2], 2) if score_stats and score_stats[2] else None,
                "max": round(score_stats[3], 2) if score_stats and score_stats[3] else None,
                "q25": round(score_stats[4], 2) if score_stats and score_stats[4] else None,
                "median": round(score_stats[5], 2) if score_stats and score_stats[5] else None,
                "q75": round(score_stats[6], 2) if score_stats and score_stats[6] else None,
                "q95": round(score_stats[7], 2) if score_stats and score_stats[7] else None,
            },
            "evidence_strength_distribution": strength_counts,
        },
    }


# ------------------------------------------------------------------
# Data completeness
# ------------------------------------------------------------------


@router.get("/completeness")
async def data_completeness(db: AsyncSession = Depends(get_db)):
    """Report data completeness — how many records per source, how many
    have full metadata vs. partial, and source recency.

    Critical for the Methods section to document coverage and limitations.
    """
    from app.models.drug import Drug, DrugTarget
    from app.models.target import Target
    from app.models.cancer_type import CancerType
    from app.models.hypothesis import Hypothesis
    from app.models.ingestion_log import IngestionLog

    # Drug completeness: how many drugs have mechanism, formula, status?
    total_drugs = (await db.execute(select(func.count(Drug.id)))).scalar() or 0
    drugs_with_mechanism = (await db.execute(
        select(func.count(Drug.id)).where(Drug.mechanism_of_action.isnot(None))
    )).scalar() or 0
    drugs_with_targets = (await db.execute(
        select(func.count(distinct(DrugTarget.drug_id)))
    )).scalar() or 0

    # Target completeness
    total_targets = (await db.execute(select(func.count(Target.id)))).scalar() or 0

    # Cancer type completeness
    total_cancers = (await db.execute(select(func.count(CancerType.id)))).scalar() or 0

    # Hypothesis dimension completeness: how many have non-null scores?
    total_hyps = (await db.execute(select(func.count(Hypothesis.id)))).scalar() or 0
    dimensions = [
        ("pathway_overlap", Hypothesis.pathway_overlap_score),
        ("expression_correlation", Hypothesis.expression_correlation_score),
        ("literature_support", Hypothesis.literature_support_score),
        ("clinical_evidence", Hypothesis.clinical_evidence_score),
        ("safety", Hypothesis.safety_score),
        ("novelty", Hypothesis.novelty_score),
        ("causal_dependency", Hypothesis.causal_dependency_score),
        ("mutation_context", Hypothesis.mutation_context_score),
        ("polypharmacology", Hypothesis.polypharmacology_score),
        ("pharmacological_response", Hypothesis.pharmacological_response_score),
    ]

    dim_completeness = {}
    for name, col in dimensions:
        non_null = (await db.execute(
            select(func.count(Hypothesis.id)).where(col.isnot(None), col > 0)
        )).scalar() or 0
        dim_completeness[name] = {
            "non_null_count": non_null,
            "total": total_hyps,
            "percentage": round(non_null / total_hyps * 100, 1) if total_hyps else 0,
        }

    # Ingestion freshness: most recent completed log per source
    freshness = {}
    log_result = await db.execute(
        select(
            IngestionLog.source,
            func.max(IngestionLog.completed_at).label("last_completed"),
            func.sum(IngestionLog.records_processed).label("total_records"),
        )
        .where(IngestionLog.status == "completed")
        .group_by(IngestionLog.source)
    )
    for row in log_result.all():
        freshness[row[0]] = {
            "last_ingested": row[1].isoformat() if row[1] else None,
            "total_records_ever": row[2] or 0,
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "entities": {
            "drugs": {
                "total": total_drugs,
                "with_mechanism": drugs_with_mechanism,
                "with_targets": drugs_with_targets,
                "mechanism_pct": round(drugs_with_mechanism / total_drugs * 100, 1) if total_drugs else 0,
            },
            "targets": {"total": total_targets},
            "cancer_types": {"total": total_cancers},
        },
        "dimension_completeness": dim_completeness,
        "ingestion_freshness": freshness,
    }


# ------------------------------------------------------------------
# Fairness / stratified analysis
# ------------------------------------------------------------------


@router.get("/fairness")
async def fairness_analysis(db: AsyncSession = Depends(get_db)):
    """Stratified performance metrics by cancer type and drug approval status.

    Reports mean scores, count, and evidence strength distribution per stratum
    so reviewers can assess whether the model is biased toward certain
    cancer types or drug classes.
    """
    from app.models.hypothesis import Hypothesis
    from app.models.cancer_type import CancerType
    from app.models.drug import Drug

    # By cancer type
    cancer_result = await db.execute(
        select(
            CancerType.name,
            CancerType.tcga_code,
            func.count(Hypothesis.id).label("n"),
            func.avg(Hypothesis.composite_score).label("mean_score"),
            func.stddev(Hypothesis.composite_score).label("std_score"),
            func.min(Hypothesis.composite_score).label("min_score"),
            func.max(Hypothesis.composite_score).label("max_score"),
        )
        .join(CancerType, Hypothesis.cancer_type_id == CancerType.id)
        .group_by(CancerType.id, CancerType.name, CancerType.tcga_code)
        .order_by(func.count(Hypothesis.id).desc())
    )
    by_cancer = []
    for row in cancer_result.all():
        by_cancer.append({
            "cancer_type": row[0],
            "tcga_code": row[1],
            "hypothesis_count": row[2],
            "mean_score": round(row[3], 2) if row[3] else 0,
            "std_score": round(row[4], 2) if row[4] else 0,
            "min_score": round(row[5], 2) if row[5] else 0,
            "max_score": round(row[6], 2) if row[6] else 0,
        })

    # By drug approval status
    drug_result = await db.execute(
        select(
            Drug.status,
            func.count(Hypothesis.id).label("n"),
            func.avg(Hypothesis.composite_score).label("mean_score"),
            func.stddev(Hypothesis.composite_score).label("std_score"),
        )
        .join(Drug, Hypothesis.drug_id == Drug.id)
        .group_by(Drug.status)
        .order_by(func.count(Hypothesis.id).desc())
    )
    by_drug_status = []
    for row in drug_result.all():
        by_drug_status.append({
            "drug_status": row[0] or "unknown",
            "hypothesis_count": row[1],
            "mean_score": round(row[2], 2) if row[2] else 0,
            "std_score": round(row[3], 2) if row[3] else 0,
        })

    # Evidence strength distribution by cancer type
    strength_by_cancer = {}
    strength_result = await db.execute(
        select(
            CancerType.tcga_code,
            Hypothesis.evidence_strength,
            func.count(Hypothesis.id),
        )
        .join(CancerType, Hypothesis.cancer_type_id == CancerType.id)
        .group_by(CancerType.tcga_code, Hypothesis.evidence_strength)
    )
    for row in strength_result.all():
        code = row[0]
        if code not in strength_by_cancer:
            strength_by_cancer[code] = {}
        strength_by_cancer[code][row[1]] = row[2]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "by_cancer_type": by_cancer,
        "by_drug_status": by_drug_status,
        "strength_by_cancer": strength_by_cancer,
    }


# ------------------------------------------------------------------
# Software version registry
# ------------------------------------------------------------------


@router.get("/software-versions")
async def list_software_versions(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """List captured software version snapshots, newest first."""
    from app.models.software_version import SoftwareVersion

    result = await db.execute(
        select(SoftwareVersion)
        .order_by(SoftwareVersion.created_at.desc())
        .limit(limit)
    )
    versions = result.scalars().all()

    return {
        "snapshots": [
            {
                "id": v.id,
                "pipeline_run_id": v.pipeline_run_id,
                "snapshot_label": v.snapshot_label,
                "python_version": v.python_version,
                "packages": v.packages,
                "platform_info": v.platform_info,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in versions
        ],
        "count": len(versions),
    }


@router.post("/software-versions")
async def capture_software_versions(
    label: str = Query("manual", description="Label for this snapshot"),
    pipeline_run_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Capture current software environment and store in the registry."""
    import importlib.metadata
    from app.models.software_version import SoftwareVersion

    # Key packages to track
    key_packages = [
        "fastapi", "sqlalchemy", "alembic", "celery", "redis",
        "numpy", "scipy", "scikit-learn", "pandas", "statsmodels",
        "sentence-transformers", "torch", "torch-geometric",
        "anthropic", "httpx", "aiohttp", "biopython",
        "pydantic", "uvicorn", "asyncpg", "neo4j",
    ]

    packages = {}
    for pkg in key_packages:
        try:
            packages[pkg] = importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            packages[pkg] = None

    platform_info = {
        "os": platform.system(),
        "os_version": platform.version(),
        "architecture": platform.machine(),
        "processor": platform.processor() or "unknown",
        "python_implementation": platform.python_implementation(),
    }

    snapshot = SoftwareVersion(
        pipeline_run_id=pipeline_run_id,
        snapshot_label=label,
        python_version=sys.version,
        packages=packages,
        platform_info=platform_info,
    )
    db.add(snapshot)
    await db.flush()

    return {
        "id": snapshot.id,
        "snapshot_label": snapshot.snapshot_label,
        "python_version": snapshot.python_version,
        "packages": packages,
        "platform_info": platform_info,
        "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
    }
