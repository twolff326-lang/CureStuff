import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text, update

from app.config import settings
from app.database import async_session, engine
from app.models.ingestion_log import IngestionLog
from app.api import drugs, cancer, hypotheses, ingestion

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Clean up stale "running" ingestion logs from previous crashes
    try:
        async with async_session() as db:
            await db.execute(
                update(IngestionLog)
                .where(IngestionLog.status == "running")
                .values(status="failed", error_message="Server restarted while running")
            )
            await db.commit()
            logger.info("Cleaned up stale ingestion logs")
    except Exception as e:
        logger.warning(f"Could not clean stale ingestion logs: {e}")
    yield


app = FastAPI(
    title="Pharma Nexus",
    description="Drug repurposing discovery platform",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
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
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "healthy", "version": "2.0.0"}
    except Exception as e:
        return {"status": "unhealthy", "version": "2.0.0", "error": str(e)}
