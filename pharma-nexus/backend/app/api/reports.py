"""API routes for report generation and data export.

Endpoints:
  POST /api/reports/hypothesis/{id}       — Generate hypothesis PDF
  POST /api/reports/cancer/{id}           — Generate cancer summary PDF
  POST /api/reports/drug/{id}             — Generate drug portfolio PDF
  POST /api/reports/comparative           — Generate comparative PDF
  POST /api/reports/novel-discoveries     — Generate novel discoveries PDF
  POST /api/reports/executive-summary     — Generate executive summary PDF
  POST /api/reports/batch                 — Generate all reports (async)
  GET  /api/reports/list                  — List generated reports
  GET  /api/reports/download/{filename}   — Download a report file
  GET  /api/reports/export/hypotheses/csv — Export hypotheses as CSV
  GET  /api/reports/export/hypotheses/excel — Export hypotheses as Excel
  GET  /api/reports/export/hypothesis/{id}/json — Export hypothesis as JSON
"""

import logging
import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.report_generator import ReportGenerator

logger = logging.getLogger(__name__)

router = APIRouter()

REPORT_OUTPUT_DIR = Path("/tmp/reports")


class NovelDiscoveriesParams(BaseModel):
    min_score: int = 45
    min_novelty: int = 60


class ComparativeParams(BaseModel):
    hypothesis_ids: list[int] | None = None
    cancer_type_id: int | None = None
    limit: int = 20


# ------------------------------------------------------------------
# PDF Report Endpoints
# ------------------------------------------------------------------


