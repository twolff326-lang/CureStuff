from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api import drugs, cancer, hypotheses, ingestion


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="Pharma Nexus",
    description="Drug repurposing discovery platform",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(drugs.router, prefix="/api/drugs", tags=["drugs"])
app.include_router(cancer.router, prefix="/api/cancer-types", tags=["cancer"])
app.include_router(hypotheses.router, prefix="/api/hypotheses", tags=["hypotheses"])
app.include_router(ingestion.router, prefix="/api/ingestion", tags=["ingestion"])


@app.get("/health")
async def health_check():
    return {"status": "healthy", "version": "2.0.0"}
