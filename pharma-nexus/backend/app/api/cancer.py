"""Cancer genomics API routes.

Endpoints:
  GET /api/cancer-types          — List all cancer types with summary stats
  GET /api/cancer-types/{id}     — Full detail for one cancer type
  GET /api/cancer-types/{id}/molecular-profile — Molecular profile (filterable)
  GET /api/cancer-types/{id}/mutations         — Mutation data
  GET /api/genes/{symbol}/cancer-landscape     — Gene alteration across all cancer types
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.cancer_type import CancerMolecularProfile, CancerType
from app.models.mutation import Mutation

router = APIRouter()


# ------------------------------------------------------------------
# Cancer Types
# ------------------------------------------------------------------


@router.get("/cancer-types")
async def list_cancer_types(
    search: str | None = Query(None, description="Search by name, tcga_code, or tissue"),
    tissue: str | None = Query(None, description="Filter by tissue type"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(50, ge=1, le=200, description="Items per page"),
    db: AsyncSession = Depends(get_db),
):
    """Paginated list of cancer types with summary statistics."""
    query = select(CancerType)
    count_query = select(func.count(CancerType.id))

    if search:
        search_filter = or_(
            CancerType.name.ilike(f"%{search}%"),
            CancerType.tcga_code.ilike(f"%{search}%"),
            CancerType.tissue.ilike(f"%{search}%"),
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    if tissue:
        query = query.where(CancerType.tissue.ilike(f"%{tissue}%"))
        count_query = count_query.where(CancerType.tissue.ilike(f"%{tissue}%"))

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (page - 1) * per_page
    query = query.order_by(CancerType.name).offset(offset).limit(per_page)
    result = await db.execute(query)
    cancer_types = result.scalars().all()

    # Batch-fetch summary stats for all cancer types on this page (avoids N+1)
    ct_ids = [ct.id for ct in cancer_types]

    mut_map: dict[int, int] = {}
    profile_map: dict[int, int] = {}
    driver_map: dict[int, int] = {}

    if ct_ids:
        mut_result = await db.execute(
            select(Mutation.cancer_type_id, func.count(Mutation.id))
            .where(Mutation.cancer_type_id.in_(ct_ids))
            .group_by(Mutation.cancer_type_id)
        )
        mut_map = {row[0]: row[1] for row in mut_result.all()}

        profile_result = await db.execute(
            select(CancerMolecularProfile.cancer_type_id, func.count(CancerMolecularProfile.id))
            .where(CancerMolecularProfile.cancer_type_id.in_(ct_ids))
            .group_by(CancerMolecularProfile.cancer_type_id)
        )
        profile_map = {row[0]: row[1] for row in profile_result.all()}

        driver_result = await db.execute(
            select(CancerMolecularProfile.cancer_type_id, func.count(CancerMolecularProfile.id))
            .where(
                CancerMolecularProfile.cancer_type_id.in_(ct_ids),
                CancerMolecularProfile.alteration_type == "driver_mutation",
            )
            .group_by(CancerMolecularProfile.cancer_type_id)
        )
        driver_map = {row[0]: row[1] for row in driver_result.all()}

    items = []
    for ct in cancer_types:
        items.append({
            "id": ct.id,
            "tcga_code": ct.tcga_code,
            "name": ct.name,
            "tissue": ct.tissue,
            "organ": ct.organ,
            "subtype": ct.subtype,
            "sample_count": ct.sample_count,
            "mutation_count": mut_map.get(ct.id, 0),
            "molecular_profile_count": profile_map.get(ct.id, 0),
            "driver_gene_count": driver_map.get(ct.id, 0),
        })

    return {
        "cancer_types": items,
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/cancer-types/{cancer_type_id}")
async def get_cancer_type(
    cancer_type_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Full detail for a cancer type including top mutations and molecular profiles."""
    result = await db.execute(
        select(CancerType).where(CancerType.id == cancer_type_id)
    )
    ct = result.scalar_one_or_none()
    if ct is None:
        raise HTTPException(status_code=404, detail="Cancer type not found")

    # Top 20 mutations by frequency
    mut_result = await db.execute(
        select(Mutation)
        .where(Mutation.cancer_type_id == cancer_type_id)
        .order_by(Mutation.frequency_percent.desc().nulls_last())
        .limit(20)
    )
    top_mutations = mut_result.scalars().all()

    # Top 20 molecular profiles by frequency
    profile_result = await db.execute(
        select(CancerMolecularProfile)
        .where(CancerMolecularProfile.cancer_type_id == cancer_type_id)
        .order_by(CancerMolecularProfile.frequency_percent.desc().nulls_last())
        .limit(20)
    )
    top_profiles = profile_result.scalars().all()

    return {
        "id": ct.id,
        "tcga_code": ct.tcga_code,
        "name": ct.name,
        "tissue": ct.tissue,
        "organ": ct.organ,
        "subtype": ct.subtype,
        "sample_count": ct.sample_count,
        "description": ct.description,
        "top_mutations": [
            {
                "id": m.id,
                "gene_symbol": m.gene_symbol,
                "mutation_type": m.mutation_type,
                "protein_change": m.protein_change,
                "genomic_position": m.genomic_position,
                "frequency_percent": m.frequency_percent,
                "functional_impact": m.functional_impact,
                "cosmic_id": m.cosmic_id,
                "source": m.source,
            }
            for m in top_mutations
        ],
        "top_molecular_profiles": [
            {
                "id": p.id,
                "gene_symbol": p.gene_symbol,
                "alteration_type": p.alteration_type,
                "frequency_percent": p.frequency_percent,
                "median_expression": p.median_expression,
                "expression_zscore": p.expression_zscore,
                "source": p.source,
            }
            for p in top_profiles
        ],
    }


# ------------------------------------------------------------------
# Molecular Profiles
# ------------------------------------------------------------------


@router.get("/cancer-types/{cancer_type_id}/molecular-profile")
async def get_molecular_profile(
    cancer_type_id: int,
    alteration_type: str | None = Query(
        None, description="Filter: expression, amplification, deletion, driver_mutation"
    ),
    gene_symbol: str | None = Query(None, description="Filter by gene symbol"),
    min_frequency: float | None = Query(None, ge=0, le=100, description="Min frequency %"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Molecular profile data for a cancer type (filterable)."""
    # Verify cancer type exists
    ct_result = await db.execute(
        select(CancerType.id).where(CancerType.id == cancer_type_id)
    )
    if ct_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Cancer type not found")

    query = select(CancerMolecularProfile).where(
        CancerMolecularProfile.cancer_type_id == cancer_type_id
    )
    count_query = select(func.count(CancerMolecularProfile.id)).where(
        CancerMolecularProfile.cancer_type_id == cancer_type_id
    )

    if alteration_type:
        query = query.where(CancerMolecularProfile.alteration_type == alteration_type)
        count_query = count_query.where(
            CancerMolecularProfile.alteration_type == alteration_type
        )

    if gene_symbol:
        query = query.where(
            CancerMolecularProfile.gene_symbol.ilike(f"%{gene_symbol}%")
        )
        count_query = count_query.where(
            CancerMolecularProfile.gene_symbol.ilike(f"%{gene_symbol}%")
        )

    if min_frequency is not None:
        query = query.where(
            CancerMolecularProfile.frequency_percent >= min_frequency
        )
        count_query = count_query.where(
            CancerMolecularProfile.frequency_percent >= min_frequency
        )

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (page - 1) * per_page
    query = (
        query.order_by(CancerMolecularProfile.frequency_percent.desc().nulls_last())
        .offset(offset)
        .limit(per_page)
    )
    result = await db.execute(query)
    profiles = result.scalars().all()

    return {
        "cancer_type_id": cancer_type_id,
        "molecular_profiles": [
            {
                "id": p.id,
                "gene_symbol": p.gene_symbol,
                "alteration_type": p.alteration_type,
                "frequency_percent": p.frequency_percent,
                "median_expression": p.median_expression,
                "expression_zscore": p.expression_zscore,
                "source": p.source,
            }
            for p in profiles
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


# ------------------------------------------------------------------
# Mutations
# ------------------------------------------------------------------


@router.get("/cancer-types/{cancer_type_id}/mutations")
async def get_cancer_type_mutations(
    cancer_type_id: int,
    gene_symbol: str | None = Query(None, description="Filter by gene symbol"),
    mutation_type: str | None = Query(None, description="Filter by mutation type"),
    functional_impact: str | None = Query(None, description="Filter: high, medium, low, unknown"),
    min_frequency: float | None = Query(None, ge=0, le=100, description="Min frequency %"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Mutation data for a cancer type with filtering."""
    # Verify cancer type exists
    ct_result = await db.execute(
        select(CancerType.id).where(CancerType.id == cancer_type_id)
    )
    if ct_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Cancer type not found")

    query = select(Mutation).where(Mutation.cancer_type_id == cancer_type_id)
    count_query = select(func.count(Mutation.id)).where(
        Mutation.cancer_type_id == cancer_type_id
    )

    if gene_symbol:
        query = query.where(Mutation.gene_symbol.ilike(f"%{gene_symbol}%"))
        count_query = count_query.where(Mutation.gene_symbol.ilike(f"%{gene_symbol}%"))

    if mutation_type:
        query = query.where(Mutation.mutation_type == mutation_type)
        count_query = count_query.where(Mutation.mutation_type == mutation_type)

    if functional_impact:
        query = query.where(Mutation.functional_impact == functional_impact)
        count_query = count_query.where(Mutation.functional_impact == functional_impact)

    if min_frequency is not None:
        query = query.where(Mutation.frequency_percent >= min_frequency)
        count_query = count_query.where(Mutation.frequency_percent >= min_frequency)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (page - 1) * per_page
    query = (
        query.order_by(Mutation.frequency_percent.desc().nulls_last())
        .offset(offset)
        .limit(per_page)
    )
    result = await db.execute(query)
    mutations = result.scalars().all()

    return {
        "cancer_type_id": cancer_type_id,
        "mutations": [
            {
                "id": m.id,
                "gene_symbol": m.gene_symbol,
                "mutation_type": m.mutation_type,
                "protein_change": m.protein_change,
                "genomic_position": m.genomic_position,
                "frequency_percent": m.frequency_percent,
                "functional_impact": m.functional_impact,
                "cosmic_id": m.cosmic_id,
                "source": m.source,
            }
            for m in mutations
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


# ------------------------------------------------------------------
# Gene Cancer Landscape (CRITICAL endpoint)
# ------------------------------------------------------------------


@router.get("/genes/{gene_symbol}/cancer-landscape")
async def get_gene_cancer_landscape(
    gene_symbol: str,
    db: AsyncSession = Depends(get_db),
):
    """Gene alteration frequency across all cancer types.

    This is a CRITICAL endpoint for the hypothesis engine — it shows how
    frequently a gene is altered across all 33+ TCGA cancer types, enabling
    cross-cancer-type drug repurposing analysis.

    Returns alteration data (mutations, expression, CNA, driver status)
    for a gene across all cancer types, sorted by mutation frequency.
    """
    gene_upper = gene_symbol.upper()

    # Get mutations across all cancer types
    mut_result = await db.execute(
        select(
            Mutation.cancer_type_id,
            Mutation.frequency_percent,
            Mutation.mutation_type,
            Mutation.functional_impact,
            CancerType.tcga_code,
            CancerType.name,
            CancerType.sample_count,
        )
        .join(CancerType, Mutation.cancer_type_id == CancerType.id)
        .where(Mutation.gene_symbol == gene_upper)
        .order_by(Mutation.frequency_percent.desc().nulls_last())
    )
    mutations = mut_result.all()

    # Get molecular profiles across all cancer types
    profile_result = await db.execute(
        select(
            CancerMolecularProfile.cancer_type_id,
            CancerMolecularProfile.alteration_type,
            CancerMolecularProfile.frequency_percent,
            CancerMolecularProfile.median_expression,
            CancerMolecularProfile.expression_zscore,
            CancerMolecularProfile.source,
            CancerType.tcga_code,
            CancerType.name,
        )
        .join(CancerType, CancerMolecularProfile.cancer_type_id == CancerType.id)
        .where(CancerMolecularProfile.gene_symbol == gene_upper)
    )
    profiles = profile_result.all()

    # Build landscape: merge mutation + profile data per cancer type
    landscape: dict[int, dict] = {}

    for row in mutations:
        ct_id = row[0]
        if ct_id not in landscape:
            landscape[ct_id] = {
                "cancer_type_id": ct_id,
                "tcga_code": row[4],
                "cancer_type_name": row[5],
                "sample_count": row[6],
                "mutation_frequency_percent": None,
                "mutation_type": None,
                "functional_impact": None,
                "expression_zscore": None,
                "median_expression": None,
                "alteration_types": [],
                "is_driver": False,
            }
        landscape[ct_id]["mutation_frequency_percent"] = row[1]
        landscape[ct_id]["mutation_type"] = row[2]
        landscape[ct_id]["functional_impact"] = row[3]

    for row in profiles:
        ct_id = row[0]
        if ct_id not in landscape:
            landscape[ct_id] = {
                "cancer_type_id": ct_id,
                "tcga_code": row[6],
                "cancer_type_name": row[7],
                "sample_count": None,
                "mutation_frequency_percent": None,
                "mutation_type": None,
                "functional_impact": None,
                "expression_zscore": None,
                "median_expression": None,
                "alteration_types": [],
                "is_driver": False,
            }

        alt_type = row[1]
        landscape[ct_id]["alteration_types"].append(alt_type)

        if alt_type == "expression":
            landscape[ct_id]["median_expression"] = row[3]
            landscape[ct_id]["expression_zscore"] = row[4]
        elif alt_type == "driver_mutation":
            landscape[ct_id]["is_driver"] = True

    # Sort by mutation frequency descending
    sorted_landscape = sorted(
        landscape.values(),
        key=lambda x: x.get("mutation_frequency_percent") or 0,
        reverse=True,
    )

    # Summary statistics
    total_cancer_types = len(sorted_landscape)
    cancer_types_with_mutations = sum(
        1 for x in sorted_landscape if x.get("mutation_frequency_percent") and x["mutation_frequency_percent"] > 0
    )
    is_driver_in_any = any(x.get("is_driver") for x in sorted_landscape)

    return {
        "gene_symbol": gene_upper,
        "summary": {
            "total_cancer_types": total_cancer_types,
            "cancer_types_with_mutations": cancer_types_with_mutations,
            "is_cancer_driver": is_driver_in_any,
            "max_mutation_frequency": max(
                (x.get("mutation_frequency_percent") or 0 for x in sorted_landscape),
                default=0,
            ),
        },
        "landscape": sorted_landscape,
    }
