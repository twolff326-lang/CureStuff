from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.drug import Drug, DrugTarget
from app.models.evidence import Bioassay
from app.models.target import Target

router = APIRouter()


@router.get("/")
async def list_drugs(
    search: str | None = Query(None, description="Search name, generic_name, or drugbank_id"),
    status: str | None = Query(None, description="Filter by status (approved/experimental/withdrawn)"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(50, ge=1, le=200, description="Items per page"),
    db: AsyncSession = Depends(get_db),
):
    """Paginated, searchable list of ingested drugs."""
    query = select(Drug)
    count_query = select(func.count(Drug.id))

    if search:
        search_filter = or_(
            Drug.name.ilike(f"%{search}%"),
            Drug.generic_name.ilike(f"%{search}%"),
            Drug.drugbank_id.ilike(f"%{search}%"),
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    if status:
        query = query.where(Drug.status == status)
        count_query = count_query.where(Drug.status == status)

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    offset = (page - 1) * per_page
    query = query.order_by(Drug.name).offset(offset).limit(per_page)
    result = await db.execute(query)
    drugs = result.scalars().all()

    return {
        "drugs": [
            {
                "id": d.id,
                "drugbank_id": d.drugbank_id,
                "name": d.name,
                "generic_name": d.generic_name,
                "status": d.status,
                "indication": d.indication,
                "mechanism_of_action": (
                    d.mechanism_of_action[:200] + "..."
                    if d.mechanism_of_action and len(d.mechanism_of_action) > 200
                    else d.mechanism_of_action
                ),
                "molecular_formula": d.molecular_formula,
                "inchi_key": d.inchi_key,
            }
            for d in drugs
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/{drug_id}")
async def get_drug(drug_id: int, db: AsyncSession = Depends(get_db)):
    """Get full drug detail including all targets with action types and binding affinities."""
    result = await db.execute(
        select(Drug)
        .options(selectinload(Drug.drug_targets).selectinload(DrugTarget.target))
        .where(Drug.id == drug_id)
    )
    drug = result.scalar_one_or_none()

    if drug is None:
        raise HTTPException(status_code=404, detail="Drug not found")

    targets = []
    for dt in drug.drug_targets:
        target = dt.target
        targets.append({
            "id": target.id if target else None,
            "uniprot_id": target.uniprot_id if target else None,
            "gene_symbol": target.gene_symbol if target else None,
            "gene_name": target.gene_name if target else None,
            "action_type": dt.action_type,
            "known_action": dt.known_action,
            "binding_affinity_nm": dt.binding_affinity_nm,
            "source": dt.source,
        })

    return {
        "id": drug.id,
        "drugbank_id": drug.drugbank_id,
        "name": drug.name,
        "generic_name": drug.generic_name,
        "description": drug.description,
        "mechanism_of_action": drug.mechanism_of_action,
        "pharmacodynamics": drug.pharmacodynamics,
        "indication": drug.indication,
        "status": drug.status,
        "molecular_formula": drug.molecular_formula,
        "smiles": drug.smiles,
        "inchi_key": drug.inchi_key,
        "cas_number": drug.cas_number,
        "categories": drug.categories,
        "targets": targets,
        "created_at": drug.created_at.isoformat() if drug.created_at else None,
        "updated_at": drug.updated_at.isoformat() if drug.updated_at else None,
    }


@router.get("/{drug_id}/targets")
async def get_drug_targets(drug_id: int, db: AsyncSession = Depends(get_db)):
    """Get targets for a drug with binding data."""
    # Verify drug exists
    drug_result = await db.execute(select(Drug.id).where(Drug.id == drug_id))
    if drug_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Drug not found")

    result = await db.execute(
        select(DrugTarget, Target)
        .join(Target, DrugTarget.target_id == Target.id)
        .where(DrugTarget.drug_id == drug_id)
        .order_by(DrugTarget.binding_affinity_nm.asc().nullslast())
    )
    rows = result.all()

    return {
        "drug_id": drug_id,
        "targets": [
            {
                "target_id": target.id,
                "uniprot_id": target.uniprot_id,
                "gene_symbol": target.gene_symbol,
                "gene_name": target.gene_name,
                "protein_class": target.protein_class,
                "action_type": dt.action_type,
                "known_action": dt.known_action,
                "binding_affinity_nm": dt.binding_affinity_nm,
                "source": dt.source,
                "references": dt.references,
            }
            for dt, target in rows
        ],
        "total": len(rows),
    }


@router.get("/{drug_id}/bioassays")
async def get_drug_bioassays(
    drug_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Bioassay results for a drug."""
    # Verify drug exists
    drug_result = await db.execute(select(Drug.id).where(Drug.id == drug_id))
    if drug_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Drug not found")

    count_result = await db.execute(
        select(func.count(Bioassay.id)).where(Bioassay.drug_id == drug_id)
    )
    total = count_result.scalar()

    offset = (page - 1) * per_page
    result = await db.execute(
        select(Bioassay, Target)
        .outerjoin(Target, Bioassay.target_id == Target.id)
        .where(Bioassay.drug_id == drug_id)
        .order_by(Bioassay.activity_value.asc().nullslast())
        .offset(offset)
        .limit(per_page)
    )
    rows = result.all()

    return {
        "drug_id": drug_id,
        "bioassays": [
            {
                "id": assay.id,
                "pubchem_aid": assay.pubchem_aid,
                "target_gene_symbol": target.gene_symbol if target else None,
                "target_uniprot_id": target.uniprot_id if target else None,
                "activity_type": assay.activity_type,
                "activity_value": assay.activity_value,
                "activity_unit": assay.activity_unit,
                "activity_outcome": assay.activity_outcome,
                "source": assay.source,
            }
            for assay, target in rows
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }
