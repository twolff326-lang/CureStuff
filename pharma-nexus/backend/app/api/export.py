"""Export API — generates downloadable hypothesis reports in JSON and CSV."""

import csv
import io
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.hypothesis import Hypothesis

router = APIRouter()


@router.get("/hypothesis/{hypothesis_id}/report")
async def export_hypothesis_report(
    hypothesis_id: int,
    format: str = Query("json", pattern="^(json|csv)$"),
    db: AsyncSession = Depends(get_db),
):
    """Export a single hypothesis report in JSON or CSV format."""
    result = await db.execute(
        select(Hypothesis)
        .options(
            selectinload(Hypothesis.evidence),
            selectinload(Hypothesis.drug),
            selectinload(Hypothesis.cancer_type),
        )
        .where(Hypothesis.id == hypothesis_id)
    )
    hyp = result.scalar_one_or_none()
    if not hyp:
        raise HTTPException(status_code=404, detail=f"Hypothesis {hypothesis_id} not found")

    report = _build_report(hyp)

    if format == "csv":
        return _to_csv_response(report, f"hypothesis_{hypothesis_id}")

    return _to_json_response(report, f"hypothesis_{hypothesis_id}")


@router.get("/hypotheses/batch")
async def export_hypotheses_batch(
    min_score: float = Query(0, ge=0, le=100),
    evidence_strength: str | None = None,
    cancer_type_id: int | None = None,
    limit: int = Query(500, ge=1, le=5000),
    format: str = Query("json", pattern="^(json|csv)$"),
    db: AsyncSession = Depends(get_db),
):
    """Export multiple hypotheses in JSON or CSV format."""
    query = (
        select(Hypothesis)
        .options(
            selectinload(Hypothesis.drug),
            selectinload(Hypothesis.cancer_type),
        )
        .where(Hypothesis.composite_score >= min_score)
        .order_by(Hypothesis.composite_score.desc())
        .limit(limit)
    )

    if evidence_strength:
        query = query.where(Hypothesis.evidence_strength == evidence_strength)
    if cancer_type_id:
        query = query.where(Hypothesis.cancer_type_id == cancer_type_id)

    result = await db.execute(query)
    hypotheses = result.scalars().all()

    reports = [_build_report(h) for h in hypotheses]

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"hypotheses_export_{timestamp}"

    if format == "csv":
        return _to_csv_batch_response(reports, filename)

    return _to_json_response(reports, filename)


@router.get("/summary/stats")
async def export_summary_stats(db: AsyncSession = Depends(get_db)):
    """Export a summary of all strong/moderate hypotheses for reporting."""
    result = await db.execute(
        select(Hypothesis)
        .options(
            selectinload(Hypothesis.drug),
            selectinload(Hypothesis.cancer_type),
        )
        .where(Hypothesis.composite_score >= 50)
        .order_by(Hypothesis.composite_score.desc())
    )
    hypotheses = result.scalars().all()

    by_cancer: dict[str, list] = {}
    for h in hypotheses:
        cancer_name = h.cancer_type.name if h.cancer_type else "Unknown"
        by_cancer.setdefault(cancer_name, []).append({
            "drug": h.drug.name if h.drug else "Unknown",
            "score": h.composite_score,
            "strength": h.evidence_strength,
        })

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "total_strong": sum(1 for h in hypotheses if h.evidence_strength == "strong"),
        "total_moderate": sum(1 for h in hypotheses if h.evidence_strength == "moderate"),
        "cancer_types_covered": len(by_cancer),
        "top_candidates": [
            {
                "drug": h.drug.name if h.drug else "Unknown",
                "cancer": h.cancer_type.name if h.cancer_type else "Unknown",
                "score": h.composite_score,
                "strength": h.evidence_strength,
            }
            for h in hypotheses[:20]
        ],
        "by_cancer_type": by_cancer,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_report(hyp: Hypothesis) -> dict:
    return {
        "id": hyp.id,
        "drug_name": hyp.drug.name if hyp.drug else None,
        "drug_drugbank_id": hyp.drug.drugbank_id if hyp.drug else None,
        "cancer_type": hyp.cancer_type.name if hyp.cancer_type else None,
        "cancer_tcga_code": hyp.cancer_type.tcga_code if hyp.cancer_type else None,
        "title": hyp.title,
        "composite_score": hyp.composite_score,
        "evidence_strength": hyp.evidence_strength,
        "pathway_overlap_score": hyp.pathway_overlap_score,
        "expression_correlation_score": hyp.expression_correlation_score,
        "literature_support_score": hyp.literature_support_score,
        "clinical_evidence_score": hyp.clinical_evidence_score,
        "safety_score": hyp.safety_score,
        "novelty_score": hyp.novelty_score,
        "summary": hyp.summary,
        "mechanism_narrative": hyp.mechanism_narrative,
        "status": hyp.status,
        "evidence_count": len(hyp.evidence) if hyp.evidence else 0,
        "evidence": [
            {
                "type": e.evidence_type,
                "source": e.source_type,
                "source_id": e.source_id,
                "description": e.description,
                "strength": e.strength,
                "confidence": e.confidence,
            }
            for e in (hyp.evidence or [])
        ],
    }


def _to_json_response(data, filename: str) -> StreamingResponse:
    content = json.dumps(data, indent=2, default=str)
    return StreamingResponse(
        io.BytesIO(content.encode("utf-8")),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}.json"'},
    )


def _to_csv_response(report: dict, filename: str) -> StreamingResponse:
    output = io.StringIO()
    writer = csv.writer(output)

    flat_keys = [k for k in report.keys() if k != "evidence"]
    writer.writerow(flat_keys)
    writer.writerow([report.get(k, "") for k in flat_keys])

    if report.get("evidence"):
        writer.writerow([])
        writer.writerow(["Evidence"])
        ev_keys = ["type", "source", "source_id", "description", "strength", "confidence"]
        writer.writerow(ev_keys)
        for ev in report["evidence"]:
            writer.writerow([ev.get(k, "") for k in ev_keys])

    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
    )


def _to_csv_batch_response(reports: list[dict], filename: str) -> StreamingResponse:
    output = io.StringIO()
    writer = csv.writer(output)

    if not reports:
        writer.writerow(["No data"])
        output.seek(0)
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode("utf-8")),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
        )

    flat_keys = [k for k in reports[0].keys() if k != "evidence"]
    writer.writerow(flat_keys)

    for report in reports:
        writer.writerow([report.get(k, "") for k in flat_keys])

    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
    )