@router.post("/hypothesis/{hypothesis_id}")
async def generate_hypothesis_report(
    hypothesis_id: int,
    force: bool = Query(False, description="Force regeneration even if cached"),
    db: AsyncSession = Depends(get_db),
):
    """Generate and return PDF for a single hypothesis.

    If a report exists and is recent (< 24h), returns the cached version.
    Use ?force=true to regenerate.
    """
    generator = ReportGenerator()

    if not force:
        cached = await generator._get_cached_report("hypothesis", hypothesis_id, db)
        if cached:
            return FileResponse(
                cached,
                media_type="application/pdf",
                filename=os.path.basename(cached),
            )

    try:
        filepath = await generator.generate_hypothesis_report(hypothesis_id, db)
        await db.commit()
        return FileResponse(
            filepath,
            media_type="application/pdf",
            filename=os.path.basename(filepath),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Hypothesis report generation failed: %s", e)
        raise HTTPException(
            status_code=500, detail=f"Report generation failed: {str(e)[:200]}"
        )


@router.post("/cancer/{cancer_type_id}")
async def generate_cancer_report(
    cancer_type_id: int,
    force: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """Generate cancer type summary PDF."""
    generator = ReportGenerator()

    if not force:
        cached = await generator._get_cached_report(
            "cancer_summary", cancer_type_id, db
        )
        if cached:
            return FileResponse(
                cached,
                media_type="application/pdf",
                filename=os.path.basename(cached),
            )

    try:
        filepath = await generator.generate_cancer_summary_report(
            cancer_type_id, db
        )
        await db.commit()
        return FileResponse(
            filepath,
            media_type="application/pdf",
            filename=os.path.basename(filepath),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Cancer summary report failed: %s", e)
        raise HTTPException(
            status_code=500, detail=f"Report generation failed: {str(e)[:200]}"
        )


@router.post("/novel-discoveries")
async def generate_novel_discoveries_report(
    params: NovelDiscoveriesParams = NovelDiscoveriesParams(),
    force: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """Generate novel discoveries report."""
    generator = ReportGenerator()

    if not force:
        cached = await generator._get_cached_report(
            "novel_discoveries", None, db
        )
        if cached:
            return FileResponse(
                cached,
                media_type="application/pdf",
                filename=os.path.basename(cached),
            )

    try:
        filepath = await generator.generate_novel_discoveries_report(
            db, min_score=params.min_score, min_novelty=params.min_novelty
        )
        await db.commit()
        return FileResponse(
            filepath,
            media_type="application/pdf",
            filename=os.path.basename(filepath),
        )
    except Exception as e:
        logger.error("Novel discoveries report failed: %s", e)
        raise HTTPException(
            status_code=500, detail=f"Report generation failed: {str(e)[:200]}"
        )


@router.post("/executive-summary")
async def generate_executive_summary(
    force: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """Generate executive summary PDF."""
    generator = ReportGenerator()

    if not force:
        cached = await generator._get_cached_report(
            "executive_summary", None, db
        )
        if cached:
            return FileResponse(
                cached,
                media_type="application/pdf",
                filename=os.path.basename(cached),
            )

    try:
        filepath = await generator.generate_executive_summary(db)
        await db.commit()
        return FileResponse(
            filepath,
            media_type="application/pdf",
            filename=os.path.basename(filepath),
        )
    except Exception as e:
        logger.error("Executive summary report failed: %s", e)
        raise HTTPException(
            status_code=500, detail=f"Report generation failed: {str(e)[:200]}"
        )


@router.post("/drug/{drug_id}")
async def generate_drug_portfolio_report(
    drug_id: int,
    force: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """Generate drug portfolio PDF — one drug across all cancer targets."""
    generator = ReportGenerator()

    if not force:
        cached = await generator._get_cached_report("drug_portfolio", drug_id, db)
        if cached:
            return FileResponse(
                cached,
                media_type="application/pdf",
                filename=os.path.basename(cached),
            )

    try:
        filepath = await generator.generate_drug_portfolio_report(drug_id, db)
        await db.commit()
        return FileResponse(
            filepath,
            media_type="application/pdf",
            filename=os.path.basename(filepath),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Drug portfolio report failed: %s", e)
        raise HTTPException(
            status_code=500, detail=f"Report generation failed: {str(e)[:200]}"
        )


@router.post("/comparative")
async def generate_comparative_report(
    params: ComparativeParams = ComparativeParams(),
    force: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """Generate comparative report — side-by-side hypothesis comparison."""
    generator = ReportGenerator()

    entity_id = params.cancer_type_id
    if not force:
        cached = await generator._get_cached_report("comparative", entity_id, db)
        if cached:
            return FileResponse(
                cached,
                media_type="application/pdf",
                filename=os.path.basename(cached),
            )

    try:
        filepath = await generator.generate_comparative_report(
            db,
            hypothesis_ids=params.hypothesis_ids,
            cancer_type_id=params.cancer_type_id,
            limit=params.limit,
        )
        await db.commit()
        return FileResponse(
            filepath,
            media_type="application/pdf",
            filename=os.path.basename(filepath),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Comparative report failed: %s", e)
        raise HTTPException(
            status_code=500, detail=f"Report generation failed: {str(e)[:200]}"
        )


@router.post("/batch")
async def generate_all_reports_endpoint():
    """Generate all reports asynchronously via Celery.

    Returns a task_id for tracking progress.
    """
    from app.tasks.reports import generate_all_reports

    task = generate_all_reports.delay()
    return {"task_id": str(task.id), "status": "queued"}


# ------------------------------------------------------------------
# Report Listing
# ------------------------------------------------------------------


@router.get("/list")
async def list_reports():
    """List all generated reports with metadata."""
    REPORT_OUTPUT_DIR.mkdir(exist_ok=True)

    reports = []
    for f in sorted(REPORT_OUTPUT_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.is_file() and f.suffix in (".pdf", ".csv", ".json", ".xlsx"):
            stat = f.stat()

            # Infer report type from filename
            name = f.stem
            if name.startswith("hypothesis_") and not name.startswith("hypotheses_"):
                report_type = "hypothesis"
            elif name.startswith("cancer_summary_"):
                report_type = "cancer_summary"
            elif name.startswith("drug_portfolio_"):
                report_type = "drug_portfolio"
            elif name.startswith("comparative_report"):
                report_type = "comparative"
            elif name.startswith("novel_discoveries"):
                report_type = "novel_discoveries"
            elif name.startswith("executive_summary"):
                report_type = "executive_summary"
            elif name.startswith("hypotheses_export"):
                report_type = "csv_export"
            else:
                report_type = "other"

            reports.append(
                {
                    "filename": f.name,
                    "type": report_type,
                    "format": f.suffix.lstrip("."),
                    "size_bytes": stat.st_size,
                    "generated_at": datetime.fromtimestamp(
                        stat.st_mtime
                    ).isoformat(),
                    "download_url": f"/api/reports/download/{f.name}",
                }
            )

    return {"reports": reports, "total": len(reports)}


@router.get("/download/{filename}")
async def download_report(filename: str):
    """Download a generated report by filename."""
    filepath = REPORT_OUTPUT_DIR / filename
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404, detail="Report not found")

    # Determine media type
    suffix = filepath.suffix.lower()
    media_types = {
        ".pdf": "application/pdf",
        ".csv": "text/csv",
        ".json": "application/json",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    media_type = media_types.get(suffix, "application/octet-stream")

    return FileResponse(
        str(filepath),
        media_type=media_type,
        filename=filename,
    )


# ------------------------------------------------------------------
# Data Export Endpoints
# ------------------------------------------------------------------


@router.get("/export/hypotheses/csv")
async def export_csv(
    cancer_type_id: int | None = Query(None),
    min_score: int = Query(0),
    min_novelty: int = Query(0),
    db: AsyncSession = Depends(get_db),
):
    """Export hypotheses as CSV for external analysis."""
    generator = ReportGenerator()
    try:
        filepath = await generator.export_hypotheses_csv(
            db,
            cancer_type_id=cancer_type_id,
            min_score=min_score,
            min_novelty=min_novelty,
        )
        return FileResponse(
            filepath,
            media_type="text/csv",
            filename=os.path.basename(filepath),
        )
    except Exception as e:
        logger.error("CSV export failed: %s", e)
        raise HTTPException(
            status_code=500, detail=f"CSV export failed: {str(e)[:200]}"
        )


@router.get("/export/hypotheses/excel")
async def export_excel(
    cancer_type_id: int | None = Query(None),
    min_score: int = Query(0),
    min_novelty: int = Query(0),
    db: AsyncSession = Depends(get_db),
):
    """Export hypotheses as Excel (XLSX) for external analysis."""
    generator = ReportGenerator()
    try:
        filepath = await generator.export_hypotheses_excel(
            db,
            cancer_type_id=cancer_type_id,
            min_score=min_score,
            min_novelty=min_novelty,
        )
        return FileResponse(
            filepath,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=os.path.basename(filepath),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:
        logger.error("Excel export failed: %s", e)
        raise HTTPException(
            status_code=500, detail=f"Excel export failed: {str(e)[:200]}"
        )


@router.get("/export/hypothesis/{hypothesis_id}/json")
async def export_hypothesis_json(
    hypothesis_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Export single hypothesis as complete JSON package."""
    generator = ReportGenerator()
    try:
        filepath = await generator.export_hypothesis_json(hypothesis_id, db)
        return FileResponse(
            filepath,
            media_type="application/json",
            filename=os.path.basename(filepath),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("JSON export failed: %s", e)
        raise HTTPException(
            status_code=500, detail=f"JSON export failed: {str(e)[:200]}"
        )
