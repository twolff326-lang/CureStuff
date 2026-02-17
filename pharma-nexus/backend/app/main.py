from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api import (
    drugs, hypotheses, analysis, ingestion, knowledge_graph, export,
    cancer, pathways, literature, clinical_trials,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup
    yield
    # Shutdown
    from app.database import engine

    await engine.dispose()


app = FastAPI(
    title="Pharma Nexus",
    description="AI-powered drug repurposing discovery engine",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers
app.include_router(drugs.router, prefix="/api/drugs", tags=["drugs"])
app.include_router(hypotheses.router, prefix="/api/hypotheses", tags=["hypotheses"])
app.include_router(analysis.router, prefix="/api/analysis", tags=["analysis"])
app.include_router(ingestion.router, prefix="/api/ingestion", tags=["ingestion"])
app.include_router(
    knowledge_graph.router, prefix="/api/knowledge-graph", tags=["knowledge-graph"]
)
app.include_router(export.router, prefix="/api/export", tags=["export"])
app.include_router(cancer.router, prefix="/api", tags=["cancer"])
app.include_router(pathways.router, prefix="/api", tags=["pathways"])
app.include_router(literature.router, tags=["literature"])
app.include_router(clinical_trials.router, tags=["clinical_trials"])


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "pharma-nexus"}
