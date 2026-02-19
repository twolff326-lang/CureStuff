import logging
import time
import uuid
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.api import (
    drugs, hypotheses, analysis, ingestion, knowledge_graph, export,
    cancer, pathways, literature, clinical_trials, llm_analysis,
    validation, combinations, tallula,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("Pharma Nexus starting (env=%s)", settings.app_env)
    yield
    from app.database import engine
    await engine.dispose()
    logger.info("Pharma Nexus shut down")


# ---------------------------------------------------------------------------
# App instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Pharma Nexus",
    description="AI-powered drug repurposing discovery engine",
    version="0.2.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Middleware: Request ID + timing
# ---------------------------------------------------------------------------

class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
        start = time.perf_counter()

        response = await call_next(request)

        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time"] = f"{elapsed_ms}ms"

        if request.url.path != "/health":
            logger.info(
                "%s %s -> %s (%sms) [%s]",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
                request_id,
            )

        return response


app.add_middleware(RequestContextMiddleware)


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
)


# ---------------------------------------------------------------------------
# Global error handlers
# ---------------------------------------------------------------------------

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = []
    for err in exc.errors():
        field = " -> ".join(str(loc) for loc in err.get("loc", []))
        errors.append({"field": field, "message": err.get("msg", "Invalid value")})
    return JSONResponse(
        status_code=422,
        content={
            "error": "validation_error",
            "message": "Request validation failed",
            "details": errors,
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    request_id = request.headers.get("X-Request-ID", "unknown")
    logger.exception("Unhandled exception on %s %s [%s]", request.method, request.url.path, request_id)

    detail = str(exc) if settings.app_debug else "Internal server error"

    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "message": detail,
            "request_id": request_id,
        },
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

app.include_router(drugs.router, prefix="/api/drugs", tags=["drugs"])
app.include_router(hypotheses.router, prefix="/api/hypotheses", tags=["hypotheses"])
app.include_router(analysis.router, prefix="/api/analysis", tags=["analysis"])
app.include_router(ingestion.router, prefix="/api/ingestion", tags=["ingestion"])
app.include_router(
    knowledge_graph.router, prefix="/api/graph", tags=["knowledge-graph"]
)
app.include_router(export.router, prefix="/api/export", tags=["export"])
app.include_router(cancer.router, prefix="/api", tags=["cancer"])
app.include_router(pathways.router, prefix="/api", tags=["pathways"])
app.include_router(literature.router, tags=["literature"])
app.include_router(clinical_trials.router, tags=["clinical_trials"])
app.include_router(
    llm_analysis.router, prefix="/api/llm-analysis", tags=["llm-analysis"]
)
app.include_router(
    validation.router, prefix="/api/validation", tags=["validation"]
)
app.include_router(
    combinations.router, prefix="/api/combinations", tags=["combinations"]
)
app.include_router(
    tallula.router, prefix="/api/tallula", tags=["tallula"]
)


# ---------------------------------------------------------------------------
# Health check with real dependency verification
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    from app.database import engine
    checks = {"service": "pharma-nexus", "status": "healthy", "checks": {}}

    # PostgreSQL
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["checks"]["postgres"] = "ok"
    except Exception as e:
        checks["checks"]["postgres"] = f"error: {e.__class__.__name__}"
        checks["status"] = "degraded"

    # Redis
    try:
        import redis as redis_lib
        r = redis_lib.from_url(settings.redis_url, socket_timeout=2)
        r.ping()
        checks["checks"]["redis"] = "ok"
    except Exception as e:
        checks["checks"]["redis"] = f"error: {e.__class__.__name__}"
        checks["status"] = "degraded"

    # Neo4j
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )
        driver.verify_connectivity()
        driver.close()
        checks["checks"]["neo4j"] = "ok"
    except Exception as e:
        checks["checks"]["neo4j"] = f"error: {e.__class__.__name__}"
        checks["status"] = "degraded"

    status_code = 200 if checks["status"] == "healthy" else 503
    return JSONResponse(content=checks, status_code=status_code)
