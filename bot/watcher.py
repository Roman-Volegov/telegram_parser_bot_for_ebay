from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.enums import ParseMode

from bot.db import Database
from bot.formatting import format_listing, marketplace_label
from bot.models import Marketplace, WatchFilter
from bot.parsers import ParserError, get_parser
from bot.parsers.base import BaseParser

logger = logging.getLogger(__name__)


class WatcherService:
    def __init__(
        self,
        bot: Bot,
        db: Database,
        *,
        interval_seconds: int,
        ebay_app_id: str = "",
        ebay_cert_id: str = "",
        ebay_marketplace_id: str = "EBAY_US",
    ) -> None:
        self.bot = bot
        self.db = db
        self.interval_seconds = interval_seconds
        self._parsers: dict[Marketplace, BaseParser] = {
            Marketplace.EBAY: get_parser(
                Marketplace.EBAY,
                app_id=ebay_app_id,
                cert_id=ebay_cert_id,
                marketplace_id=ebay_marketplace_id,
            ),
            Marketplace.POSHMARK: get_parser(Marketplace.POSHMARK),
        }
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopped.clear()
            self._task = asyncio.create_task(self._run(), name="watch-loop")

    async def stop(self) -> None:
        self._stopped.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        for parser in self._parsers.values():
            await parser.aclose()

    async def _run(self) -> None:
        logger.info("Watcher started, interval=%ss", self.interval_seconds)
        # Небольшая пауза, чтобы бот успел подняться.
        await asyncio.sleep(3)
        while not self._stopped.is_set():
            try:
                await self.poll_once()
            except Exception:
                logger.exception("Watcher poll failed")
            try:
                await asyncio.wait_for(
                    self._stopped.wait(),
                    timeout=self.interval_seconds,
                )
            except asyncio.TimeoutError:
                continue

    async def poll_once(self) -> None:
        watches = await self.db.list_active_watches()
        for watch in watches:
            await self._process_watch(watch)
            await asyncio.sleep(1)

    async def _process_watch(self, watch: WatchFilter) -> None:
        parser = self._parsers[watch.marketplace]
        try:
            listings = await parser.search_watch(watch, limit=15)
        except ParserError as exc:
            logger.warning("Parser error for watch #%s: %s", watch.id, exc)
            return

        if not listings:
            return

        external_ids = [listing.external_id for listing in listings]
        new_ids = await self.db.filter_new_ids(watch.id, external_ids)

        # Первый прогон только запоминает текущую выдачу, без спама.
        known_before = len(external_ids) - len(new_ids)
        if known_before == 0 and len(new_ids) == len(external_ids):
            await self.db.mark_seen(watch.id, external_ids)
            logger.info(
                "Seeded watch #%s with %s listings (%s)",
                watch.id,
                len(external_ids),
                watch.marketplace,
            )
            return

        new_listings = [item for item in listings if item.external_id in set(new_ids)]
        if not new_listings:
            return

        await self.db.mark_seen(watch.id, [item.external_id for item in new_listings])
        market = marketplace_label(watch.marketplace)
        for listing in new_listings[:5]:
            text = (
                f"🆕 Новый лот по подписке #{watch.id}\n"
                f"{market}: <code>{watch.query}</code>\n\n"
                f"{format_listing(listing)}"
            )
            try:
                await self.bot.send_message(
                    watch.user_id,
                    text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False,
                )
            except Exception:
                logger.exception(
                    "Failed to notify user_id=%s for watch #%s",
                    watch.user_id,
                    watch.id,
                )
            await asyncio.sleep(0.3)
