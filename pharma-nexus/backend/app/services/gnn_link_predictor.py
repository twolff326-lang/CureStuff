"""Graph Neural Network link predictor for drug repurposing.

Uses the Neo4j knowledge graph (~90K nodes, ~435K edges) to train a GNN
that predicts missing Drug->CancerType edges. The model learns from the
full graph topology — a drug that targets proteins interacting with
cancer-mutated genes through shared pathways gets a high predicted link
score, even with zero literature evidence.

Architecture:
  - Heterogeneous graph with 4 node types (Drug, Target, Cancer, Pathway)
  - R-GCN via PyTorch Geometric's to_hetero() on GraphSAGE convolutions
  - Link prediction via dot-product scoring on learned node embeddings
  - Trained on known Drug->CancerType associations (hypotheses with score > 50)
  - Negative sampling: random drug-cancer pairs not in positive set

The GNN-predicted link score becomes an 8th scoring dimension that captures
multi-hop transitive relationships the hardcoded strategies miss.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

MODEL_DIR = Path(os.getenv("GNN_MODEL_DIR", "/tmp/pharma_nexus_gnn"))


async def export_graph_for_training(db) -> dict[str, Any]:
    """Export the knowledge graph as edge lists and node feature matrices.

    Queries PostgreSQL directly and produces arrays for PyTorch Geometric.
    """
    from sqlalchemy import select

    from app.models.cancer_type import CancerType
    from app.models.drug import Drug, DrugTarget
    from app.models.hypothesis import Hypothesis
    from app.models.mutation import Mutation
    from app.models.pathway import Pathway, PathwayTarget
    from app.models.target import ProteinInteraction, Target

    logger.info("Exporting knowledge graph for GNN training")

    # ---- Drug nodes ----
    result = await db.execute(
        select(Drug.id, Drug.name, Drug.status, Drug.mechanism_embedding)
    )
    drugs = result.all()
    drug_id_to_idx = {row[0]: i for i, row in enumerate(drugs)}
    drug_features = []
    for row in drugs:
        status = (row[2] or "").lower()
        status_vec = [
            float(status == "approved"),
            float(status == "investigational"),
            float(status == "experimental"),
            float(status == "withdrawn"),
            float(status not in ("approved", "investigational", "experimental", "withdrawn")),
        ]
        if row[3] is not None:
            emb = list(row[3])[:384]
            emb += [0.0] * (384 - len(emb))
        else:
            emb = [0.0] * 384
        drug_features.append(status_vec + emb)
    drug_feat_np = np.array(drug_features, dtype=np.float32)

    # ---- Target nodes ----
    result = await db.execute(
        select(Target.id, Target.gene_symbol, Target.protein_class, Target.embedding)
    )
    targets = result.all()
    target_id_to_idx = {row[0]: i for i, row in enumerate(targets)}
    target_features = []
    for row in targets:
        if row[3] is not None:
            emb = list(row[3])[:384]
            emb += [0.0] * (384 - len(emb))
        else:
            emb = [0.0] * 384
        target_features.append(emb)
    target_feat_np = np.array(target_features, dtype=np.float32) if targets else np.zeros((0, 384), dtype=np.float32)

    # ---- CancerType nodes ----
    result = await db.execute(
        select(CancerType.id, CancerType.name, CancerType.tissue, CancerType.sample_count)
    )
    cancers = result.all()
    cancer_id_to_idx = {row[0]: i for i, row in enumerate(cancers)}
    cancer_features = [[min((row[3] or 0) / 1000.0, 1.0)] for row in cancers]
    cancer_feat_np = np.array(cancer_features, dtype=np.float32) if cancers else np.zeros((0, 1), dtype=np.float32)

    # ---- Pathway nodes ----
    result = await db.execute(select(Pathway.id, Pathway.name, Pathway.source))
    pathways = result.all()
    pathway_id_to_idx = {row[0]: i for i, row in enumerate(pathways)}
    pathway_features = [
        [float((row[2] or "").lower() == "kegg"), float((row[2] or "").lower() == "reactome")]
        for row in pathways
    ]
    pathway_feat_np = np.array(pathway_features, dtype=np.float32) if pathways else np.zeros((0, 2), dtype=np.float32)

    # ---- Edge: Drug -> TARGETS -> Target ----
    result = await db.execute(select(DrugTarget.drug_id, DrugTarget.target_id))
    dt_edges = [
        [drug_id_to_idx[r[0]], target_id_to_idx[r[1]]]
        for r in result.all()
        if r[0] in drug_id_to_idx and r[1] in target_id_to_idx
    ]
    dt_edge_np = np.array(dt_edges, dtype=np.int64).T if dt_edges else np.zeros((2, 0), dtype=np.int64)

    # ---- Edge: Target -> PARTICIPATES_IN -> Pathway ----
    result = await db.execute(select(PathwayTarget.target_id, PathwayTarget.pathway_id))
    tp_edges = [
        [target_id_to_idx[r[0]], pathway_id_to_idx[r[1]]]
        for r in result.all()
        if r[0] in target_id_to_idx and r[1] in pathway_id_to_idx
    ]
    tp_edge_np = np.array(tp_edges, dtype=np.int64).T if tp_edges else np.zeros((2, 0), dtype=np.int64)

    # ---- Edge: Target <-> INTERACTS_WITH <-> Target ----
    result = await db.execute(
        select(ProteinInteraction.protein_a_uniprot, ProteinInteraction.protein_b_uniprot).where(
            ProteinInteraction.interaction_score >= 700
        )
    )
    ppi_rows = result.all()
    result2 = await db.execute(
        select(Target.id, Target.uniprot_id).where(Target.uniprot_id.isnot(None))
    )
    uniprot_to_idx = {row[1]: target_id_to_idx[row[0]] for row in result2.all() if row[0] in target_id_to_idx}
    ppi_edges = [
        [uniprot_to_idx[a], uniprot_to_idx[b]]
        for a, b in ppi_rows
        if a in uniprot_to_idx and b in uniprot_to_idx
    ]
    ppi_edge_np = np.array(ppi_edges, dtype=np.int64).T if ppi_edges else np.zeros((2, 0), dtype=np.int64)

    # ---- Edge: Target -> MUTATED_IN -> CancerType ----
    gene_to_tidx = {}
    for t in targets:
        if t[1]:
            gene_to_tidx[t[1]] = target_id_to_idx[t[0]]
    result = await db.execute(select(Mutation.gene_symbol, Mutation.cancer_type_id).distinct())
    mut_edges = [
        [gene_to_tidx[g], cancer_id_to_idx[c]]
        for g, c in result.all()
        if g in gene_to_tidx and c in cancer_id_to_idx
    ]
    mut_edge_np = np.array(mut_edges, dtype=np.int64).T if mut_edges else np.zeros((2, 0), dtype=np.int64)

    # ---- Supervision: Drug -> CancerType from high-scoring hypotheses ----
    result = await db.execute(
        select(Hypothesis.drug_id, Hypothesis.cancer_type_id).where(Hypothesis.composite_score >= 50)
    )
    pos_edges = [
        [drug_id_to_idx[r[0]], cancer_id_to_idx[r[1]]]
        for r in result.all()
        if r[0] in drug_id_to_idx and r[1] in cancer_id_to_idx
    ]
    pos_edge_np = np.array(pos_edges, dtype=np.int64).T if pos_edges else np.zeros((2, 0), dtype=np.int64)

    # Save to disk
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    arrays = {
        "drug_features": drug_feat_np, "target_features": target_feat_np,
        "cancer_features": cancer_feat_np, "pathway_features": pathway_feat_np,
        "dt_edge": dt_edge_np, "tp_edge": tp_edge_np,
        "ppi_edge": ppi_edge_np, "mut_edge": mut_edge_np,
        "pos_labels": pos_edge_np,
    }
    for name, arr in arrays.items():
        np.save(MODEL_DIR / f"{name}.npy", arr)

    meta = {
        "node_counts": {"drug": len(drugs), "target": len(targets), "cancer": len(cancers), "pathway": len(pathways)},
        "drug_id_to_idx": {str(k): v for k, v in drug_id_to_idx.items()},
        "target_id_to_idx": {str(k): v for k, v in target_id_to_idx.items()},
        "cancer_id_to_idx": {str(k): v for k, v in cancer_id_to_idx.items()},
        "pathway_id_to_idx": {str(k): v for k, v in pathway_id_to_idx.items()},
    }
    with open(MODEL_DIR / "graph_metadata.json", "w") as f:
        json.dump(meta, f, indent=2)

    logger.info("Graph export complete: %s", meta["node_counts"])
    return meta


def train_gnn_model(
    epochs: int = 100,
    hidden_channels: int = 128,
    out_channels: int = 64,
    lr: float = 0.01,
    neg_ratio: int = 3,
) -> dict[str, Any]:
    """Train R-GCN link predictor on exported graph data.

    Returns training summary with loss history and test metrics.
    """
    try:
        import torch
        import torch.nn.functional as F
        from torch_geometric.data import HeteroData
        from torch_geometric.nn import SAGEConv, to_hetero
        from torch_geometric.transforms import RandomLinkSplit
    except ImportError:
        raise ImportError(
            "PyTorch Geometric required. Install: pip install torch-geometric torch-scatter torch-sparse"
        )

    logger.info("Loading graph data from %s", MODEL_DIR)

    # Load arrays
    drug_feat = torch.tensor(np.load(MODEL_DIR / "drug_features.npy"), dtype=torch.float)
    target_feat = torch.tensor(np.load(MODEL_DIR / "target_features.npy"), dtype=torch.float)
    cancer_feat = torch.tensor(np.load(MODEL_DIR / "cancer_features.npy"), dtype=torch.float)
    pathway_feat = torch.tensor(np.load(MODEL_DIR / "pathway_features.npy"), dtype=torch.float)
    dt_edge = torch.tensor(np.load(MODEL_DIR / "dt_edge.npy"), dtype=torch.long)
    tp_edge = torch.tensor(np.load(MODEL_DIR / "tp_edge.npy"), dtype=torch.long)
    ppi_edge = torch.tensor(np.load(MODEL_DIR / "ppi_edge.npy"), dtype=torch.long)
    mut_edge = torch.tensor(np.load(MODEL_DIR / "mut_edge.npy"), dtype=torch.long)
    pos_labels = torch.tensor(np.load(MODEL_DIR / "pos_labels.npy"), dtype=torch.long)

    with open(MODEL_DIR / "graph_metadata.json") as f:
        meta = json.load(f)

    # Build HeteroData
    data = HeteroData()
    data["drug"].x = drug_feat
    data["target"].x = target_feat
    data["cancer"].x = cancer_feat
    data["pathway"].x = pathway_feat

    def _add_edge(src_t, rel, dst_t, edge_idx):
        if edge_idx.shape[1] > 0:
            data[src_t, rel, dst_t].edge_index = edge_idx

    _add_edge("drug", "targets", "target", dt_edge)
    _add_edge("target", "targeted_by", "drug", dt_edge[[1, 0]])
    _add_edge("target", "in_pathway", "pathway", tp_edge)
    _add_edge("pathway", "contains", "target", tp_edge[[1, 0]])
    _add_edge("target", "interacts", "target", ppi_edge)
    _add_edge("target", "mutated_in", "cancer", mut_edge)
    _add_edge("cancer", "has_mutation", "target", mut_edge[[1, 0]])
    _add_edge("drug", "repurposing_candidate", "cancer", pos_labels)
    _add_edge("cancer", "candidate_for", "drug", pos_labels[[1, 0]])

    if pos_labels.shape[1] < 10:
        return {"status": "insufficient_data", "positive_labels": int(pos_labels.shape[1])}

    # Train/val/test split on supervision edges
    transform = RandomLinkSplit(
        num_val=0.15, num_test=0.15, neg_sampling_ratio=neg_ratio,
        edge_types=("drug", "repurposing_candidate", "cancer"),
        rev_edge_types=("cancer", "candidate_for", "drug"),
    )
    train_data, val_data, test_data = transform(data)

    # Model
    class GNNEncoder(torch.nn.Module):
        def __init__(self, hc, oc):
            super().__init__()
            self.conv1 = SAGEConv((-1, -1), hc)
            self.conv2 = SAGEConv((-1, -1), oc)

        def forward(self, x, edge_index):
            x = self.conv1(x, edge_index).relu()
            x = F.dropout(x, p=0.3, training=self.training)
            return self.conv2(x, edge_index)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = to_hetero(GNNEncoder(hidden_channels, out_channels), data.metadata(), aggr="mean").to(device)
    train_data, val_data, test_data = train_data.to(device), val_data.to(device), test_data.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)

    sup_key = ("drug", "repurposing_candidate", "cancer")
    loss_history = []
    best_val_auc = 0.0

    def _score(z, split_data):
        ei = split_data[sup_key].edge_label_index
        lbl = split_data[sup_key].edge_label
        s, d = z["drug"][ei[0]], z["cancer"][ei[1]]
        dim = min(s.shape[1], d.shape[1])
        pred = (s[:, :dim] * d[:, :dim]).sum(-1)
        return pred, lbl

    for epoch in range(epochs):
        model.train()
        z = model(train_data.x_dict, train_data.edge_index_dict)
        pred, lbl = _score(z, train_data)
        loss = F.binary_cross_entropy_with_logits(pred, lbl.float())
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        loss_history.append(float(loss.item()))

        if (epoch + 1) % 10 == 0:
            model.eval()
            with torch.no_grad():
                z_v = model(val_data.x_dict, val_data.edge_index_dict)
                vp, vl = _score(z_v, val_data)
                try:
                    from sklearn.metrics import roc_auc_score
                    val_auc = float(roc_auc_score(vl.cpu().numpy(), torch.sigmoid(vp).cpu().numpy()))
                except Exception:
                    val_auc = float(((torch.sigmoid(vp) > 0.5).float() == vl).float().mean())
                if val_auc > best_val_auc:
                    best_val_auc = val_auc
                    torch.save(model.state_dict(), MODEL_DIR / "best_model.pt")
            logger.info("Epoch %d/%d: loss=%.4f val_auc=%.3f", epoch + 1, epochs, loss_history[-1], val_auc)

    # Test evaluation
    model.eval()
    with torch.no_grad():
        z_t = model(test_data.x_dict, test_data.edge_index_dict)
        tp, tl = _score(z_t, test_data)
        tp_sig = torch.sigmoid(tp)
        test_acc = float(((tp_sig > 0.5).float() == tl).float().mean())
        try:
            from sklearn.metrics import roc_auc_score, average_precision_score
            test_auc = float(roc_auc_score(tl.cpu().numpy(), tp_sig.cpu().numpy()))
            test_ap = float(average_precision_score(tl.cpu().numpy(), tp_sig.cpu().numpy()))
        except Exception:
            test_auc, test_ap = test_acc, test_acc

    # Save embeddings for fast inference
    torch.save(model.state_dict(), MODEL_DIR / "final_model.pt")
    with torch.no_grad():
        z_final = model(test_data.x_dict, test_data.edge_index_dict)
        np.save(MODEL_DIR / "drug_embeddings_gnn.npy", z_final["drug"].cpu().numpy())
        np.save(MODEL_DIR / "cancer_embeddings_gnn.npy", z_final["cancer"].cpu().numpy())

    summary = {
        "status": "completed", "epochs": epochs,
        "final_loss": round(loss_history[-1], 4) if loss_history else None,
        "best_val_auc": round(best_val_auc, 4),
        "test_metrics": {"accuracy": round(test_acc, 4), "roc_auc": round(test_auc, 4), "avg_precision": round(test_ap, 4)},
        "graph_stats": meta.get("node_counts", {}),
        "model_path": str(MODEL_DIR),
    }
    with open(MODEL_DIR / "training_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    logger.info("GNN training complete: test_auc=%.4f test_ap=%.4f", test_auc, test_ap)
    return summary


class GNNLinkPredictor:
    """Fast inference using pre-trained GNN embeddings (dot-product, no PyTorch needed)."""

    def __init__(self):
        self._drug_emb: np.ndarray | None = None
        self._cancer_emb: np.ndarray | None = None
        self._drug_id_to_idx: dict[int, int] = {}
        self._cancer_id_to_idx: dict[int, int] = {}
        self._loaded = False

    def load(self) -> bool:
        """Load pre-trained embeddings. Returns True if successful."""
        try:
            paths = [MODEL_DIR / n for n in ("drug_embeddings_gnn.npy", "cancer_embeddings_gnn.npy", "graph_metadata.json")]
            if not all(p.exists() for p in paths):
                logger.warning("GNN model not found at %s", MODEL_DIR)
                return False
            self._drug_emb = np.load(paths[0])
            self._cancer_emb = np.load(paths[1])
            with open(paths[2]) as f:
                meta = json.load(f)
            self._drug_id_to_idx = {int(k): v for k, v in meta["drug_id_to_idx"].items()}
            self._cancer_id_to_idx = {int(k): v for k, v in meta["cancer_id_to_idx"].items()}
            self._loaded = True
            logger.info("GNN predictor loaded: %d drugs, %d cancers", len(self._drug_id_to_idx), len(self._cancer_id_to_idx))
            return True
        except Exception as e:
            logger.warning("Failed to load GNN model: %s", e)
            return False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def predict_link_score(self, drug_id: int, cancer_type_id: int) -> float | None:
        """Predict link score (0-1) between a drug and cancer type."""
        if not self._loaded:
            return None
        didx = self._drug_id_to_idx.get(drug_id)
        cidx = self._cancer_id_to_idx.get(cancer_type_id)
        if didx is None or cidx is None:
            return None
        d, c = self._drug_emb[didx], self._cancer_emb[cidx]
        dim = min(len(d), len(c))
        dot = float(np.dot(d[:dim], c[:dim]))
        return float(1.0 / (1.0 + np.exp(-dot)))

    def predict_top_cancers(self, drug_id: int, top_k: int = 10) -> list[dict]:
        if not self._loaded or drug_id not in self._drug_id_to_idx:
            return []
        d = self._drug_emb[self._drug_id_to_idx[drug_id]]
        dim = min(d.shape[0], self._cancer_emb.shape[1])
        scores = 1.0 / (1.0 + np.exp(-(self._cancer_emb[:, :dim] @ d[:dim])))
        idx_to_cid = {v: k for k, v in self._cancer_id_to_idx.items()}
        return [{"cancer_type_id": idx_to_cid[int(i)], "gnn_score": round(float(scores[i]), 4)}
                for i in np.argsort(scores)[::-1][:top_k] if int(i) in idx_to_cid]

    def predict_top_drugs(self, cancer_type_id: int, top_k: int = 10) -> list[dict]:
        if not self._loaded or cancer_type_id not in self._cancer_id_to_idx:
            return []
        c = self._cancer_emb[self._cancer_id_to_idx[cancer_type_id]]
        dim = min(c.shape[0], self._drug_emb.shape[1])
        scores = 1.0 / (1.0 + np.exp(-(self._drug_emb[:, :dim] @ c[:dim])))
        idx_to_did = {v: k for k, v in self._drug_id_to_idx.items()}
        return [{"drug_id": idx_to_did[int(i)], "gnn_score": round(float(scores[i]), 4)}
                for i in np.argsort(scores)[::-1][:top_k] if int(i) in idx_to_did]

    def get_training_summary(self) -> dict | None:
        p = MODEL_DIR / "training_summary.json"
        if not p.exists():
            return None
        with open(p) as f:
            return json.load(f)


_predictor = GNNLinkPredictor()


def get_gnn_predictor() -> GNNLinkPredictor:
    """Get the module-level GNN predictor, loading if needed."""
    if not _predictor.is_loaded:
        _predictor.load()
    return _predictor
