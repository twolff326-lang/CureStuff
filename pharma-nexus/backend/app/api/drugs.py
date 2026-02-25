from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.drug import Drug
from app.models.drug_target import DrugTarget

router = APIRouter()


@router.get("")
async def list_drugs(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: str = Query(None),
    status: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    query = select(Drug)
    count_query = select(func.count(Drug.id))

    if search:
        query = query.where(Drug.name.ilike(f"%{search}%"))
        count_query = count_query.where(Drug.name.ilike(f"%{search}%"))
    if status:
        query = query.where(Drug.status == status)
        count_query = count_query.where(Drug.status == status)

    total = (await db.execute(count_query)).scalar()
    result = await db.execute(
        query.order_by(Drug.name).offset((page - 1) * per_page).limit(per_page)
    )
    drugs = result.scalars().all()

    return {
        "items": [
            {
                "id": d.id,
                "name": d.name,
                "generic_name": d.generic_name,
                "pubchem_cid": d.pubchem_cid,
                "chembl_id": d.chembl_id,
                "smiles": d.smiles,
                "molecular_formula": d.molecular_formula,
                "molecular_weight": d.molecular_weight,
                "mechanism_of_action": d.mechanism_of_action,
                "status": d.status,
            }
            for d in drugs
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/count")
async def drug_count(db: AsyncSession = Depends(get_db)):
    total = (await db.execute(select(func.count(Drug.id)))).scalar()
    return {"count": total}


@router.get("/{drug_id}")
async def get_drug(drug_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Drug)
        .options(selectinload(Drug.targets).selectinload(DrugTarget.target))
        .where(Drug.id == drug_id)
    )
    drug = result.scalar_one_or_none()
    if not drug:
        raise HTTPException(status_code=404, detail="Drug not found")

    return {
        "id": drug.id,
        "name": drug.name,
        "generic_name": drug.generic_name,
        "pubchem_cid": drug.pubchem_cid,
        "chembl_id": drug.chembl_id,
        "smiles": drug.smiles,
        "molecular_formula": drug.molecular_formula,
        "molecular_weight": drug.molecular_weight,
        "mechanism_of_action": drug.mechanism_of_action,
        "status": drug.status,
        "targets": [
            {
                "gene_symbol": dt.target.gene_symbol,
                "action_type": dt.action_type,
                "binding_affinity": dt.binding_affinity,
                "affinity_type": dt.affinity_type,
                "source": dt.source,
            }
            for dt in drug.targets
        ],
    }
