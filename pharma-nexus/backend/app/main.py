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
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import settings
from app.api import (
    drugs, hypotheses, analysis, ingestion, knowledge_graph, export,
    cancer, pathways, literature, clinical_trials, llm_analysis,
    validation, combinations, tallula, gnn, literature_monitor,
    pipeline, publication,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("Pharma Nexus starting (env=%s)", settings.app_env)

    # Mark any ingestion logs stuck in "running" as failed.
    # This happens when the server crashes mid-ingestion — the logs never
    # get finalized, and the frontend hides the Start button for those
    # sources because it thinks they're still running.
    try:
        from app.database import async_session_factory
        from app.models.ingestion_log import IngestionLog
        from sqlalchemy import update
        from datetime import datetime, timezone

        async with async_session_factory() as session:
            result = await session.execute(
                update(IngestionLog)
                .where(IngestionLog.status == "running")
                .values(
                    status="failed",
                    errors=[{"context": "startup", "error": "Interrupted by server restart"}],
                    completed_at=datetime.now(timezone.utc),
                )
            )
            if result.rowcount:
                logger.warning(
                    "Marked %d stale 'running' ingestion logs as failed", result.rowcount
                )
            await session.commit()
    except Exception:
        logger.exception("Failed to clean up stale ingestion logs")

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

class RequestContextMiddleware:
    """Pure ASGI middleware — no BaseHTTPMiddleware, no exception re-raising."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Extract request-id from headers (ASGI headers are bytes pairs).
        raw_headers = dict(scope.get("headers", []))
        request_id = (
            raw_headers.get(b"x-request-id", b"").decode() or str(uuid.uuid4())[:8]
        )
        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: dict) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                headers.append((b"x-response-time", f"{elapsed_ms}ms".encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # If the inner stack truly raises (nothing handled it), send 500.
            elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
            response = JSONResponse(
                status_code=500,
                content={
                    "error": "internal_error",
                    "message": "Internal server error",
                    "request_id": request_id,
                },
                headers={
                    "X-Request-ID": request_id,
                    "X-Response-Time": f"{elapsed_ms}ms",
                },
            )
            await response(scope, receive, send)
            return

        path = scope.get("path", "")
        if path != "/health":
            elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
            method = scope.get("method", "")
            logger.info(
                "%s %s -> %s (%sms) [%s]",
                method,
                path,
                status_code,
                elapsed_ms,
                request_id,
            )


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
    # HTTPException should be handled by FastAPI's built-in handler, not here.
    if isinstance(exc, StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

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
app.include_router(
    gnn.router, prefix="/api/gnn", tags=["gnn"]
)
app.include_router(
    literature_monitor.router, prefix="/api/monitoring", tags=["monitoring"]
)
app.include_router(
    pipeline.router, prefix="/api/pipeline", tags=["pipeline"]
)
app.include_router(
    publication.router, prefix="/api/publication", tags=["publication"]
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
        try:
            r.ping()
            checks["checks"]["redis"] = "ok"
        finally:
            r.close()
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
