"""GNN link prediction models.

Stores GNN training runs and per-pair prediction scores so the GNN
dimension can be queried from the database without loading numpy arrays.
"""

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.database import Base


class GNNTrainingRun(Base):
    """Metadata for a single GNN training execution."""
    __tablename__ = "gnn_training_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    status = Column(String(20), nullable=False, default="running")
    epochs = Column(Integer, nullable=False)
    hidden_channels = Column(Integer, nullable=False, default=128)
    out_channels = Column(Integer, nullable=False, default=64)

    # Graph stats at time of training
    n_drug_nodes = Column(Integer)
    n_target_nodes = Column(Integer)
    n_cancer_nodes = Column(Integer)
    n_pathway_nodes = Column(Integer)
    n_positive_labels = Column(Integer)

    # Evaluation metrics
    best_val_auc = Column(Float)
    test_roc_auc = Column(Float)
    test_avg_precision = Column(Float)
    test_accuracy = Column(Float)
    final_loss = Column(Float)

    # Full training summary
    training_summary = Column(JSONB, default=dict)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    completed_at = Column(DateTime)

    __table_args__ = (
        Index("ix_gnn_runs_created", "created_at"),
    )


class GNNPrediction(Base):
    """Cached GNN link prediction score for a drug-cancer pair.

    Populated after training by running inference on all drug-cancer
    combinations. The gnn_score is used as the 8th scoring dimension.
    """
    __tablename__ = "gnn_predictions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(
        Integer, ForeignKey("gnn_training_runs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    drug_id = Column(
        Integer, ForeignKey("drugs.id", ondelete="CASCADE"),
        nullable=False,
    )
    cancer_type_id = Column(
        Integer, ForeignKey("cancer_types.id", ondelete="CASCADE"),
        nullable=False,
    )
    gnn_score = Column(
        Float, nullable=False,
        comment="GNN-predicted link probability (0-1)",
    )

    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_gnn_pred_drug_cancer", "drug_id", "cancer_type_id"),
        Index("ix_gnn_pred_score", gnn_score.desc()),
        Index("ix_gnn_pred_run", "run_id"),
    )
