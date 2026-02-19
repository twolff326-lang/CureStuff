"""Pathway and protein interaction API routes.

Endpoints:
  GET /api/pathways                      — Paginated pathway list
  GET /api/pathways/{id}                 — Pathway detail with genes and targets
  GET /api/pathways/{id}/genes           — All genes in a pathway
  GET /api/genes/{symbol}/pathways       — All pathways for a gene (KEGG + Reactome)
  GET /api/targets/{id}/interactions     — Protein interaction partners (STRING)
  GET /api/targets/{id}/disease-associations — OpenTargets disease associations
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.pathway import Pathway, PathwayTarget
from app.models.target import ProteinInteraction, Target
from app.models.target_disease import TargetDiseaseAssociation

router = APIRouter()


# ------------------------------------------------------------------
# Pathways
# ------------------------------------------------------------------


@router.get("/pathways")
async def list_pathways(
    search: str | None = Query(None, description="Search by name or external_id"),
    source: str | None = Query(None, description="Filter by source: kegg, reactome"),
    category: str | None = Query(None, description="Filter by category"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Paginated, searchable list of pathways."""
    query = select(Pathway)
    count_query = select(func.count(Pathway.id))

    if search:
        search_filter = or_(
            Pathway.name.ilike(f"%{search}%"),
            Pathway.external_id.ilike(f"%{search}%"),
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    if source:
        query = query.where(Pathway.source == source.lower())
        count_query = count_query.where(Pathway.source == source.lower())

    if category:
        query = query.where(Pathway.category.ilike(f"%{category}%"))
        count_query = count_query.where(Pathway.category.ilike(f"%{category}%"))

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    offset = (page - 1) * per_page
    query = query.order_by(Pathway.name).offset(offset).limit(per_page)
    result = await db.execute(query)
    pathways = result.scalars().all()

    return {
        "pathways": [
            {
                "id": pw.id,
                "source": pw.source,
                "external_id": pw.external_id,
                "name": pw.name,
                "category": pw.category,
                "gene_count": len(pw.genes) if pw.genes else 0,
                "parent_pathway_id": pw.parent_pathway_id,
            }
            for pw in pathways
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/pathways/{pathway_id}")
async def get_pathway(
    pathway_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Pathway detail with member genes and linked targets."""
    result = await db.execute(
        select(Pathway)
        .options(selectinload(Pathway.pathway_targets).selectinload(PathwayTarget.target))
        .where(Pathway.id == pathway_id)
    )
    pathway = result.scalar_one_or_none()

    if pathway is None:
        raise HTTPException(status_code=404, detail="Pathway not found")

    targets = []
    for pt in pathway.pathway_targets:
        t = pt.target
        if t:
            targets.append({
                "target_id": t.id,
                "uniprot_id": t.uniprot_id,
                "gene_symbol": t.gene_symbol,
                "gene_name": t.gene_name,
                "protein_class": t.protein_class,
                "role": pt.role,
            })

    return {
        "id": pathway.id,
        "source": pathway.source,
        "external_id": pathway.external_id,
        "name": pathway.name,
        "description": pathway.description,
        "category": pathway.category,
        "genes": pathway.genes or [],
        "parent_pathway_id": pathway.parent_pathway_id,
        "linked_targets": targets,
        "created_at": pathway.created_at.isoformat() if pathway.created_at else None,
    }


@router.get("/pathways/{pathway_id}/genes")
async def get_pathway_genes(
    pathway_id: int,
    db: AsyncSession = Depends(get_db),
):
    """All genes in a pathway."""
    result = await db.execute(
        select(Pathway.genes, Pathway.name).where(Pathway.id == pathway_id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Pathway not found")

    genes = row[0] or []

    # Check which genes have targets in our database
    gene_details = []
    for gene_sym in genes:
        target_result = await db.execute(
            select(Target.id, Target.uniprot_id, Target.protein_class).where(
                Target.gene_symbol == gene_sym
            )
        )
        target_row = target_result.one_or_none()
        gene_details.append({
            "gene_symbol": gene_sym,
            "has_target": target_row is not None,
            "target_id": target_row[0] if target_row else None,
            "uniprot_id": target_row[1] if target_row else None,
            "protein_class": target_row[2] if target_row else None,
        })

    return {
        "pathway_id": pathway_id,
        "pathway_name": row[1],
        "genes": gene_details,
        "total": len(genes),
    }


# ------------------------------------------------------------------
# Targets (browseable list)
# ------------------------------------------------------------------


@router.get("/targets")
async def list_targets(
    search: str | None = Query(None, description="Search by gene_symbol, uniprot_id, or gene_name"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Paginated, searchable list of protein targets."""
    query = select(Target)
    count_query = select(func.count(Target.id))

    if search:
        search_filter = or_(
            Target.gene_symbol.ilike(f"%{search}%"),
            Target.uniprot_id.ilike(f"%{search}%"),
            Target.gene_name.ilike(f"%{search}%"),
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    offset = (page - 1) * per_page
    query = query.order_by(Target.gene_symbol).offset(offset).limit(per_page)
    result = await db.execute(query)
    targets = result.scalars().all()

    return {
        "targets": [
            {
                "id": t.id,
                "uniprot_id": t.uniprot_id,
                "gene_symbol": t.gene_symbol,
                "gene_name": t.gene_name,
                "organism": t.organism,
                "protein_class": t.protein_class,
                "ensembl_gene_id": t.ensembl_gene_id,
            }
            for t in targets
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


# ------------------------------------------------------------------
# Gene pathways (cross-database)
# ------------------------------------------------------------------


@router.get("/genes/{gene_symbol}/pathways")
async def get_gene_pathways(
    gene_symbol: str,
    db: AsyncSession = Depends(get_db),
):
    """All pathways a gene participates in (across KEGG + Reactome)."""
    from app.services.pathway_analyzer import PathwayAnalyzer

    analyzer = PathwayAnalyzer(db)
    pathways = await analyzer.get_gene_pathways(gene_symbol)

    return {
        "gene_symbol": gene_symbol.upper(),
        "pathways": pathways,
        "total": len(pathways),
        "sources": list({p["source"] for p in pathways}),
    }


# ------------------------------------------------------------------
# Protein interactions (STRING)
# ------------------------------------------------------------------


@router.get("/targets/{target_id}/interactions")
async def get_target_interactions(
    target_id: int,
    min_score: float = Query(0.4, ge=0, le=1, description="Min interaction score (0-1)"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Protein interaction partners from STRING with scores."""
    # Get target's UniProt ID
    target_result = await db.execute(
        select(Target.uniprot_id, Target.gene_symbol).where(Target.id == target_id)
    )
    target_row = target_result.one_or_none()
    if target_row is None:
        raise HTTPException(status_code=404, detail="Target not found")

    uniprot_id = target_row[0]
    gene_symbol = target_row[1]

    # Query interactions (check both columns since interactions are stored once)
    query = select(ProteinInteraction).where(
        or_(
            ProteinInteraction.protein_a_uniprot == uniprot_id,
            ProteinInteraction.protein_b_uniprot == uniprot_id,
        ),
        ProteinInteraction.interaction_score >= min_score,
    )

    count_query = select(func.count(ProteinInteraction.id)).where(
        or_(
            ProteinInteraction.protein_a_uniprot == uniprot_id,
            ProteinInteraction.protein_b_uniprot == uniprot_id,
        ),
        ProteinInteraction.interaction_score >= min_score,
    )

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    offset = (page - 1) * per_page
    query = (
        query.order_by(ProteinInteraction.interaction_score.desc())
        .offset(offset)
        .limit(per_page)
    )
    result = await db.execute(query)
    interactions = result.scalars().all()

    items = []
    for i in interactions:
        # Determine which is the partner
        partner_uniprot = (
            i.protein_b_uniprot if i.protein_a_uniprot == uniprot_id
            else i.protein_a_uniprot
        )
        items.append({
            "id": i.id,
            "partner_uniprot": partner_uniprot,
            "interaction_score": i.interaction_score,
            "experimental_score": i.experimental_score,
            "database_score": i.database_score,
            "textmining_score": i.textmining_score,
            "source": i.source,
        })

    return {
        "target_id": target_id,
        "gene_symbol": gene_symbol,
        "uniprot_id": uniprot_id,
        "interactions": items,
        "total": total,
        "page": page,
        "per_page": per_page,
    }


# ------------------------------------------------------------------
# Target-disease associations (OpenTargets)
# ------------------------------------------------------------------


@router.get("/targets/{target_id}/disease-associations")
async def get_target_disease_associations(
    target_id: int,
    min_score: float = Query(0.0, ge=0, le=1, description="Min overall score"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """OpenTargets disease associations for a target."""
    # Verify target exists
    target_result = await db.execute(
        select(Target.gene_symbol, Target.ensembl_gene_id).where(Target.id == target_id)
    )
    target_row = target_result.one_or_none()
    if target_row is None:
        raise HTTPException(status_code=404, detail="Target not found")

    query = select(TargetDiseaseAssociation).where(
        TargetDiseaseAssociation.target_id == target_id,
    )
    count_query = select(func.count(TargetDiseaseAssociation.id)).where(
        TargetDiseaseAssociation.target_id == target_id,
    )

    if min_score > 0:
        query = query.where(TargetDiseaseAssociation.overall_score >= min_score)
        count_query = count_query.where(
            TargetDiseaseAssociation.overall_score >= min_score
        )

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    offset = (page - 1) * per_page
    query = (
        query.order_by(TargetDiseaseAssociation.overall_score.desc())
        .offset(offset)
        .limit(per_page)
    )
    result = await db.execute(query)
    associations = result.scalars().all()

    return {
        "target_id": target_id,
        "gene_symbol": target_row[0],
        "ensembl_gene_id": target_row[1],
        "associations": [
            {
                "id": a.id,
                "disease_id": a.disease_id,
                "disease_name": a.disease_name,
                "overall_score": a.overall_score,
                "genetic_association_score": a.genetic_association_score,
                "somatic_mutation_score": a.somatic_mutation_score,
                "known_drug_score": a.known_drug_score,
                "literature_score": a.literature_score,
                "rna_expression_score": a.rna_expression_score,
                "animal_model_score": a.animal_model_score,
                "source": a.source,
            }
            for a in associations
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }
