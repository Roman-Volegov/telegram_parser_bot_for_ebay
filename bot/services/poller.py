from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from bot.cards import send_listing_card
from bot.db import Database
from bot.models import Search, Source, User
from bot.providers import ProviderError, get_provider
from bot.providers.base import BaseProvider
from bot.services.credentials import CredentialsService

logger = logging.getLogger(__name__)


class PollerService:
    def __init__(
        self,
        bot: Bot,
        db: Database,
        credentials: CredentialsService,
        *,
        interval_sec: int,
        proxy: str | None = None,
    ) -> None:
        self.bot = bot
        self.db = db
        self.credentials = credentials
        self.interval_sec = interval_sec
        self.proxy = proxy
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopped.clear()
            self._task = asyncio.create_task(self._run(), name="poller")

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
        logger.info("Poller started, interval=%ss", self.interval_sec)
        await asyncio.sleep(5)
        while not self._stopped.is_set():
            try:
                await self.poll_once()
            except Exception:
                logger.exception("Poller cycle failed")
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.interval_sec)
            except asyncio.TimeoutError:
                continue

    async def poll_once(self) -> None:
        searches = await self.db.list_active_searches_for_polling()
        for search in searches:
            try:
                await self.process_search(search, notify=True)
            except Exception:
                logger.exception("Failed search #%s", search.id)
            await asyncio.sleep(1)

    async def process_search(self, search: Search, *, notify: bool) -> int:
        """Обрабатывает поиск. Возвращает число новых уведомлений.
        Если seen пуст — тихий seed без notify.
        """
        user = await self.db.get_user(search.telegram_id)
        if user is None:
            return 0
        provider = await self._build_provider(user, search.source)
        try:
            listings = await provider.search(search, limit=20)
        except ProviderError as exc:
            logger.warning("Provider error search #%s: %s", search.id, exc)
            return 0
        finally:
            await provider.aclose()

        if not listings:
            return 0

        item_ids = [item.id for item in listings]
        seen_count = await self.db.count_seen(search.id)
        new_ids = await self.db.filter_new_ids(search.id, item_ids)

        if seen_count == 0:
            await self.db.mark_seen(search.id, item_ids)
            logger.info(
                "Seeded search #%s with %s items (%s)",
                search.id,
                len(item_ids),
                search.source,
            )
            return 0

        if not new_ids:
            return 0

        new_set = set(new_ids)
        new_listings = [item for item in listings if item.id in new_set]
        await self.db.mark_seen(search.id, [item.id for item in new_listings])

        if not notify:
            return 0

        sent = 0
        for listing in new_listings[:8]:
            try:
                await send_listing_card(self.bot, search.telegram_id, listing)
                sent += 1
            except Exception:
                logger.exception(
                    "Notify failed user=%s search=#%s item=%s",
                    search.telegram_id,
                    search.id,
                    listing.id,
                )
            await asyncio.sleep(0.35)
        return sent

    async def _build_provider(self, user: User, source: Source) -> BaseProvider:
        if source is Source.EBAY_API:
            return await self.credentials.build_ebay_api_provider(
                user, proxy=self.proxy or None
            )
        return get_provider(source, proxy=self.proxy or None)
