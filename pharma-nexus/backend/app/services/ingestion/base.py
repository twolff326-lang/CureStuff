"""Base ingestion framework for all 13 biomedical data source connectors.

Provides: async HTTP with rate limiting, retry with exponential backoff,
batch upsert, progress tracking via ingestion_logs, and error aggregation.
"""

import asyncio
import logging
import time
import traceback
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models.ingestion_log import IngestionLog

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
DEFAULT_TIMEOUT = 30.0
DEFAULT_BATCH_SIZE = 500
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0


class RateLimiter:
    """Token-bucket rate limiter for API calls."""

    def __init__(self, requests_per_second: float):
        self._rate = requests_per_second
        self._min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0
        self._last_request_time: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_request_time = time.monotonic()


class BaseConnector(ABC):
    """Abstract base class for all data source connectors.

    Subclasses must implement:
        - get_source_name() -> str
        - fetch_data(session) -> list of raw records
        - transform_record(raw_record) -> dict matching the target table schema
    """

    def __init__(
        self,
        db_session: AsyncSession | None = None,
        http_client: httpx.AsyncClient | None = None,
        rate_limit: float = 2.0,
        batch_size: int = DEFAULT_BATCH_SIZE,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self._external_session = db_session
        self._external_client = http_client
        self._rate_limiter = RateLimiter(rate_limit)
        self._batch_size = batch_size
        self._timeout = timeout
        self._errors: list[dict[str, Any]] = []
        self._records_processed: int = 0
        self._total_expected: int | None = None
        self._log_id: int | None = None

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    def get_source_name(self) -> str:
        """Return a unique identifier for this data source (e.g. 'drugbank')."""

    @abstractmethod
    async def fetch_data(self, session: AsyncSession) -> list[dict[str, Any]]:
        """Fetch raw data from the source. Returns list of raw records."""

    @abstractmethod
    def transform_record(self, raw_record: dict[str, Any]) -> dict[str, Any]:
        """Transform a single raw API/file record into our DB schema dict."""

    # ------------------------------------------------------------------
    # HTTP helpers with retry + rate limiting
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._external_client is not None:
            return self._external_client
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            follow_redirects=True,
        )

    async def http_get(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> httpx.Response:
        """HTTP GET with rate limiting and exponential backoff retry."""
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            await self._rate_limiter.acquire()
            try:
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code in RETRYABLE_STATUS_CODES:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    if resp.status_code == 429:
                        retry_after = resp.headers.get("Retry-After")
                        if retry_after and retry_after.isdigit():
                            delay = max(delay, float(retry_after))
                    logger.warning(
                        "Retryable %d from %s (attempt %d/%d), waiting %.1fs",
                        resp.status_code, url, attempt + 1, MAX_RETRIES + 1, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                resp.raise_for_status()
                return resp
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
                last_exc = exc
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "Connection error for %s (attempt %d/%d): %s, waiting %.1fs",
                    url, attempt + 1, MAX_RETRIES + 1, exc, delay,
                )
                await asyncio.sleep(delay)
            except httpx.HTTPStatusError:
                raise

        raise httpx.ConnectError(
            f"Failed after {MAX_RETRIES + 1} attempts: {last_exc}"
        )

    # ------------------------------------------------------------------
    # Batch upsert helper
    # ------------------------------------------------------------------

    async def batch_upsert(
        self,
        session: AsyncSession,
        table_class: type,
        records: list[dict[str, Any]],
        conflict_column: str,
        update_columns: list[str] | None = None,
    ) -> int:
        """Insert records in batches with ON CONFLICT DO UPDATE (upsert).

        Args:
            session: Async SQLAlchemy session.
            table_class: The ORM model class to insert into.
            records: List of dicts matching the table columns.
            conflict_column: The unique column name for conflict detection.
            update_columns: Columns to update on conflict. If None, all
                non-primary-key columns from the first record are used.

        Returns:
            Number of records processed.
        """
        if not records:
            return 0

        total = 0
        for i in range(0, len(records), self._batch_size):
            batch = records[i : i + self._batch_size]
            stmt = pg_insert(table_class.__table__).values(batch)

            if update_columns is None:
                update_columns = [
                    k for k in batch[0].keys()
                    if k != conflict_column and k != "id"
                ]

            if update_columns:
                update_dict = {col: stmt.excluded[col] for col in update_columns}
                stmt = stmt.on_conflict_do_update(
                    index_elements=[conflict_column],
                    set_=update_dict,
                )
            else:
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=[conflict_column],
                )

            await session.execute(stmt)
            await session.flush()
            total += len(batch)

        return total

    async def batch_upsert_composite(
        self,
        session: AsyncSession,
        table_class: type,
        records: list[dict[str, Any]],
        conflict_columns: list[str],
        update_columns: list[str] | None = None,
    ) -> int:
        """Batch upsert with composite unique constraint."""
        if not records:
            return 0

        total = 0
        for i in range(0, len(records), self._batch_size):
            batch = records[i : i + self._batch_size]
            stmt = pg_insert(table_class.__table__).values(batch)

            if update_columns is None:
                update_columns = [
                    k for k in batch[0].keys()
                    if k not in conflict_columns and k != "id"
                ]

            if update_columns:
                update_dict = {col: stmt.excluded[col] for col in update_columns}
                stmt = stmt.on_conflict_do_update(
                    index_elements=conflict_columns,
                    set_=update_dict,
                )
            else:
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=conflict_columns,
                )

            await session.execute(stmt)
            await session.flush()
            total += len(batch)

        return total

    async def batch_insert_no_conflict(
        self,
        session: AsyncSession,
        table_class: type,
        records: list[dict[str, Any]],
    ) -> int:
        """Simple batch insert for tables without unique constraints (e.g. bioassays)."""
        if not records:
            return 0

        total = 0
        for i in range(0, len(records), self._batch_size):
            batch = records[i : i + self._batch_size]
            stmt = pg_insert(table_class.__table__).values(batch)
            stmt = stmt.on_conflict_do_nothing()
            await session.execute(stmt)
            await session.flush()
            total += len(batch)

        return total

    # ------------------------------------------------------------------
    # Error aggregation
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_error(error: Exception) -> str:
        """Categorize an exception for the frontend error panel."""
        name = type(error).__name__
        msg = str(error).lower()

        if isinstance(error, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
            return "timeout"
        if isinstance(error, (httpx.ConnectError, httpx.RemoteProtocolError)):
            return "connection"
        if isinstance(error, httpx.HTTPStatusError):
            code = error.response.status_code
            if code == 429:
                return "rate_limit"
            if 400 <= code < 500:
                return "api_client_error"
            if 500 <= code < 600:
                return "api_server_error"
            return "api_error"
        if "unique" in msg or "duplicate" in msg or "constraint" in msg:
            return "db_constraint"
        if "deadlock" in msg or "lock" in msg:
            return "db_lock"
        if "connection" in msg and ("refused" in msg or "reset" in msg):
            return "connection"
        if isinstance(error, (KeyError, ValueError, TypeError)):
            return "data_format"
        if isinstance(error, RuntimeError):
            return "runtime"
        return "unknown"

    @staticmethod
    def _suggest_fix(category: str, error: Exception) -> str:
        """Return a one-line suggested fix based on error category."""
        suggestions = {
            "timeout": "Increase connector timeout or check if the external API is under heavy load. Retry may succeed.",
            "connection": "External API is unreachable. Check network connectivity and whether the service is up.",
            "rate_limit": "API rate limit hit. Reduce rate_limit parameter or add longer backoff delays.",
            "api_client_error": "The request was rejected (4xx). Check URL, parameters, and API docs for breaking changes.",
            "api_server_error": "External API returned a server error (5xx). This is transient — retry later.",
            "api_error": "Unexpected API response. Check response format against connector expectations.",
            "db_constraint": "Database constraint violation. Check unique indexes and foreign keys on the target table.",
            "db_lock": "Database lock contention. Another task may be writing to the same table concurrently.",
            "data_format": "Unexpected data shape from the API. A field may be missing or have a different type than expected.",
            "runtime": "Application logic error. See traceback for the specific failure point.",
            "unknown": "Unclassified error. See traceback and error message for details.",
        }
        return suggestions.get(category, suggestions["unknown"])

    def record_error(self, context: str, error: Exception, record_id: str = "") -> None:
        """Record a non-fatal error with full diagnostic detail.

        Captures traceback, HTTP response info (for httpx errors), error
        classification, and a suggested fix — all stored in the JSONB
        errors column so the frontend can display a complete diagnostic
        report suitable for pasting into Claude Code.
        """
        category = self._classify_error(error)
        tb = traceback.format_exception(type(error), error, error.__traceback__)
        # Keep last 15 frames max to avoid bloating JSONB
        tb_str = "".join(tb[-15:])

        error_entry: dict[str, Any] = {
            "context": context,
            "error": str(error),
            "type": type(error).__name__,
            "category": category,
            "record_id": record_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "traceback": tb_str,
            "suggested_fix": self._suggest_fix(category, error),
        }

        # Capture HTTP details for httpx errors
        if isinstance(error, httpx.HTTPStatusError):
            resp = error.response
            body = ""
            try:
                body = resp.text[:2000]
            except Exception:
                body = "<unreadable>"
            error_entry["http"] = {
                "status_code": resp.status_code,
                "url": str(resp.url),
                "method": resp.request.method if resp.request else "GET",
                "response_body": body,
                "headers": dict(resp.headers.items())
                if len(dict(resp.headers)) < 20 else {"note": "too many headers"},
            }
        elif isinstance(error, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.ConnectError)):
            req = getattr(error, "request", None)
            if req:
                error_entry["http"] = {
                    "url": str(req.url),
                    "method": req.method,
                }

        self._errors.append(error_entry)
        logger.error(
            "Ingestion error [%s] %s (%s): %s",
            self.get_source_name(), context, category, error,
        )

    # ------------------------------------------------------------------
    # Ingestion log tracking
    # ------------------------------------------------------------------

    async def _create_log(self, session: AsyncSession, task_type: str) -> int:
        log_entry = IngestionLog(
            source=self.get_source_name(),
            task_type=task_type,
            status="running",
            records_processed=0,
            errors=[],
            started_at=datetime.now(timezone.utc),
        )
        session.add(log_entry)
        await session.flush()
        return log_entry.id

    async def _update_log(
        self,
        session: AsyncSession,
        log_id: int,
        status: str,
        records_processed: int,
        errors: list[dict],
    ) -> None:
        stmt = (
            update(IngestionLog)
            .where(IngestionLog.id == log_id)
            .values(
                status=status,
                records_processed=records_processed,
                errors=errors,
                completed_at=datetime.now(timezone.utc) if status in ("completed", "failed") else None,
            )
        )
        await session.execute(stmt)

    async def set_total_expected(self, session: AsyncSession, total: int) -> None:
        """Set (or update) the expected total record count for this run.

        Call this as soon as the total is known — e.g. after the first
        paginated API response or after a DB query that determines the
        work set.  May be called multiple times if the estimate is refined
        during multi-phase ingestion.
        """
        self._total_expected = total
        if self._log_id is None:
            return
        stmt = (
            update(IngestionLog)
            .where(IngestionLog.id == self._log_id)
            .values(total_expected=total)
        )
        await session.execute(stmt)
        await session.commit()

    async def _flush_progress(self, session: AsyncSession) -> None:
        """Flush the current records_processed count to the ingestion log.

        Called after every batch so that the live-status API can report
        near-realtime record counts to the frontend.
        """
        if self._log_id is None:
            return
        values: dict[str, Any] = {"records_processed": self._records_processed}
        if self._total_expected is not None:
            values["total_expected"] = self._total_expected
        stmt = (
            update(IngestionLog)
            .where(IngestionLog.id == self._log_id)
            .values(**values)
        )
        await session.execute(stmt)
        await session.commit()

    # ------------------------------------------------------------------
    # Main run method
    # ------------------------------------------------------------------

    async def run(self, task_type: str = "full_ingestion") -> dict[str, Any]:
        """Execute the full ingestion pipeline.

        1. Check for already-running task (skip if duplicate)
        2. Create ingestion log entry
        3. Fetch data from source
        4. Transform and batch-upsert records
        5. Finalize log with status and error summary

        Returns:
            Summary dict with records_processed, errors count, status.
        """
        owns_session = self._external_session is None
        if owns_session:
            session = async_session_factory()
        else:
            session = self._external_session

        try:
            # Guard: skip if this source is already running OR completed
            # very recently.  Stale Celery retries (persisted in Redis
            # across container restarts) arrive minutes after the fresh
            # run has already finished.  The 10-minute window covers the
            # worst-case retry countdown (4 min) with generous margin.
            from sqlalchemy import or_, and_

            cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
            existing = await session.execute(
                select(IngestionLog.id)
                .where(
                    IngestionLog.source == self.get_source_name(),
                    or_(
                        IngestionLog.status == "running",
                        and_(
                            IngestionLog.status == "completed",
                            IngestionLog.completed_at >= cutoff,
                        ),
                    ),
                )
                .limit(1)
            )
            if existing.scalar_one_or_none() is not None:
                logger.info(
                    "Skipping %s — already running or recently completed",
                    self.get_source_name(),
                )
                return {
                    "source": self.get_source_name(),
                    "status": "skipped_duplicate",
                    "records_processed": 0,
                    "errors_count": 0,
                    "errors": [],
                    "log_id": None,
                }

            self._log_id = await self._create_log(session, task_type)
            await session.commit()

            try:
                raw_records = await self.fetch_data(session)
                # Only overwrite _records_processed if the connector didn't
                # already set it inside fetch_data() (many connectors commit
                # data in batches and return [] while tracking count internally).
                if raw_records:
                    self._records_processed = len(raw_records)

                await self._update_log(
                    session, self._log_id, "completed",
                    self._records_processed, self._errors,
                )
                await session.commit()

            except Exception as exc:
                self.record_error("fetch_data", exc)
                # The failed operation may have left the transaction in an
                # aborted state (e.g. CardinalityViolationError).  We must
                # rollback before issuing any new SQL, otherwise the UPDATE
                # below will fail with InFailedSqlTransactionError.
                await session.rollback()
                await self._update_log(
                    session, self._log_id, "failed",
                    self._records_processed, self._errors,
                )
                await session.commit()
                raise

            return {
                "source": self.get_source_name(),
                "status": "completed",
                "records_processed": self._records_processed,
                "errors_count": len(self._errors),
                "errors": self._errors[:50],  # Cap error detail in return value
                "log_id": self._log_id,
            }

        finally:
            if owns_session:
                await session.close()

    # ------------------------------------------------------------------
    # Utility: resolve target ID from uniprot_id
    # ------------------------------------------------------------------

    async def get_or_create_target(
        self,
        session: AsyncSession,
        uniprot_id: str,
        gene_symbol: str = "",
        gene_name: str = "",
        organism: str = "Homo sapiens",
    ) -> int | None:
        """Look up a target by uniprot_id; create if missing. Returns target.id."""
        from app.models.target import Target

        if not uniprot_id:
            return None

        result = await session.execute(
            select(Target.id).where(Target.uniprot_id == uniprot_id)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            return row

        target = Target(
            uniprot_id=uniprot_id,
            gene_symbol=gene_symbol or uniprot_id,
            gene_name=gene_name,
            organism=organism,
        )
        session.add(target)
        await session.flush()
        return target.id

    async def get_drug_id_by_drugbank_id(
        self,
        session: AsyncSession,
        drugbank_id: str,
    ) -> int | None:
        """Look up a drug by drugbank_id. Returns drug.id or None."""
        from app.models.drug import Drug

        result = await session.execute(
            select(Drug.id).where(Drug.drugbank_id == drugbank_id)
        )
        return result.scalar_one_or_none()
