import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ingestion_log import IngestionLog

logger = logging.getLogger(__name__)


class BaseConnector:
    """Base class for all ingestion connectors."""

    SOURCE_NAME = "base"

    def __init__(self, db: AsyncSession, log_id: int):
        self.db = db
        self.log_id = log_id

    async def run(self):
        raise NotImplementedError

    async def _update_log(self, **kwargs):
        result = await self.db.execute(
            select(IngestionLog).where(IngestionLog.id == self.log_id)
        )
        log = result.scalar_one_or_none()
        if log:
            for k, v in kwargs.items():
                setattr(log, k, v)
            await self.db.commit()

    async def _mark_completed(self, records_processed: int):
        await self._update_log(
            status="completed",
            records_processed=records_processed,
            completed_at=datetime.now(timezone.utc),
        )

    async def _mark_failed(self, error_message: str):
        await self._update_log(
            status="failed",
            error_message=error_message[:2000],
            completed_at=datetime.now(timezone.utc),
        )

    async def _fetch_json(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: dict = None,
        max_retries: int = 3,
    ) -> dict | list | None:
        """Fetch JSON from a URL with retries and error handling."""
        for attempt in range(max_retries):
            try:
                resp = await client.get(url, params=params, timeout=30.0)
                if resp.status_code == 404:
                    return None
                if resp.status_code == 429:
                    import asyncio
                    retry_after = resp.headers.get("Retry-After", "5")
                    delay = int(retry_after) if retry_after.isdigit() else 5
                    logger.warning(f"Rate limited, waiting {delay}s")
                    await asyncio.sleep(delay)
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500 and attempt < max_retries - 1:
                    import asyncio
                    await asyncio.sleep(2 ** attempt)
                    continue
                logger.error(f"HTTP error fetching {url}: {e}")
                return None
            except (httpx.ConnectError, httpx.ReadTimeout) as e:
                if attempt < max_retries - 1:
                    import asyncio
                    await asyncio.sleep(2 ** attempt)
                    continue
                logger.error(f"Connection error fetching {url}: {e}")
                return None
            except Exception as e:
                logger.error(f"Unexpected error fetching {url}: {e}")
                return None
        return None
