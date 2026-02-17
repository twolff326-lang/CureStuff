"""Knowledge graph API routes — sync, stats, subgraph, path-finding, discovery."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.services.graph_queries import GraphQueryService
from app.services.knowledge_graph import KnowledgeGraphService
from app.tasks.celery_app import celery_app

router = APIRouter()

# Lazy-initialized services (share the Neo4j driver)
_kg_service: KnowledgeGraphService | None = None
_query_service: GraphQueryService | None = None


def _get_kg_service() -> KnowledgeGraphService:
    global _kg_service
    if _kg_service is None:
        _kg_service = KnowledgeGraphService()
    return _kg_service


def _get_query_service() -> GraphQueryService:
    global _query_service
    if _query_service is None:
        _query_service = GraphQueryService(_get_kg_service().driver)
    return _query_service


# ------------------------------------------------------------------
# Sync
# ------------------------------------------------------------------


@router.post("/sync")
async def trigger_graph_sync():
    """Trigger full PostgreSQL -> Neo4j synchronization (async via Celery)."""
    task = celery_app.send_task("app.tasks.ingest.sync_knowledge_graph")
    return {"task_id": task.id, "status": "queued"}


# ------------------------------------------------------------------
# Statistics
# ------------------------------------------------------------------


@router.get("/stats")
async def get_graph_stats():
    """Get knowledge graph statistics (node/edge counts by type)."""
    qs = _get_query_service()
    return await qs.get_graph_stats()


# ------------------------------------------------------------------
# Neighborhood / subgraph extraction
# ------------------------------------------------------------------


@router.get("/drug/{drugbank_id}/neighborhood")
async def get_drug_neighborhood(
    drugbank_id: str,
    depth: int = Query(2, ge=1, le=3),
):
    """Subgraph around a drug for D3.js visualization."""
    qs = _get_query_service()
    return await qs.get_drug_neighborhood(drugbank_id, depth=depth)


@router.get("/cancer/{tcga_code}/neighborhood")
async def get_cancer_neighborhood(
    tcga_code: str,
    depth: int = Query(1, ge=1, le=3),
):
    """Subgraph around a cancer type."""
    qs = _get_query_service()
    return await qs.get_cancer_neighborhood(tcga_code, depth=depth)


# ------------------------------------------------------------------
# Path finding
# ------------------------------------------------------------------


@router.get("/paths")
async def find_paths(
    drug_id: str = Query(..., description="DrugBank ID (e.g. DB00001)"),
    cancer_id: str = Query(..., description="TCGA code (e.g. BRCA)"),
    max_hops: int = Query(3, ge=1, le=4),
):
    """Find all paths between a drug and cancer type (1 to max_hops)."""
    qs = _get_query_service()
    paths = await qs.find_all_paths(drug_id, cancer_id, max_hops=max_hops)
    return {"drug_id": drug_id, "cancer_id": cancer_id, "max_hops": max_hops, "paths": paths}


# ------------------------------------------------------------------
# Discovery queries
# ------------------------------------------------------------------


@router.get("/drug/{drugbank_id}/direct-targets/{tcga_code}")
async def get_direct_targets(drugbank_id: str, tcga_code: str):
    """Drugs directly targeting cancer-altered genes (filtered to one drug)."""
    qs = _get_query_service()
    results = await qs.find_direct_targets(tcga_code)
    filtered = [r for r in results if r.get("drug_id") == drugbank_id]
    return {"drug_id": drugbank_id, "cancer": tcga_code, "connections": filtered}


@router.get("/drug/{drugbank_id}/pathway-connections/{tcga_code}")
async def get_pathway_connections(drugbank_id: str, tcga_code: str):
    """Pathway-mediated connections between a drug and cancer."""
    qs = _get_query_service()
    results = await qs.find_pathway_connections(tcga_code)
    filtered = [r for r in results if r.get("drug_id") == drugbank_id]
    return {"drug_id": drugbank_id, "cancer": tcga_code, "connections": filtered}


@router.get("/drug/{drugbank_id}/interaction-connections/{tcga_code}")
async def get_interaction_connections(drugbank_id: str, tcga_code: str):
    """Interaction-mediated connections between a drug and cancer."""
    qs = _get_query_service()
    results = await qs.find_interaction_connections(tcga_code)
    filtered = [r for r in results if r.get("drug_id") == drugbank_id]
    return {"drug_id": drugbank_id, "cancer": tcga_code, "connections": filtered}


# ------------------------------------------------------------------
# Literature & trial connections
# ------------------------------------------------------------------


@router.get("/literature-connections")
async def get_literature_connections(
    cancer: str | None = Query(None, description="TCGA code to filter by"),
):
    """Drug-cancer pairs with literature support."""
    qs = _get_query_service()
    return await qs.find_literature_connections(cancer_tcga_code=cancer)


@router.get("/trial-connections")
async def get_trial_connections(
    cancer: str | None = Query(None, description="TCGA code to filter by"),
):
    """Drug-cancer pairs in clinical trials."""
    qs = _get_query_service()
    return await qs.find_trial_connections(cancer_tcga_code=cancer)


# ------------------------------------------------------------------
# Hypothesis subgraph
# ------------------------------------------------------------------


@router.get("/hypothesis/{hypothesis_id}/subgraph")
async def get_hypothesis_subgraph(
    hypothesis_id: int,
    session: AsyncSession = Depends(get_db),
):
    """Subgraph for a specific hypothesis — looks up drug + cancer, returns graph."""
    from app.models.hypothesis import Hypothesis

    from sqlalchemy import select

    result = await session.execute(
        select(Hypothesis).where(Hypothesis.id == hypothesis_id)
    )
    hypothesis = result.scalar_one_or_none()
    if not hypothesis:
        raise HTTPException(status_code=404, detail="Hypothesis not found")

    # Get the drug's drugbank_id and cancer's tcga_code
    from app.models.drug import Drug
    from app.models.cancer_type import CancerType

    drug_result = await session.execute(
        select(Drug.drugbank_id).where(Drug.id == hypothesis.drug_id)
    )
    drugbank_id = drug_result.scalar_one_or_none()

    cancer_result = await session.execute(
        select(CancerType.tcga_code).where(CancerType.id == hypothesis.cancer_type_id)
    )
    tcga_code = cancer_result.scalar_one_or_none()

    if not drugbank_id or not tcga_code:
        raise HTTPException(
            status_code=404, detail="Drug or cancer type not found for hypothesis"
        )

    qs = _get_query_service()
    subgraph = await qs.get_hypothesis_subgraph(drugbank_id, tcga_code)
    return {"hypothesis_id": hypothesis_id, **subgraph}
