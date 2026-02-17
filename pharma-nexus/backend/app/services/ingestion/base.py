"""Base ingestion framework for all 13 biomedical data source connectors.

Provides: async HTTP with rate limiting, retry with exponential backoff,
batch upsert, progress tracking via ingestion_logs, and error aggregation.
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
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
        self._semaphore = asyncio.Semaphore(max(1, int(requests_per_second)))
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

    def record_error(self, context: str, error: Exception, record_id: str = "") -> None:
        """Record a non-fatal error. Ingestion continues."""
        error_entry = {
            "context": context,
            "error": str(error),
            "type": type(error).__name__,
            "record_id": record_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._errors.append(error_entry)
        logger.error("Ingestion error [%s] %s: %s", self.get_source_name(), context, error)

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

    # ------------------------------------------------------------------
    # Main run method
    # ------------------------------------------------------------------

    async def run(self, task_type: str = "full_ingestion") -> dict[str, Any]:
        """Execute the full ingestion pipeline.

        1. Create ingestion log entry
        2. Fetch data from source
        3. Transform and batch-upsert records
        4. Finalize log with status and error summary

        Returns:
            Summary dict with records_processed, errors count, status.
        """
        owns_session = self._external_session is None
        session = self._external_session or async_session_factory()

        try:
            if owns_session:
                # For owned sessions, we manage the context manually
                session = async_session_factory()

            self._log_id = await self._create_log(session, task_type)
            await session.commit()

            try:
                raw_records = await self.fetch_data(session)
                self._records_processed = len(raw_records) if raw_records else 0

                await self._update_log(
                    session, self._log_id, "completed",
                    self._records_processed, self._errors,
                )
                await session.commit()

            except Exception as exc:
                self.record_error("fetch_data", exc)
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
