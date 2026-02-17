"""API endpoints for LLM synthesis discovery (Strategy 7).

Trigger discovery runs, view proposals, and promote them into hypotheses.
"""

from pydantic import BaseModel
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.tasks.celery_app import celery_app

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


class DiscoverRequest(BaseModel):
    cancer_type_id: int | None = None
    cancer_type_ids: list[int] | None = None
    limit: int = 10
    cost_mode: str | None = None


class PromoteRequest(BaseModel):
    min_confidence: float = 0.5
    limit: int = 50


@router.post("/run")
async def trigger_discovery(request: DiscoverRequest):
    """Trigger LLM synthesis discovery.

    This is the novel part — Claude reads papers about a cancer type
    alongside drug mechanisms and reasons about implicit connections.

    If cancer_type_id is provided, discovers for that cancer only.
    If cancer_type_ids is provided, discovers for those cancers.
    If neither, picks the top cancer types by paper count.
    """
    kwargs = {
        "cost_mode": request.cost_mode,
        "limit": request.limit,
    }
    if request.cancer_type_id:
        kwargs["cancer_type_ids"] = [request.cancer_type_id]
    elif request.cancer_type_ids:
        kwargs["cancer_type_ids"] = request.cancer_type_ids

    task = celery_app.send_task(
        "app.tasks.generate.run_synthesis_discovery",
        kwargs=kwargs,
    )
    return {
        "task_id": task.id,
        "status": "queued",
        "cost_mode": request.cost_mode or "from settings",
    }


@router.post("/promote")
async def trigger_promote(request: PromoteRequest | None = None):
    """Promote accepted proposals into the hypothesis scoring pipeline."""
    min_confidence = request.min_confidence if request else 0.5
    limit = request.limit if request else 50
    task = celery_app.send_task(
        "app.tasks.generate.promote_proposals",
        kwargs={"min_confidence": min_confidence, "limit": limit},
    )
    return {
        "task_id": task.id,
        "status": "queued",
        "min_confidence": min_confidence,
    }


@router.get("/proposals")
async def list_proposals(
    status: str | None = None,
    cancer_type_id: int | None = None,
    min_confidence: float = 0.0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """List discovery proposals with optional filters."""
    from app.services.synthesis_discovery import SynthesisDiscovery

    svc = SynthesisDiscovery()
    proposals = await svc.get_proposals(
        db,
        status=status,
        cancer_type_id=cancer_type_id,
        min_confidence=min_confidence,
        limit=limit,
    )
    return {"proposals": proposals, "total": len(proposals)}
