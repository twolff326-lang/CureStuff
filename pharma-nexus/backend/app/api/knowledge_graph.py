from fastapi import APIRouter

router = APIRouter()


@router.get("/stats")
async def get_graph_stats():
    """Get knowledge graph statistics (node/edge counts by type)."""
    # Implementation in future prompt
    return {"nodes": 0, "edges": 0, "node_types": {}, "edge_types": {}}


@router.get("/subgraph/{drug_id}")
async def get_drug_subgraph(drug_id: int, depth: int = 2):
    """Get the knowledge graph subgraph centered on a specific drug."""
    # Implementation in future prompt
    return {"nodes": [], "edges": [], "drug_id": drug_id, "depth": depth}
