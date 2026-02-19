"""Tallula Algorithm API routes.

Endpoints:
  - POST /run                  Execute the Tallula Algorithm against DB hypotheses
  - GET  /discoveries          List discoveries from the most recent run
  - GET  /discoveries/resonant Top resonant discoveries (the novel findings)
  - GET  /runs                 List past Tallula runs
  - GET  /runs/{run_id}        Full results for a specific run
  - GET  /runs/{run_id}/export Export a Tallula run as JSON or CSV
"""

import csv
import io
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.tallula import TallulaDiscovery, TallulaRun
from app.services.tallula import TallulaEngine

logger = logging.getLogger(__name__)
router = APIRouter()


# ------------------------------------------------------------------
# Request / response models
# ------------------------------------------------------------------

class TallulaRunRequest(BaseModel):
    n_lenses: int = Field(200, ge=10, le=2000, description="Number of stochastic lenses (K)")
    dropout_rate: float = Field(0.3, ge=0.0, le=0.8, description="Per-dimension dropout probability")
    dirichlet_alpha: float = Field(2.0, ge=0.1, le=20.0, description="Dirichlet concentration parameter")
    seed: int | None = Field(42, description="Random seed for reproducibility")
    cancer_type_id: int | None = Field(None, description="Optional: scope to a single cancer type")
    min_deterministic_score: float = Field(5.0, ge=0.0, description="Min baseline score to include")
    top_n: int | None = Field(None, ge=1, description="Return top N per discovery class")


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.post("/run", summary="Execute the Tallula Algorithm")
async def run_tallula(
    request: TallulaRunRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Run the Tallula Algorithm against all scored hypotheses in the database.

    The algorithm generates K stochastic "discovery lenses" (random scoring
    configurations), scores every hypothesis under each lens, and classifies
    discoveries as robust, resonant, fragile, moderate, or weak.

    **Resonant discoveries** are the primary output — these are drug-cancer
    pairs that score highly only under specific evidence weightings,
    representing potentially novel findings that deterministic scoring misses.
    """
    engine = TallulaEngine()

    results = await engine.run_from_db(
        db=db,
        n_lenses=request.n_lenses,
        cancer_type_id=request.cancer_type_id,
        min_deterministic_score=request.min_deterministic_score,
        dropout_rate=request.dropout_rate,
        dirichlet_alpha=request.dirichlet_alpha,
        seed=request.seed,
        top_n=request.top_n,
    )

    if "error" in results and not results.get("discoveries"):
        raise HTTPException(status_code=404, detail=results["error"])

    # Persist run metadata and discoveries
    run_record = TallulaRun(
        n_lenses=request.n_lenses,
        dropout_rate=request.dropout_rate,
        dirichlet_alpha=request.dirichlet_alpha,
        seed=request.seed,
        n_hypotheses_input=results.get("parameters", {}).get("n_hypotheses_input", 0),
        cancer_type_id=request.cancer_type_id,
        n_resonant=results.get("summary", {}).get("n_resonant", 0),
        n_robust=results.get("summary", {}).get("n_robust", 0),
        n_fragile=results.get("summary", {}).get("n_fragile", 0),
        n_moderate=results.get("summary", {}).get("class_counts", {}).get("moderate", 0),
        n_weak=results.get("summary", {}).get("class_counts", {}).get("weak", 0),
        parameters=results.get("parameters", {}),
        summary=results.get("summary", {}),
    )
    db.add(run_record)
    await db.flush()

    for disc in results.get("discoveries", []):
        dist = disc.get("score_distribution", {})
        metrics = disc.get("tallula_metrics", {})
        db.add(TallulaDiscovery(
            run_id=run_record.id,
            hypothesis_id=disc["hypothesis_id"],
            drug_id=disc.get("drug_id", 0),
            cancer_type_id=disc.get("cancer_type_id", 0),
            discovery_class=disc["discovery_class"],
            deterministic_score=disc.get("deterministic_score", 0.0),
            ubiquity=metrics.get("ubiquity", 0.0),
            resonance=metrics.get("resonance", 0.0),
            fragility_index=metrics.get("fragility_index", 0.0),
            critical_dimension=metrics.get("critical_dimension"),
            score_mean=dist.get("mean"),
            score_median=dist.get("median"),
            score_std=dist.get("std"),
            score_max=dist.get("max"),
            score_min=dist.get("min"),
            resonance_profile=disc.get("resonance_profile"),
            ablation_impacts=disc.get("ablation_impacts"),
        ))

    await db.flush()

    results["run_id"] = run_record.id
    return results


@router.get("/discoveries", summary="List discoveries from the latest Tallula run")
async def list_discoveries(
    discovery_class: str | None = Query(None, description="Filter by class: robust, resonant, fragile, moderate, weak"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List classified discoveries from the most recent Tallula run."""
    # Get most recent run
    run_result = await db.execute(
        select(TallulaRun).order_by(TallulaRun.created_at.desc()).limit(1)
    )
    latest_run = run_result.scalar_one_or_none()
    if not latest_run:
        raise HTTPException(status_code=404, detail="No Tallula runs found. POST /api/tallula/run first.")

    query = select(TallulaDiscovery).where(
        TallulaDiscovery.run_id == latest_run.id
    )
    if discovery_class:
        query = query.where(TallulaDiscovery.discovery_class == discovery_class)

    query = query.order_by(TallulaDiscovery.resonance.desc()).offset(offset).limit(limit)

    result = await db.execute(query)
    discoveries = result.scalars().all()

    # Count
    count_query = select(func.count(TallulaDiscovery.id)).where(
        TallulaDiscovery.run_id == latest_run.id
    )
    if discovery_class:
        count_query = count_query.where(TallulaDiscovery.discovery_class == discovery_class)
    total = (await db.execute(count_query)).scalar() or 0

    return {
        "run_id": latest_run.id,
        "run_date": latest_run.created_at.isoformat() if latest_run.created_at else None,
        "total": total,
        "limit": limit,
        "offset": offset,
        "discoveries": [
            {
                "id": d.id,
                "hypothesis_id": d.hypothesis_id,
                "drug_id": d.drug_id,
                "cancer_type_id": d.cancer_type_id,
                "discovery_class": d.discovery_class,
                "deterministic_score": d.deterministic_score,
                "ubiquity": d.ubiquity,
                "resonance": d.resonance,
                "fragility_index": d.fragility_index,
                "critical_dimension": d.critical_dimension,
                "score_mean": d.score_mean,
                "score_max": d.score_max,
                "resonance_profile": d.resonance_profile,
            }
            for d in discoveries
        ],
    }


@router.get("/discoveries/resonant", summary="Top resonant (novel) discoveries")
async def list_resonant_discoveries(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return the most resonant discoveries — the novel findings.

    These are drug-cancer pairs that score highly only under specific
    evidence weightings. They represent the primary novel output of
    the Tallula Algorithm.
    """
    # Get most recent run
    run_result = await db.execute(
        select(TallulaRun).order_by(TallulaRun.created_at.desc()).limit(1)
    )
    latest_run = run_result.scalar_one_or_none()
    if not latest_run:
        raise HTTPException(status_code=404, detail="No Tallula runs found.")

    result = await db.execute(
        select(TallulaDiscovery).where(
            TallulaDiscovery.run_id == latest_run.id,
            TallulaDiscovery.discovery_class == "resonant",
        ).order_by(TallulaDiscovery.resonance.desc()).limit(limit)
    )
    discoveries = result.scalars().all()

    return {
        "run_id": latest_run.id,
        "n_resonant": latest_run.n_resonant,
        "description": (
            "Resonant discoveries are drug-cancer pairs that score highly "
            "only under specific evidence weightings. They represent novel "
            "findings that deterministic scoring misses because weak signals "
            "in specific dimensions are averaged away."
        ),
        "discoveries": [
            {
                "id": d.id,
                "hypothesis_id": d.hypothesis_id,
                "drug_id": d.drug_id,
                "cancer_type_id": d.cancer_type_id,
                "deterministic_score": d.deterministic_score,
                "resonance": d.resonance,
                "ubiquity": d.ubiquity,
                "score_max": d.score_max,
                "score_median": d.score_median,
                "critical_dimension": d.critical_dimension,
                "resonance_profile": d.resonance_profile,
                "ablation_impacts": d.ablation_impacts,
            }
            for d in discoveries
        ],
    }


@router.get("/runs", summary="List past Tallula runs")
async def list_runs(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """List past Tallula Algorithm executions with summary stats."""
    result = await db.execute(
        select(TallulaRun).order_by(TallulaRun.created_at.desc()).limit(limit)
    )
    runs = result.scalars().all()

    return [
        {
            "id": r.id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "n_lenses": r.n_lenses,
            "n_hypotheses_input": r.n_hypotheses_input,
            "cancer_type_id": r.cancer_type_id,
            "n_resonant": r.n_resonant,
            "n_robust": r.n_robust,
            "n_fragile": r.n_fragile,
            "n_moderate": r.n_moderate,
            "n_weak": r.n_weak,
            "parameters": r.parameters,
        }
        for r in runs
    ]


@router.get("/runs/{run_id}", summary="Full results for a specific Tallula run")
async def get_run(
    run_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get full details of a specific Tallula run including all discoveries."""
    run_result = await db.execute(
        select(TallulaRun).where(TallulaRun.id == run_id)
    )
    run = run_result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail=f"Tallula run {run_id} not found")

    disc_result = await db.execute(
        select(TallulaDiscovery).where(
            TallulaDiscovery.run_id == run_id
        ).order_by(TallulaDiscovery.resonance.desc())
    )
    discoveries = disc_result.scalars().all()

    return {
        "run": {
            "id": run.id,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "parameters": run.parameters,
            "summary": run.summary,
        },
        "discoveries": [
            {
                "id": d.id,
                "hypothesis_id": d.hypothesis_id,
                "drug_id": d.drug_id,
                "cancer_type_id": d.cancer_type_id,
                "discovery_class": d.discovery_class,
                "deterministic_score": d.deterministic_score,
                "ubiquity": d.ubiquity,
                "resonance": d.resonance,
                "fragility_index": d.fragility_index,
                "critical_dimension": d.critical_dimension,
                "score_mean": d.score_mean,
                "score_median": d.score_median,
                "score_std": d.score_std,
                "score_max": d.score_max,
                "score_min": d.score_min,
                "resonance_profile": d.resonance_profile,
                "ablation_impacts": d.ablation_impacts,
            }
            for d in discoveries
        ],
    }


@router.get("/runs/{run_id}/export", summary="Export Tallula run results")
async def export_run(
    run_id: int,
    format: str = Query("json", pattern="^(json|csv)$"),
    discovery_class: str | None = Query(None, description="Filter by class"),
    db: AsyncSession = Depends(get_db),
):
    """Export a Tallula run's discoveries as a downloadable JSON or CSV file."""
    run_result = await db.execute(
        select(TallulaRun).where(TallulaRun.id == run_id)
    )
    run = run_result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail=f"Tallula run {run_id} not found")

    query = select(TallulaDiscovery).where(
        TallulaDiscovery.run_id == run_id
    )
    if discovery_class:
        query = query.where(TallulaDiscovery.discovery_class == discovery_class)
    query = query.order_by(TallulaDiscovery.resonance.desc())

    disc_result = await db.execute(query)
    discoveries = disc_result.scalars().all()

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"tallula_run_{run_id}_{timestamp}"

    if format == "csv":
        return _tallula_csv_response(run, discoveries, filename)
    return _tallula_json_response(run, discoveries, filename)


