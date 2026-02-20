"""GNN link prediction API routes.

Endpoints:
  - POST /train          Kick off full GNN pipeline (export + train + cache)
  - GET  /status         Get latest training run status and metrics
  - GET  /predict/{drug_id}/{cancer_type_id}  Get GNN score for a pair
  - GET  /top-cancers/{drug_id}   Top predicted cancers for a drug
  - GET  /top-drugs/{cancer_type_id}  Top predicted drugs for a cancer
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.gnn_prediction import GNNPrediction, GNNTrainingRun

logger = logging.getLogger(__name__)
router = APIRouter()


class GNNTrainRequest(BaseModel):
    epochs: int = Field(100, ge=10, le=500)
    hidden_channels: int = Field(128, ge=32, le=512)
    out_channels: int = Field(64, ge=16, le=256)


@router.post("/train", summary="Start GNN training pipeline")
async def train_gnn(
    request: GNNTrainRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Kick off the full GNN pipeline: graph export -> training -> prediction caching.

    This runs as a Celery background task. Returns the task ID for status polling.
    """
    from app.tasks.gnn import full_gnn_pipeline

    task = full_gnn_pipeline.delay(
        epochs=request.epochs,
        hidden_channels=request.hidden_channels,
        out_channels=request.out_channels,
    )

    return {
        "task_id": task.id,
        "status": "started",
        "message": f"GNN pipeline started (epochs={request.epochs})",
    }


@router.get("/status", summary="Get GNN training status")
async def gnn_status(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get the latest GNN training run status and evaluation metrics."""
    result = await db.execute(
        select(GNNTrainingRun).order_by(GNNTrainingRun.created_at.desc()).limit(1)
    )
    run = result.scalar_one_or_none()

    if not run:
        return {"status": "no_model", "message": "No GNN model has been trained. POST /api/gnn/train to start."}

    # Count cached predictions
    pred_count = await db.execute(
        select(GNNPrediction.id).where(GNNPrediction.run_id == run.id).limit(1)
    )
    has_predictions = pred_count.scalar_one_or_none() is not None

    return {
        "status": run.status,
        "run_id": run.id,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "epochs": run.epochs,
        "graph_stats": {
            "drugs": run.n_drug_nodes,
            "targets": run.n_target_nodes,
            "cancers": run.n_cancer_nodes,
            "pathways": run.n_pathway_nodes,
            "positive_labels": run.n_positive_labels,
        },
        "test_metrics": {
            "roc_auc": run.test_roc_auc,
            "avg_precision": run.test_avg_precision,
            "accuracy": run.test_accuracy,
        },
        "best_val_auc": run.best_val_auc,
        "final_loss": run.final_loss,
        "has_cached_predictions": has_predictions,
    }


@router.get("/predict/{drug_id}/{cancer_type_id}", summary="Predict link score")
async def predict_link(
    drug_id: int,
    cancer_type_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get the GNN-predicted link score for a drug-cancer pair."""
    # Try database cache first
    latest = await db.execute(
        select(GNNTrainingRun.id).where(
            GNNTrainingRun.status == "completed"
        ).order_by(GNNTrainingRun.created_at.desc()).limit(1)
    )
    run_id = latest.scalar_one_or_none()

    if run_id:
        cached = await db.execute(
            select(GNNPrediction.gnn_score).where(
                GNNPrediction.run_id == run_id,
                GNNPrediction.drug_id == drug_id,
                GNNPrediction.cancer_type_id == cancer_type_id,
            )
        )
        score = cached.scalar_one_or_none()
        if score is not None:
            return {
                "drug_id": drug_id,
                "cancer_type_id": cancer_type_id,
                "gnn_score": round(score, 4),
                "gnn_score_pct": round(score * 100, 1),
                "source": "database_cache",
                "run_id": run_id,
            }

    # Fall back to in-memory predictor
    from app.services.gnn_link_predictor import get_gnn_predictor

    predictor = get_gnn_predictor()
    if predictor.is_loaded:
        score = predictor.predict_link_score(drug_id, cancer_type_id)
        if score is not None:
            return {
                "drug_id": drug_id,
                "cancer_type_id": cancer_type_id,
                "gnn_score": round(score, 4),
                "gnn_score_pct": round(score * 100, 1),
                "source": "in_memory",
            }

    raise HTTPException(
        status_code=404,
        detail="No GNN predictions available. Train a model first: POST /api/gnn/train",
    )


@router.get("/top-cancers/{drug_id}", summary="Top predicted cancers for a drug")
async def top_cancers_for_drug(
    drug_id: int,
    top_k: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get top-K cancer types most likely to respond to this drug (by GNN)."""
    from app.services.gnn_link_predictor import get_gnn_predictor

    predictor = get_gnn_predictor()
    if not predictor.is_loaded:
        raise HTTPException(status_code=404, detail="No GNN model available.")

    results = predictor.predict_top_cancers(drug_id, top_k)
    return {"drug_id": drug_id, "top_k": top_k, "predictions": results}


@router.get("/top-drugs/{cancer_type_id}", summary="Top predicted drugs for a cancer")
async def top_drugs_for_cancer(
    cancer_type_id: int,
    top_k: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get top-K drugs most likely to work against this cancer (by GNN)."""
    from app.services.gnn_link_predictor import get_gnn_predictor

    predictor = get_gnn_predictor()
    if not predictor.is_loaded:
        raise HTTPException(status_code=404, detail="No GNN model available.")

    results = predictor.predict_top_drugs(cancer_type_id, top_k)
    return {"cancer_type_id": cancer_type_id, "top_k": top_k, "predictions": results}


@router.get("/training-history", summary="List GNN training runs")
async def training_history(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """List past GNN training runs with summary metrics."""
    result = await db.execute(
        select(GNNTrainingRun).order_by(GNNTrainingRun.created_at.desc()).limit(limit)
    )
    runs = result.scalars().all()
    return [
        {
            "id": r.id,
            "status": r.status,
            "epochs": r.epochs,
            "test_roc_auc": r.test_roc_auc,
            "test_avg_precision": r.test_avg_precision,
            "best_val_auc": r.best_val_auc,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in runs
    ]
