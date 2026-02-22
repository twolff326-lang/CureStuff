"""Clinical trials API routes — browse, search, and detail endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.clinical_trial import ClinicalTrial
from app.models.drug import TrialDrug

router = APIRouter(prefix="/api/clinical-trials", tags=["clinical_trials"])


@router.get("/drug/{drug_id}")
async def get_drug_trials(
    drug_id: int,
    status: str | None = Query(None, description="Filter by trial status"),
    phase: str | None = Query(None, description="Filter by trial phase"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
):
    """All clinical trials for a drug."""
    query = (
        select(ClinicalTrial)
        .join(TrialDrug, TrialDrug.trial_id == ClinicalTrial.id)
        .where(TrialDrug.drug_id == drug_id)
    )

    count_q = (
        select(func.count(ClinicalTrial.id))
        .join(TrialDrug, TrialDrug.trial_id == ClinicalTrial.id)
        .where(TrialDrug.drug_id == drug_id)
    )

    if status:
        query = query.where(ClinicalTrial.status == status)
        count_q = count_q.where(ClinicalTrial.status == status)
    if phase:
        query = query.where(ClinicalTrial.phase == phase)
        count_q = count_q.where(ClinicalTrial.phase == phase)

    total = (await session.execute(count_q)).scalar() or 0
    offset = (page - 1) * page_size
    result = await session.execute(
        query.order_by(desc(ClinicalTrial.start_date)).offset(offset).limit(page_size)
    )
    trials = result.scalars().all()

    return {
        "trials": [_trial_summary(t) for t in trials],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/cancer/{cancer_type_id}")
async def get_cancer_trials(
    cancer_type_id: int,
    status: str | None = Query(None),
    phase: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
):
    """All clinical trials for a cancer type (via TCGA condition mapping)."""
    from app.models.cancer_type import CancerType
    from app.services.ingestion.clinicaltrials import CONDITION_TO_TCGA

    # Get the TCGA code for this cancer type
    ct_result = await session.execute(
        select(CancerType.tcga_code, CancerType.name).where(
            CancerType.id == cancer_type_id
        )
    )
    ct_row = ct_result.first()
    if not ct_row:
        raise HTTPException(status_code=404, detail="Cancer type not found")

    tcga_code = ct_row[0]

    # Find condition names that map to this TCGA code
    matching_conditions = [
        cond for cond, code in CONDITION_TO_TCGA.items() if code == tcga_code
    ]
    if not matching_conditions:
        return {"trials": [], "total": 0, "page": page, "page_size": page_size}

    # Search trials with JSONB conditions array overlapping
    # Use a raw text query for JSONB array containment
    from sqlalchemy import text

    condition_filter = ClinicalTrial.conditions.cast(
        type_=func.text()
    ).ilike(f"%{ct_row[1]}%")

    query = select(ClinicalTrial).where(condition_filter)
    count_q = select(func.count(ClinicalTrial.id)).where(condition_filter)

    if status:
        query = query.where(ClinicalTrial.status == status)
        count_q = count_q.where(ClinicalTrial.status == status)
    if phase:
        query = query.where(ClinicalTrial.phase == phase)
        count_q = count_q.where(ClinicalTrial.phase == phase)

    total = (await session.execute(count_q)).scalar() or 0

    result = await session.execute(
        query.order_by(desc(ClinicalTrial.start_date))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    trials = result.scalars().all()

    return {
        "trials": [_trial_summary(t) for t in trials],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/search")
async def search_trials(
    drug: str | None = Query(None, description="Drug name substring"),
    cancer: str | None = Query(None, description="Cancer/condition substring"),
    status: str | None = Query(None),
    phase: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
):
    """Search clinical trials by drug name, condition, status, phase."""
    query = select(ClinicalTrial)
    count_q = select(func.count(ClinicalTrial.id))

    if drug:
        # Join to trial_drugs → drugs to match drug name
        from app.models.drug import Drug

        query = (
            query.join(TrialDrug, TrialDrug.trial_id == ClinicalTrial.id)
            .join(Drug, Drug.id == TrialDrug.drug_id)
            .where(Drug.name.ilike(f"%{drug}%"))
        )
        count_q = (
            count_q.join(TrialDrug, TrialDrug.trial_id == ClinicalTrial.id)
            .join(Drug, Drug.id == TrialDrug.drug_id)
            .where(Drug.name.ilike(f"%{drug}%"))
        )

    if cancer:
        query = query.where(
            ClinicalTrial.conditions.cast(type_=func.text()).ilike(f"%{cancer}%")
        )
        count_q = count_q.where(
            ClinicalTrial.conditions.cast(type_=func.text()).ilike(f"%{cancer}%")
        )

    if status:
        query = query.where(ClinicalTrial.status == status)
        count_q = count_q.where(ClinicalTrial.status == status)
    if phase:
        query = query.where(ClinicalTrial.phase == phase)
        count_q = count_q.where(ClinicalTrial.phase == phase)

    total = (await session.execute(count_q)).scalar() or 0
    offset = (page - 1) * page_size
    result = await session.execute(
        query.order_by(desc(ClinicalTrial.start_date)).offset(offset).limit(page_size)
    )
    trials = result.scalars().all()

    return {
        "trials": [_trial_summary(t) for t in trials],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{trial_id}")
async def get_trial_detail(
    trial_id: int,
    session: AsyncSession = Depends(get_db),
):
    """Full trial detail with linked drugs and conditions."""
    result = await session.execute(
        select(ClinicalTrial).where(ClinicalTrial.id == trial_id)
    )
    trial = result.scalar_one_or_none()
    if not trial:
        raise HTTPException(status_code=404, detail="Trial not found")

    # Linked drugs
    drug_links = await session.execute(
        select(TrialDrug.drug_id).where(TrialDrug.trial_id == trial_id)
    )

    return {
        "id": trial.id,
        "nct_id": trial.nct_id,
        "title": trial.title,
        "status": trial.status,
        "phase": trial.phase,
        "conditions": trial.conditions,
        "interventions": trial.interventions,
        "enrollment": trial.enrollment,
        "start_date": trial.start_date.isoformat() if trial.start_date else None,
        "completion_date": (
            trial.completion_date.isoformat() if trial.completion_date else None
        ),
        "results_summary": trial.results_summary,
        "source_url": trial.source_url,
        "linked_drug_ids": [r[0] for r in drug_links.all()],
    }


def _trial_summary(trial: ClinicalTrial) -> dict:
    return {
        "id": trial.id,
        "nct_id": trial.nct_id,
        "title": trial.title,
        "status": trial.status,
        "phase": trial.phase,
        "conditions": trial.conditions,
        "enrollment": trial.enrollment,
        "start_date": trial.start_date.isoformat() if trial.start_date else None,
        "source_url": trial.source_url,
    }
