"""Literature API routes — search, browse, evidence, and analysis endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.drug import LiteratureDrug
from app.models.literature import Literature, LiteratureCancer, LiteratureTarget
from app.services.literature_analyzer import LiteratureAnalyzer

router = APIRouter(prefix="/api/literature", tags=["literature"])

_analyzer = LiteratureAnalyzer()


# ------------------------------------------------------------------
# Search
# ------------------------------------------------------------------


@router.get("/search")
async def search_literature(
    q: str = Query(..., min_length=1, description="Search query text"),
    type: str = Query("hybrid", regex="^(keyword|semantic|hybrid)$"),
    top_k: int = Query(20, ge=1, le=100),
    drug_id: int | None = Query(None),
    cancer_type_id: int | None = Query(None),
    session: AsyncSession = Depends(get_db),
):
    """Combined keyword + semantic search over literature.

    - keyword: PostgreSQL full-text search on title + abstract
    - semantic: embed query → pgvector cosine similarity
    - hybrid: both merged via Reciprocal Rank Fusion
    """
    if type == "keyword":
        results = await _analyzer.search_keyword(session, q, top_k=top_k)
    elif type == "semantic":
        results = await _analyzer.search_similar_abstracts(session, q, top_k=top_k)
    else:
        results = await _analyzer.search_hybrid(session, q, top_k=top_k)

    # Optional post-filter by drug or cancer
    if drug_id:
        linked = await session.execute(
            select(LiteratureDrug.literature_id).where(
                LiteratureDrug.drug_id == drug_id
            )
        )
        linked_ids = {r[0] for r in linked.all()}
        results = [r for r in results if r["id"] in linked_ids]

    if cancer_type_id:
        linked = await session.execute(
            select(LiteratureCancer.literature_id).where(
                LiteratureCancer.cancer_type_id == cancer_type_id
            )
        )
        linked_ids = {r[0] for r in linked.all()}
        results = [r for r in results if r["id"] in linked_ids]

    return {"results": results, "count": len(results), "search_type": type}


@router.get("/similar")
async def search_similar(
    text: str = Query(..., min_length=1, description="Query text"),
    top_k: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
):
    """Semantic similarity search — embed query, find closest abstracts."""
    results = await _analyzer.search_similar_abstracts(session, text, top_k=top_k)
    return {"results": results, "count": len(results)}


# ------------------------------------------------------------------
# Browse by entity
# ------------------------------------------------------------------


@router.get("/drug/{drug_id}")
async def get_drug_literature(
    drug_id: int,
    mention_type: str | None = Query(None),
    sort_by: str = Query("pub_date", regex="^(pub_date|title)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
):
    """All papers mentioning a drug."""
    query = (
        select(Literature)
        .join(LiteratureDrug, LiteratureDrug.literature_id == Literature.id)
        .where(LiteratureDrug.drug_id == drug_id)
    )
    if mention_type:
        query = query.where(LiteratureDrug.mention_type == mention_type)

    if sort_by == "pub_date":
        query = query.order_by(desc(Literature.pub_date))
    else:
        query = query.order_by(Literature.title)

    # Count
    count_q = (
        select(func.count(Literature.id))
        .join(LiteratureDrug, LiteratureDrug.literature_id == Literature.id)
        .where(LiteratureDrug.drug_id == drug_id)
    )
    if mention_type:
        count_q = count_q.where(LiteratureDrug.mention_type == mention_type)
    total = (await session.execute(count_q)).scalar() or 0

    offset = (page - 1) * page_size
    result = await session.execute(query.offset(offset).limit(page_size))
    papers = result.scalars().all()

    return {
        "papers": [_paper_summary(p) for p in papers],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/cancer/{cancer_type_id}")
async def get_cancer_literature(
    cancer_type_id: int,
    mention_type: str | None = Query(None),
    sort_by: str = Query("pub_date", regex="^(pub_date|title)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
):
    """All papers studying a cancer type."""
    query = (
        select(Literature)
        .join(LiteratureCancer, LiteratureCancer.literature_id == Literature.id)
        .where(LiteratureCancer.cancer_type_id == cancer_type_id)
    )
    if mention_type:
        query = query.where(LiteratureCancer.mention_type == mention_type)

    if sort_by == "pub_date":
        query = query.order_by(desc(Literature.pub_date))
    else:
        query = query.order_by(Literature.title)

    count_q = (
        select(func.count(Literature.id))
        .join(LiteratureCancer, LiteratureCancer.literature_id == Literature.id)
        .where(LiteratureCancer.cancer_type_id == cancer_type_id)
    )
    if mention_type:
        count_q = count_q.where(LiteratureCancer.mention_type == mention_type)
    total = (await session.execute(count_q)).scalar() or 0

    offset = (page - 1) * page_size
    result = await session.execute(query.offset(offset).limit(page_size))
    papers = result.scalars().all()

    return {
        "papers": [_paper_summary(p) for p in papers],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ------------------------------------------------------------------
# Evidence
# ------------------------------------------------------------------


@router.get("/evidence/{drug_id}/{cancer_type_id}")
async def get_evidence(
    drug_id: int,
    cancer_type_id: int,
    session: AsyncSession = Depends(get_db),
):
    """Structured evidence summary connecting a drug to a cancer type."""
    evidence = await _analyzer.find_drug_cancer_evidence(
        session, drug_id, cancer_type_id
    )
    return evidence


# ------------------------------------------------------------------
# Paper detail & analysis
# ------------------------------------------------------------------


@router.get("/{literature_id}")
async def get_paper_detail(
    literature_id: int,
    session: AsyncSession = Depends(get_db),
):
    """Full paper detail including linked drugs, targets, cancers."""
    result = await session.execute(
        select(Literature).where(Literature.id == literature_id)
    )
    paper = result.scalar_one_or_none()
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")

    # Linked drugs
    drug_links = await session.execute(
        select(LiteratureDrug.drug_id, LiteratureDrug.mention_type).where(
            LiteratureDrug.literature_id == literature_id
        )
    )
    # Linked targets
    target_links = await session.execute(
        select(LiteratureTarget.target_id, LiteratureTarget.mention_type).where(
            LiteratureTarget.literature_id == literature_id
        )
    )
    # Linked cancers
    cancer_links = await session.execute(
        select(LiteratureCancer.cancer_type_id, LiteratureCancer.mention_type).where(
            LiteratureCancer.literature_id == literature_id
        )
    )

    return {
        "id": paper.id,
        "pmid": paper.pmid,
        "title": paper.title,
        "abstract": paper.abstract,
        "authors": paper.authors,
        "journal": paper.journal,
        "pub_date": paper.pub_date.isoformat() if paper.pub_date else None,
        "doi": paper.doi,
        "mesh_terms": paper.mesh_terms,
        "extracted_findings": paper.extracted_findings,
        "analysis_status": paper.analysis_status,
        "linked_drugs": [
            {"drug_id": r[0], "mention_type": r[1]} for r in drug_links.all()
        ],
        "linked_targets": [
            {"target_id": r[0], "mention_type": r[1]} for r in target_links.all()
        ],
        "linked_cancers": [
            {"cancer_type_id": r[0], "mention_type": r[1]}
            for r in cancer_links.all()
        ],
    }


@router.get("/{literature_id}/analysis")
async def get_paper_analysis(
    literature_id: int,
    session: AsyncSession = Depends(get_db),
):
    """Claude-extracted findings for a paper."""
    result = await session.execute(
        select(
            Literature.extracted_findings, Literature.analysis_status
        ).where(Literature.id == literature_id)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Paper not found")

    if row[1] != "analyzed":
        raise HTTPException(
            status_code=404,
            detail=f"Analysis not yet run (status: {row[1]})",
        )

    return {"literature_id": literature_id, "findings": row[0]}


@router.post("/{literature_id}/analyze")
async def trigger_paper_analysis(
    literature_id: int,
    session: AsyncSession = Depends(get_db),
):
    """Trigger Claude analysis for a single paper on demand."""
    findings = await _analyzer.analyze_paper(session, literature_id)
    if findings is None:
        raise HTTPException(
            status_code=404, detail="Paper not found or has no abstract"
        )
    return {"literature_id": literature_id, "findings": findings}


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _paper_summary(paper: Literature) -> dict:
    return {
        "id": paper.id,
        "pmid": paper.pmid,
        "title": paper.title,
        "journal": paper.journal,
        "pub_date": paper.pub_date.isoformat() if paper.pub_date else None,
        "doi": paper.doi,
        "analysis_status": paper.analysis_status,
        "abstract_snippet": (paper.abstract or "")[:300],
    }
