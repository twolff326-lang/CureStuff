"""Base ingestion framework for all 13 biomedical data source connectors.

Provides: async HTTP with rate limiting, retry with exponential backoff,
batch upsert, progress tracking via ingestion_logs, error aggregation,
and comprehensive execution reports (phases, HTTP stats, data quality,
timeline, warnings).
"""

import asyncio
import logging
import time
import traceback
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

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
        self._checkpoint: dict[str, Any] | None = None
        self._run_start_time: float = 0.0

        # Execution report — populated automatically by base methods and
        # optionally enriched by subclass calls to begin_phase / end_phase /
        # record_warning / record_skip.
        self._report: dict[str, Any] = self._init_report()

    # ------------------------------------------------------------------
    # Report infrastructure
    # ------------------------------------------------------------------

    @staticmethod
    def _init_report() -> dict[str, Any]:
        """Create an empty report skeleton."""
        return {
            "phases": [],
            "http": {
                "total_requests": 0,
                "successful": 0,
                "retries": 0,
                "failures": 0,
                "by_status_code": {},
                "by_domain": {},
                "latency_samples": [],   # raw ms values, capped at 2000
                "slowest_call": None,    # {url, latency_ms, status_code}
            },
            "data_quality": {
                "total_api_records_received": 0,
                "skipped_non_dict": 0,
                "skipped_missing_field": 0,
                "skipped_filtered": 0,
                "skipped_duplicate": 0,
                "records_inserted": 0,
                "records_upserted": 0,
            },
            "db_operations": {
                "batch_upsert_calls": 0,
                "batch_insert_calls": 0,
                "total_rows_written": 0,
            },
            "warnings": [],
            "timeline": [],
            "guard_decisions": {
                "stale_logs_expired": 0,
                "duplicate_check_result": None,
                "checkpoint_loaded": None,
            },
            "caches": {},
        }

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _elapsed_s(self) -> float:
        """Seconds since run() started, or 0 if not started yet."""
        if self._run_start_time:
            return round(time.monotonic() - self._run_start_time, 2)
        return 0.0

    def _log_event(self, event: str, detail: str = "") -> None:
        """Append a timestamped entry to the report timeline."""
        self._report["timeline"].append({
            "t": self._now_iso(),
            "elapsed_s": self._elapsed_s(),
            "event": event,
            "detail": detail,
        })

    def begin_phase(self, name: str, detail: str = "") -> None:
        """Start tracking a named execution phase (e.g. 'Phase 1: mechanisms').

        Call end_phase() when the phase completes.
        """
        self._report["phases"].append({
            "name": name,
            "status": "running",
            "started_at": self._now_iso(),
            "ended_at": None,
            "duration_s": None,
            "records_in": 0,
            "records_out": 0,
            "detail": detail,
            "_mono_start": time.monotonic(),
        })
        self._log_event(f"phase_started:{name}", detail)

    def end_phase(
        self,
        records_in: int = 0,
        records_out: int = 0,
        status: str = "completed",
        detail: str = "",
    ) -> None:
        """Complete the most recent phase with stats."""
        if not self._report["phases"]:
            return
        phase = self._report["phases"][-1]
        mono_start = phase.pop("_mono_start", time.monotonic())
        phase["status"] = status
        phase["ended_at"] = self._now_iso()
        phase["duration_s"] = round(time.monotonic() - mono_start, 2)
        phase["records_in"] = records_in
        phase["records_out"] = records_out
        if detail:
            phase["detail"] = detail
        self._log_event(
            f"phase_ended:{phase['name']}",
            f"status={status} in={records_in} out={records_out}"
            + (f" {detail}" if detail else ""),
        )

    def record_warning(self, context: str, message: str) -> None:
        """Record a non-fatal observation that isn't an error.

        Warnings are for things like: empty API pages, unexpected but
        non-breaking data shapes, slow responses, fallback paths taken.
        """
        entry = {
            "timestamp": self._now_iso(),
            "elapsed_s": self._elapsed_s(),
            "context": context,
            "message": message,
        }
        self._report["warnings"].append(entry)
        logger.warning(
            "Ingestion warning [%s] %s: %s",
            self.get_source_name(), context, message,
        )

    def record_skip(self, reason: str, count: int = 1) -> None:
        """Increment a skip counter in the data quality section.

        Valid reasons: non_dict, missing_field, filtered, duplicate.
        """
        key = f"skipped_{reason}"
        dq = self._report["data_quality"]
        if key in dq:
            dq[key] += count
        else:
            dq[key] = count

    def report_api_records(self, count: int) -> None:
        """Record how many raw records were received from an API page."""
        self._report["data_quality"]["total_api_records_received"] += count

    def report_cache_access(self, cache_name: str, hit: bool) -> None:
        """Track cache hit/miss for a named cache."""
        caches = self._report["caches"]
        if cache_name not in caches:
            caches[cache_name] = {"hits": 0, "misses": 0}
        if hit:
            caches[cache_name]["hits"] += 1
        else:
            caches[cache_name]["misses"] += 1

    def _finalize_report(self) -> dict[str, Any]:
        """Compute summary stats and return the report dict for persistence.

        Called once at the end of run() before writing to the DB.
        """
        report = self._report

        # Compute HTTP latency percentiles
        samples = report["http"]["latency_samples"]
        if samples:
            sorted_samples = sorted(samples)
            n = len(sorted_samples)
            report["http"]["latency_p50_ms"] = round(sorted_samples[n // 2], 1)
            report["http"]["latency_p95_ms"] = round(
                sorted_samples[min(int(n * 0.95), n - 1)], 1
            )
            report["http"]["latency_p99_ms"] = round(
                sorted_samples[min(int(n * 0.99), n - 1)], 1
            )
            report["http"]["latency_max_ms"] = round(sorted_samples[-1], 1)
            report["http"]["latency_avg_ms"] = round(sum(samples) / n, 1)
        # Remove raw samples to avoid bloating JSONB (keep at most 200 for spark-line)
        if len(samples) > 200:
            step = len(samples) // 200
            report["http"]["latency_samples"] = samples[::step][:200]

        # Total run duration
        if self._run_start_time:
            report["total_duration_s"] = round(
                time.monotonic() - self._run_start_time, 2
            )

        # Summary line
        http = report["http"]
        dq = report["data_quality"]
        report["summary"] = (
            f"{http['total_requests']} HTTP calls "
            f"({http['retries']} retries, {http['failures']} failures), "
            f"{dq['total_api_records_received']} API records received, "
            f"{dq['records_upserted'] + dq['records_inserted']} rows written, "
            f"{len(report['warnings'])} warnings, "
            f"{len(self._errors)} errors"
        )

        return report

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
        """HTTP GET with rate limiting and exponential backoff retry.

        Automatically tracks request count, latency, retries, and failures
        in the execution report.
        """
        http_report = self._report["http"]
        domain = urlparse(url).netloc
        retries_this_call = 0
        last_exc: Exception | None = None
        t0 = time.monotonic()

        for attempt in range(MAX_RETRIES + 1):
            await self._rate_limiter.acquire()
            try:
                resp = await client.get(url, params=params, headers=headers)
                latency_ms = (time.monotonic() - t0) * 1000

                # Track per-status-code counts
                sc = str(resp.status_code)
                http_report["by_status_code"][sc] = http_report["by_status_code"].get(sc, 0) + 1

                if resp.status_code in RETRYABLE_STATUS_CODES:
                    retries_this_call += 1
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

                # Success — record metrics
                http_report["total_requests"] += 1
                http_report["successful"] += 1
                http_report["retries"] += retries_this_call
                if len(http_report["latency_samples"]) < 2000:
                    http_report["latency_samples"].append(round(latency_ms, 1))

                # Per-domain stats
                if domain not in http_report["by_domain"]:
                    http_report["by_domain"][domain] = {
                        "requests": 0, "total_ms": 0.0,
                    }
                http_report["by_domain"][domain]["requests"] += 1
                http_report["by_domain"][domain]["total_ms"] += latency_ms

                # Slowest call tracker
                slowest = http_report["slowest_call"]
                if slowest is None or latency_ms > slowest["latency_ms"]:
                    http_report["slowest_call"] = {
                        "url": url[:200],
                        "latency_ms": round(latency_ms, 1),
                        "status_code": resp.status_code,
                    }

                return resp

            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
                last_exc = exc
                retries_this_call += 1
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "Connection error for %s (attempt %d/%d): %s, waiting %.1fs",
                    url, attempt + 1, MAX_RETRIES + 1, exc, delay,
                )
                await asyncio.sleep(delay)
            except httpx.HTTPStatusError:
                # Non-retryable HTTP error — track and re-raise
                latency_ms = (time.monotonic() - t0) * 1000
                http_report["total_requests"] += 1
                http_report["failures"] += 1
                http_report["retries"] += retries_this_call
                if len(http_report["latency_samples"]) < 2000:
                    http_report["latency_samples"].append(round(latency_ms, 1))
                raise

        # All retries exhausted
        http_report["total_requests"] += 1
        http_report["failures"] += 1
        http_report["retries"] += retries_this_call
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

        self._report["db_operations"]["batch_upsert_calls"] += 1
        self._report["db_operations"]["total_rows_written"] += total
        self._report["data_quality"]["records_upserted"] += total
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

        self._report["db_operations"]["batch_upsert_calls"] += 1
        self._report["db_operations"]["total_rows_written"] += total
        self._report["data_quality"]["records_upserted"] += total
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

        self._report["db_operations"]["batch_insert_calls"] += 1
        self._report["db_operations"]["total_rows_written"] += total
        self._report["data_quality"]["records_inserted"] += total
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
            "elapsed_s": self._elapsed_s(),
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
        self._log_event(
            f"error:{category}",
            f"[{context}] {type(error).__name__}: {str(error)[:200]}",
        )
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
        values: dict[str, Any] = {
            "status": status,
            "records_processed": records_processed,
            "errors": errors,
            "report": self._finalize_report(),
        }
        if status in ("completed", "failed"):
            now = datetime.now(timezone.utc)
            values["completed_at"] = now
            # Also set duration_seconds from monotonic clock for precision
            if self._run_start_time:
                values["duration_seconds"] = round(
                    time.monotonic() - self._run_start_time, 2
                )
        stmt = (
            update(IngestionLog)
            .where(IngestionLog.id == log_id)
            .values(**values)
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
        self._log_event("total_expected_set", f"total_expected={total}")
        if self._log_id is None:
            return
        try:
            stmt = (
                update(IngestionLog)
                .where(IngestionLog.id == self._log_id)
                .values(total_expected=total)
            )
            await session.execute(stmt)
            await session.commit()
        except Exception as exc:
            logger.warning(
                "Failed to set total_expected for %s: %s",
                self.get_source_name(), exc,
            )
            try:
                await session.rollback()
            except Exception:
                pass

    async def _flush_progress(self, session: AsyncSession) -> None:
        """Flush the current records_processed count to the ingestion log.

        Called after every batch so that the live-status API can report
        near-realtime record counts to the frontend.

        Errors are caught and logged rather than propagated — a failed
        progress update must never crash the entire ingestion pipeline.
        """
        if self._log_id is None:
            return
        values: dict[str, Any] = {"records_processed": self._records_processed}
        if self._total_expected is not None:
            values["total_expected"] = self._total_expected
        try:
            stmt = (
                update(IngestionLog)
                .where(IngestionLog.id == self._log_id)
                .values(**values)
            )
            await session.execute(stmt)
            await session.commit()
        except Exception as exc:
            logger.warning(
                "Failed to flush progress for %s (records_processed=%d): %s",
                self.get_source_name(), self._records_processed, exc,
            )
            try:
                await session.rollback()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Checkpoint helpers (resume after failure)
    # ------------------------------------------------------------------

    async def _save_checkpoint(
        self, session: AsyncSession, data: dict[str, Any],
    ) -> None:
        """Persist checkpoint data so a retry can resume from this point."""
        if self._log_id is None:
            return
        self._log_event("checkpoint_saved", str(data))
        try:
            stmt = (
                update(IngestionLog)
                .where(IngestionLog.id == self._log_id)
                .values(checkpoint=data)
            )
            await session.execute(stmt)
            await session.commit()
        except Exception as exc:
            logger.warning(
                "Failed to save checkpoint for %s: %s",
                self.get_source_name(), exc,
            )
            try:
                await session.rollback()
            except Exception:
                pass

    async def _load_previous_checkpoint(
        self, session: AsyncSession,
    ) -> dict[str, Any] | None:
        """Load checkpoint from the most recent failed run for this source.

        Returns the checkpoint dict if one exists, else None.
        """
        result = await session.execute(
            select(IngestionLog.checkpoint)
            .where(
                IngestionLog.source == self.get_source_name(),
                IngestionLog.status == "failed",
                IngestionLog.checkpoint.isnot(None),
            )
            .order_by(IngestionLog.started_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row:
            logger.info(
                "Loaded checkpoint for %s from previous failed run: %s",
                self.get_source_name(), row,
            )
        return row

    # ------------------------------------------------------------------
    # Main run method
    # ------------------------------------------------------------------

    async def run(self, task_type: str = "full_ingestion") -> dict[str, Any]:
        """Execute the full ingestion pipeline.

        1. Check for already-running task (skip if duplicate)
        2. Create ingestion log entry
        3. Fetch data from source
        4. Transform and batch-upsert records
        5. Finalize log with status, error summary, and execution report

        Returns:
            Summary dict with records_processed, errors count, status.
        """
        self._run_start_time = time.monotonic()
        self._log_event("run_started", f"source={self.get_source_name()} task_type={task_type}")

        owns_session = self._external_session is None
        if owns_session:
            session = async_session_factory()
        else:
            session = self._external_session

        try:
            from sqlalchemy import or_, and_

            # Expire stale "running" logs.  If a previous task was killed
            # (OOM, worker restart, etc.) the log stays "running" forever
            # and blocks all future runs.  Mark anything running for >2 h
            # as failed so the duplicate guard below won't mis-fire.
            stale_cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
            stale_result = await session.execute(
                select(IngestionLog.id).where(
                    IngestionLog.source == self.get_source_name(),
                    IngestionLog.status == "running",
                    IngestionLog.started_at < stale_cutoff,
                )
            )
            stale_ids = [row[0] for row in stale_result.all()]
            if stale_ids:
                await session.execute(
                    update(IngestionLog)
                    .where(IngestionLog.id.in_(stale_ids))
                    .values(
                        status="failed",
                        completed_at=datetime.now(timezone.utc),
                    )
                )
                await session.commit()
                logger.info(
                    "Expired %d stale 'running' log(s) for %s",
                    len(stale_ids), self.get_source_name(),
                )

            self._report["guard_decisions"]["stale_logs_expired"] = len(stale_ids)
            self._log_event(
                "stale_check",
                f"expired {len(stale_ids)} stale log(s)" if stale_ids else "no stale logs",
            )

            # Guard: skip if this source is already running OR completed
            # very recently.  Stale Celery retries (persisted in Redis
            # across container restarts) arrive minutes after the fresh
            # run has already finished.  The 10-minute window covers the
            # worst-case retry countdown (4 min) with generous margin.
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
            existing = await session.execute(
                select(IngestionLog.id, IngestionLog.status, IngestionLog.started_at)
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
            existing_row = existing.first()
            if existing_row is not None:
                reason = (
                    f"blocked by log id={existing_row[0]} "
                    f"status={existing_row[1]} started={existing_row[2]}"
                )
                self._report["guard_decisions"]["duplicate_check_result"] = reason
                self._log_event("duplicate_guard_blocked", reason)
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
                    "report": self._finalize_report(),
                }

            self._report["guard_decisions"]["duplicate_check_result"] = "passed"
            self._log_event("duplicate_guard_passed")

            # Load checkpoint from the most recent failed run (if any)
            # so connectors can resume rather than restart from scratch.
            self._checkpoint = await self._load_previous_checkpoint(session)
            self._report["guard_decisions"]["checkpoint_loaded"] = self._checkpoint
            self._log_event(
                "checkpoint_check",
                f"loaded: {self._checkpoint}" if self._checkpoint else "no checkpoint found",
            )

            self._log_id = await self._create_log(session, task_type)
            await session.commit()
            self._log_event("log_created", f"ingestion_log.id={self._log_id}")

            try:
                raw_records = await self.fetch_data(session)
                # Only overwrite _records_processed if the connector didn't
                # already set it inside fetch_data() (many connectors commit
                # data in batches and return [] while tracking count internally).
                if raw_records:
                    self._records_processed = len(raw_records)

                self._log_event(
                    "fetch_data_completed",
                    f"records_processed={self._records_processed}",
                )

                await self._update_log(
                    session, self._log_id, "completed",
                    self._records_processed, self._errors,
                )
                await session.commit()

            except Exception as exc:
                self.record_error("fetch_data", exc)
                self._log_event(
                    "fetch_data_failed",
                    f"{type(exc).__name__}: {str(exc)[:200]}",
                )
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
