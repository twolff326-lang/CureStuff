"""Celery tasks for GNN training and inference.

Tasks:
  - export_graph: Export knowledge graph as training data
  - train_gnn: Train the R-GCN link predictor
  - cache_gnn_predictions: Run inference on all drug-cancer pairs and cache in DB
  - full_gnn_pipeline: Export -> Train -> Cache (complete pipeline)
"""

import logging
from datetime import datetime, timezone

from app.tasks.celery_app import celery_app
from app.tasks.utils import run_async, task_session

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=2, name="app.tasks.gnn.export_graph")
def export_graph(self):
    """Export knowledge graph for GNN training."""
    logger.info("Starting graph export for GNN training")
    try:
        async def _export():
            from app.services.gnn_link_predictor import export_graph_for_training

            async with task_session() as session:
                return await export_graph_for_training(session)

        result = run_async(_export())
        logger.info("Graph export complete: %s", result.get("node_counts", {}))
        return result

    except Exception as exc:
        logger.error("Graph export failed: %s", exc)
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(bind=True, max_retries=1, name="app.tasks.gnn.train_gnn")
def train_gnn(self, epochs=100, hidden_channels=128, out_channels=64, lr=0.01):
    """Train the GNN link predictor model."""
    logger.info("Starting GNN training (epochs=%d)", epochs)
    try:
        from app.services.gnn_link_predictor import train_gnn_model

        summary = train_gnn_model(
            epochs=epochs,
            hidden_channels=hidden_channels,
            out_channels=out_channels,
            lr=lr,
        )

        # Store training run in database
        async def _store_run():
            from app.models.gnn_prediction import GNNTrainingRun

            async with task_session() as session:
                run = GNNTrainingRun(
                    status=summary.get("status", "completed"),
                    epochs=epochs,
                    hidden_channels=hidden_channels,
                    out_channels=out_channels,
                    n_drug_nodes=summary.get("graph_stats", {}).get("drug", 0),
                    n_target_nodes=summary.get("graph_stats", {}).get("target", 0),
                    n_cancer_nodes=summary.get("graph_stats", {}).get("cancer", 0),
                    n_pathway_nodes=summary.get("graph_stats", {}).get("pathway", 0),
                    n_positive_labels=summary.get("edge_stats", {}).get("positive_labels", 0),
                    best_val_auc=summary.get("best_val_auc"),
                    test_roc_auc=summary.get("test_metrics", {}).get("roc_auc"),
                    test_avg_precision=summary.get("test_metrics", {}).get("avg_precision"),
                    test_accuracy=summary.get("test_metrics", {}).get("accuracy"),
                    final_loss=summary.get("final_loss"),
                    training_summary=summary,
                    completed_at=datetime.now(timezone.utc),
                )
                session.add(run)
                await session.commit()
                return run.id

        run_id = run_async(_store_run())
        summary["run_id"] = run_id

        logger.info("GNN training complete: %s", summary.get("test_metrics", {}))
        return summary

    except Exception as exc:
        logger.error("GNN training failed: %s", exc)
        raise self.retry(exc=exc, countdown=120)


@celery_app.task(bind=True, max_retries=2, name="app.tasks.gnn.cache_predictions")
def cache_gnn_predictions(self, run_id=None):
    """Cache GNN predictions in the database for all drug-cancer pairs."""
    logger.info("Caching GNN predictions (run_id=%s)", run_id)
    try:
        async def _cache():
            from sqlalchemy import select

            from app.models.cancer_type import CancerType
            from app.models.drug import Drug
            from app.models.gnn_prediction import GNNPrediction, GNNTrainingRun
            from app.services.gnn_link_predictor import get_gnn_predictor

            predictor = get_gnn_predictor()
            if not predictor.is_loaded:
                if not predictor.load():
                    return {"status": "error", "message": "GNN model not loaded"}

            async with task_session() as session:
                # Get or determine run_id
                nonlocal run_id
                if not run_id:
                    result = await session.execute(
                        select(GNNTrainingRun.id).where(
                            GNNTrainingRun.status == "completed"
                        ).order_by(GNNTrainingRun.created_at.desc()).limit(1)
                    )
                    run_id = result.scalar_one_or_none()
                    if not run_id:
                        return {"status": "error", "message": "No completed training runs"}

                # Get all drugs and cancers
                drugs = await session.execute(select(Drug.id))
                drug_ids = [r[0] for r in drugs.all()]

                cancers = await session.execute(select(CancerType.id))
                cancer_ids = [r[0] for r in cancers.all()]

                # Generate predictions in batches to bound memory
                count = 0
                batch: list[dict] = []
                for drug_id in drug_ids:
                    for cancer_id in cancer_ids:
                        score = predictor.predict_link_score(drug_id, cancer_id)
                        if score is not None and score > 0.1:
                            batch.append({
                                "run_id": run_id,
                                "drug_id": drug_id,
                                "cancer_type_id": cancer_id,
                                "gnn_score": score,
                            })
                            count += 1

                        if len(batch) >= 500:
                            await session.execute(
                                GNNPrediction.__table__.insert(), batch
                            )
                            await session.flush()
                            batch.clear()

                if batch:
                    await session.execute(
                        GNNPrediction.__table__.insert(), batch
                    )
                await session.commit()
                return {"status": "completed", "predictions_cached": count, "run_id": run_id}

        result = run_async(_cache())
        logger.info("GNN prediction caching complete: %s", result)
        return result

    except Exception as exc:
        logger.error("GNN prediction caching failed: %s", exc)
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(bind=True, name="app.tasks.gnn.full_gnn_pipeline")
def full_gnn_pipeline(self, epochs=100, hidden_channels=128, out_channels=64):
    """Run the complete GNN pipeline: export -> train -> cache."""
    logger.info("Starting full GNN pipeline")

    # Step 1: Export graph
    export_result = export_graph()

    # Step 2: Train model
    train_result = train_gnn(
        epochs=epochs,
        hidden_channels=hidden_channels,
        out_channels=out_channels,
    )

    if train_result.get("status") != "completed":
        return {"status": "failed", "step": "training", "details": train_result}

    # Step 3: Cache predictions
    cache_result = cache_gnn_predictions(run_id=train_result.get("run_id"))

    return {
        "status": "completed",
        "export": export_result,
        "training": train_result,
        "cache": cache_result,
    }
