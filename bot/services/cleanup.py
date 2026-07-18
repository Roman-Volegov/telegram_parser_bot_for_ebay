from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from bot.db import Database

logger = logging.getLogger(__name__)


class CleanupService:
    def __init__(self, db: Database, *, ttl_days: int) -> None:
        self.db = db
        self.ttl_days = ttl_days
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopped.clear()
            self._task = asyncio.create_task(self._run(), name="cleanup")

    async def stop(self) -> None:
        self._stopped.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        logger.info("Cleanup service started, ttl_days=%s", self.ttl_days)
        while not self._stopped.is_set():
            try:
                deleted = await self.db.cleanup_seen_items(self.ttl_days)
                if deleted:
                    logger.info("Cleanup removed %s seen_items", deleted)
            except Exception:
                logger.exception("Cleanup failed")
            # Ждём до следующего дня (или ~24ч)
            sleep_for = _seconds_until_next_run()
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=sleep_for)
            except asyncio.TimeoutError:
                continue


def _seconds_until_next_run() -> float:
    now = datetime.now(timezone.utc)
    # Каждый день в 03:17 UTC
    target = now.replace(hour=3, minute=17, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(60.0, (target - now).total_seconds())
