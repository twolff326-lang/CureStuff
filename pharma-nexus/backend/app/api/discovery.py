"""API endpoints for LLM synthesis discovery (Strategy 7).

Trigger discovery runs, view proposals, promote them into hypotheses,
run multi-round verification, retroactive validation, and ablation studies.
"""

from pydantic import BaseModel
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.tasks.celery_app import celery_app

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


class DiscoverRequest(BaseModel):
    cancer_type_id: int | None = None
    cancer_type_ids: list[int] | None = None
    limit: int = 10
    cost_mode: str | None = None


class PromoteRequest(BaseModel):
    min_confidence: float = 0.5
    limit: int = 50


class VerifyProposalRequest(BaseModel):
    """Verify a specific proposal through multi-round pipeline."""
    proposal_id: int
    cost_mode: str | None = None


@router.post("/run")
async def trigger_discovery(request: DiscoverRequest):
    """Trigger LLM synthesis discovery.

    This is the novel part — Claude reads papers about a cancer type
    alongside drug mechanisms and reasons about implicit connections.

    If cancer_type_id is provided, discovers for that cancer only.
    If cancer_type_ids is provided, discovers for those cancers.
    If neither, picks the top cancer types by paper count.
    """
    kwargs = {
        "cost_mode": request.cost_mode,
        "limit": request.limit,
    }
    if request.cancer_type_id:
        kwargs["cancer_type_ids"] = [request.cancer_type_id]
    elif request.cancer_type_ids:
        kwargs["cancer_type_ids"] = request.cancer_type_ids

    task = celery_app.send_task(
        "app.tasks.generate.run_synthesis_discovery",
        kwargs=kwargs,
    )
    return {
        "task_id": task.id,
        "status": "queued",
        "cost_mode": request.cost_mode or "from settings",
    }


@router.post("/promote")
async def trigger_promote(request: PromoteRequest | None = None):
    """Promote accepted proposals into the hypothesis scoring pipeline."""
    min_confidence = request.min_confidence if request else 0.5
    limit = request.limit if request else 50
    task = celery_app.send_task(
        "app.tasks.generate.promote_proposals",
        kwargs={"min_confidence": min_confidence, "limit": limit},
    )
    return {
        "task_id": task.id,
        "status": "queued",
        "min_confidence": min_confidence,
    }


@router.get("/proposals")
async def list_proposals(
    status: str | None = None,
    cancer_type_id: int | None = None,
    min_confidence: float = 0.0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """List discovery proposals with optional filters."""
    from app.services.synthesis_discovery import SynthesisDiscovery

    svc = SynthesisDiscovery()
    proposals = await svc.get_proposals(
        db,
        status=status,
        cancer_type_id=cancer_type_id,
        min_confidence=min_confidence,
        limit=limit,
    )
    return {"proposals": proposals, "total": len(proposals)}


# ------------------------------------------------------------------
# Verification endpoints
# ------------------------------------------------------------------


@router.post("/verify/{proposal_id}")
async def verify_proposal(
    proposal_id: int,
    cost_mode: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Run multi-round verification on a discovery proposal.

    Three rounds:
      1. Adversarial critique (second LLM call challenges the proposal)
      2. Citation grounding (verify PMIDs exist and support claims)
      3. Chain verification (check transitive links in database)

    Returns a verified_score that incorporates all three evidence sources.
    """
    from app.models.discovery import LLMDiscoveryProposal
    from app.models.drug import Drug
    from app.models.cancer_type import CancerType
    from app.services.discovery_verification import ProposalVerifier
    from app.services.synthesis_discovery import SynthesisDiscovery

    from sqlalchemy import select as sa_select

    # Get the proposal
    result = await db.execute(
        sa_select(LLMDiscoveryProposal).where(
            LLMDiscoveryProposal.id == proposal_id
        )
    )
    proposal_obj = result.scalar_one_or_none()
    if not proposal_obj:
        return {"error": f"Proposal {proposal_id} not found"}

    # Get drug name
    drug_result = await db.execute(
        sa_select(Drug.name).where(Drug.id == proposal_obj.drug_id)
    )
    drug_name = drug_result.scalar_one_or_none() or f"Drug#{proposal_obj.drug_id}"

    # Build proposal dict
    proposal_dict = {
        "drug_name": drug_name,
        "drug_id": proposal_obj.drug_id,
        "mechanism_rationale": proposal_obj.mechanism_rationale,
        "transitive_chain": proposal_obj.transitive_chain or [],
        "key_papers": proposal_obj.key_papers or [],
        "inferred_pathways": proposal_obj.inferred_pathways or [],
        "confidence": proposal_obj.confidence,
        "novelty_reasoning": proposal_obj.novelty_reasoning,
    }

    # Get cancer context
    svc = SynthesisDiscovery(cost_mode=cost_mode)
    cancer_context = await svc._get_cancer_context(
        proposal_obj.cancer_type_id, db
    )

    # Run verification
    verifier = ProposalVerifier(cost_mode=cost_mode)
    result = await verifier.verify_proposal(
        proposal_dict,
        proposal_obj.cancer_type_id,
        cancer_context,
        db,
    )

    return result


# ------------------------------------------------------------------
# Retroactive validation endpoints
# ------------------------------------------------------------------


@router.post("/retroactive-validation")
async def run_retroactive_validation(
    db: AsyncSession = Depends(get_db),
):
    """Run the 'time machine' test against all 23 ground truth cases.

    For each known drug repurposing success:
      1. Filters evidence to before the discovery date
      2. Checks whether structured data would have connected drug to cancer
      3. Computes a retroactive predictability score

    This is the gold standard for evaluating drug repurposing methods.
    """
    from app.services.retroactive_validation import RetroactiveValidator

    validator = RetroactiveValidator()
    results = await validator.run_full_evaluation(db)
    return results


# ------------------------------------------------------------------
# Ablation study endpoints
# ------------------------------------------------------------------


@router.post("/ablation/{cancer_type_id}")
async def run_ablation_single(
    cancer_type_id: int,
    top_k: int = Query(20, description="Top K proposals per method"),
    db: AsyncSession = Depends(get_db),
):
    """Run ablation study for a single cancer type.

    Compares four methods head-to-head:
      1. Keyword co-occurrence (simplest baseline)
      2. Structured strategies 1-6 (database joins only)
      3. Random selection (floor)
      4. LLM synthesis — Strategy 7 (the method under evaluation)

    For each method, measures overlap with ground truth repurposing
    cases, unique contributions, and recall.
    """
    from app.services.ablation_study import AblationStudy

    study = AblationStudy()
    return await study.run_ablation(cancer_type_id, db, top_k)


@router.post("/ablation")
async def run_ablation_full(
    top_k: int = Query(20, description="Top K proposals per method"),
    db: AsyncSession = Depends(get_db),
):
    """Run ablation study across all cancer types with ground truth data.

    Aggregates results to show the overall contribution of each method.
    """
    from app.services.ablation_study import AblationStudy

    study = AblationStudy()
    return await study.run_full_ablation(db, top_k)