def _discovery_to_flat_dict(d: TallulaDiscovery) -> dict[str, Any]:
    """Convert a TallulaDiscovery ORM row to a flat dict for export."""
    row: dict[str, Any] = {
        "hypothesis_id": d.hypothesis_id,
        "drug_id": d.drug_id,
        "cancer_type_id": d.cancer_type_id,
        "discovery_class": d.discovery_class,
        "deterministic_score": d.deterministic_score,
        "ubiquity": d.ubiquity,
        "resonance": d.resonance,
        "fragility_index": d.fragility_index,
        "critical_dimension": d.critical_dimension,
        "score_mean": d.score_mean,
        "score_median": d.score_median,
        "score_std": d.score_std,
        "score_max": d.score_max,
        "score_min": d.score_min,
    }
    # Flatten ablation impacts into individual columns
    if d.ablation_impacts:
        for dim, impact in d.ablation_impacts.items():
            row[f"ablation_{dim}"] = impact
    # Add resonance narrative as a single column
    if d.resonance_profile and isinstance(d.resonance_profile, dict):
        row["resonance_narrative"] = d.resonance_profile.get("narrative", "")
    return row


def _tallula_json_response(
    run: TallulaRun,
    discoveries: list[TallulaDiscovery],
    filename: str,
) -> StreamingResponse:
    data = {
        "run": {
            "id": run.id,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "n_lenses": run.n_lenses,
            "n_hypotheses_input": run.n_hypotheses_input,
            "parameters": run.parameters,
            "summary": run.summary,
        },
        "discoveries": [
            {
                "hypothesis_id": d.hypothesis_id,
                "drug_id": d.drug_id,
                "cancer_type_id": d.cancer_type_id,
                "discovery_class": d.discovery_class,
                "deterministic_score": d.deterministic_score,
                "ubiquity": d.ubiquity,
                "resonance": d.resonance,
                "fragility_index": d.fragility_index,
                "critical_dimension": d.critical_dimension,
                "score_mean": d.score_mean,
                "score_median": d.score_median,
                "score_std": d.score_std,
                "score_max": d.score_max,
                "score_min": d.score_min,
                "resonance_profile": d.resonance_profile,
                "ablation_impacts": d.ablation_impacts,
            }
            for d in discoveries
        ],
    }
    content = json.dumps(data, indent=2, default=str)
    return StreamingResponse(
        io.BytesIO(content.encode("utf-8")),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}.json"'},
    )


def _tallula_csv_response(
    run: TallulaRun,
    discoveries: list[TallulaDiscovery],
    filename: str,
) -> StreamingResponse:
    output = io.StringIO()
    writer = csv.writer(output)

    if not discoveries:
        writer.writerow(["No discoveries"])
        output.seek(0)
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode("utf-8")),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
        )

    rows = [_discovery_to_flat_dict(d) for d in discoveries]
    # Use the first row's keys as header (all rows have the same keys)
    headers = list(rows[0].keys())
    writer.writerow(headers)
    for row in rows:
        writer.writerow([row.get(h, "") for h in headers])

    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
    )
