from fastapi import APIRouter

router = APIRouter()


@router.get("/hypothesis/{hypothesis_id}/report")
async def export_hypothesis_report(hypothesis_id: int, format: str = "json"):
    """Export a hypothesis report in the specified format."""
    # Implementation in future prompt
    return {"hypothesis_id": hypothesis_id, "format": format, "status": "not_implemented"}
