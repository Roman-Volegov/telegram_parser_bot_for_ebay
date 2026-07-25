from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.cards import send_listing_card
from bot.db import Database
from bot.models import Search, Source, User
from bot.providers import ProviderError, get_provider
from bot.providers.base import BaseProvider
from bot.services.credentials import CredentialsService

logger = logging.getLogger(__name__)
ETSY_CAPTCHA_NOTIFICATION_COOLDOWN_SEC = 3600


class PollerService:
    def __init__(
        self,
        bot: Bot,
        db: Database,
        credentials: CredentialsService,
        *,
        interval_sec: int,
        proxy: str | None = None,
        etsy_novnc_url: str | None = None,
    ) -> None:
        self.bot = bot
        self.db = db
        self.credentials = credentials
        self.interval_sec = interval_sec
        self.proxy = proxy
        self.etsy_novnc_url = etsy_novnc_url
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()
        self._etsy_captcha_notified_at: dict[int, float] = {}

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
        # В логе только снимок текущего цикла опроса
        cleared_users: set[int] = set()
        for search in searches:
            if search.telegram_id not in cleared_users:
                await self.db.clear_poll_logs(search.telegram_id)
                cleared_users.add(search.telegram_id)
            try:
                await self.process_search(search, notify=True)
            except Exception as exc:
                logger.exception("Failed search #%s", search.id)
                try:
                    await self.db.add_poll_log(
                        search.telegram_id,
                        search_id=search.id,
                        source=search.source,
                        keywords=search.keywords,
                        status="error",
                        message=str(exc)[:400],
                    )
                except Exception:
                    logger.exception("Failed to write poll log for search #%s", search.id)
            await asyncio.sleep(1)

    async def process_search(
        self,
        search: Search,
        *,
        notify: bool,
        record_log: bool = True,
    ) -> int:
        """Обрабатывает поиск. Возвращает число новых уведомлений.
        Если seen пуст — тихий seed без notify.
        """
        user = await self.db.get_user(search.telegram_id)
        if user is None:
            return 0

        async def write_log(**kwargs) -> None:
            if not record_log:
                return
            await self.db.add_poll_log(
                search.telegram_id,
                search_id=search.id,
                source=search.source,
                keywords=search.keywords,
                **kwargs,
            )

        try:
            provider = await self._build_provider(user, search)
        except ValueError as exc:
            logger.warning("Provider setup failed search #%s: %s", search.id, exc)
            await write_log(status="error", message=str(exc)[:400])
            return 0
        try:
            try:
                listings = await provider.search(search, limit=20)
            except ProviderError as exc:
                logger.warning("Provider error search #%s: %s", search.id, exc)
                await write_log(status="error", message=str(exc)[:400])
                if search.source is Source.ETSY and "DataDome" in str(exc):
                    await self._notify_etsy_captcha(search.telegram_id)
                return 0
        finally:
            await provider.aclose()

        found = len(listings)
        if not listings:
            await write_log(
                status="empty",
                found=0,
                message="Источник не вернул объявлений",
            )
            return 0

        item_ids = [item.id for item in listings]
        seen_count = await self.db.count_seen(search.id)
        new_ids = await self.db.filter_new_ids(search.id, item_ids)

        if seen_count == 0:
            await self.db.mark_seen(search.id, item_ids)
            logger.info(
                "Seeded search #%s with %s items (%s)",
                search.id,
                found,
                search.source,
            )
            await write_log(
                status="seed",
                found=found,
                message="Первый опрос: объявления сохранены без уведомлений",
            )
            return 0

        if not new_ids:
            await write_log(
                status="ok",
                found=found,
                new_items=0,
                notified=0,
                message="Новых объявлений нет",
            )
            return 0

        new_set = set(new_ids)
        new_listings = [item for item in listings if item.id in new_set]
        await self.db.mark_seen(search.id, [item.id for item in new_listings])
        new_count = len(new_listings)

        if not notify:
            await write_log(
                status="ok",
                found=found,
                new_items=new_count,
                notified=0,
                message="Новые объявления отмечены без уведомлений",
            )
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

        await write_log(
            status="ok",
            found=found,
            new_items=new_count,
            notified=sent,
            message="Опрос завершён" if sent else "Новые найдены, уведомления не отправлены",
        )
        return sent

    async def _notify_etsy_captcha(self, telegram_id: int) -> None:
        if not self.etsy_novnc_url:
            return
        now = time.monotonic()
        last_notified = self._etsy_captcha_notified_at.get(telegram_id, 0)
        if now - last_notified < ETSY_CAPTCHA_NOTIFICATION_COOLDOWN_SEC:
            return
        try:
            await self.bot.send_message(
                telegram_id,
                "Etsy запросил проверку. Откройте браузер и пройдите CAPTCHA:",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🔐 Пройти CAPTCHA Etsy",
                                url=self.etsy_novnc_url,
                            )
                        ]
                    ]
                ),
            )
            self._etsy_captcha_notified_at[telegram_id] = now
        except Exception:
            logger.exception("Failed to send Etsy CAPTCHA link to user=%s", telegram_id)

    async def _build_provider(self, user: User, search: Search) -> BaseProvider:
        if search.source is Source.EBAY_API:
            return await self.credentials.build_ebay_api_provider(
                user,
                proxy=self.proxy or None,
                marketplace_id=search.marketplace or user.ebay_marketplace,
            )
        if search.source is Source.ETSY:
            return await self.credentials.build_etsy_provider(
                user,
                proxy=self.proxy or None,
            )
        return get_provider(search.source, proxy=self.proxy or None)
