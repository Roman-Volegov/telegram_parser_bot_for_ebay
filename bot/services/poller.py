from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.cards import send_listing_card
from bot.db import Database
from bot.models import Listing, Search, Source, User
from bot.providers import ProviderError, get_provider
from bot.providers.base import BaseProvider
from bot.services.credentials import CredentialsService
from bot.services.etsy_access import EtsyVncAccess

logger = logging.getLogger(__name__)
ETSY_CAPTCHA_NOTIFICATION_COOLDOWN_SEC = 1800
ETSY_SEARCH_TIMEOUT_SEC = 150
ETSY_BROWSER_RESTART_TIMEOUT_SEC = 20


class PollerService:
    def __init__(
        self,
        bot: Bot,
        db: Database,
        credentials: CredentialsService,
        *,
        interval_sec: int,
        proxy: str | None = None,
        etsy_vnc_access: EtsyVncAccess | None = None,
        etsy_captcha_notify_ids: set[int] | None = None,
    ) -> None:
        self.bot = bot
        self.db = db
        self.credentials = credentials
        self.interval_sec = interval_sec
        self.proxy = proxy
        self.etsy_vnc_access = etsy_vnc_access
        self.etsy_captcha_notify_ids = etsy_captcha_notify_ids or set()
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()
        self._etsy_captcha_notified_at: float | None = None
        self._search_locks: dict[int, asyncio.Lock] = {}
        self._background_tasks: set[asyncio.Task] = set()
        self._public_providers: dict[Source, BaseProvider] = {}
        self._source_semaphores = {
            Source.ETSY: asyncio.Semaphore(1),
            Source.EBAY_API: asyncio.Semaphore(5),
            Source.EBAY_PARSER: asyncio.Semaphore(4),
            Source.POSHMARK: asyncio.Semaphore(4),
        }

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
        for task in self._background_tasks:
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()
        for provider in self._public_providers.values():
            await provider.aclose()
        self._public_providers.clear()

    def schedule_search(
        self,
        search: Search,
        *,
        notify: bool,
        record_log: bool = True,
    ) -> None:
        task = asyncio.create_task(
            self.process_search(search, notify=notify, record_log=record_log),
            name=f"search-seed-{search.id}",
        )
        self._background_tasks.add(task)

        def finished(done: asyncio.Task) -> None:
            self._background_tasks.discard(done)
            if done.cancelled():
                return
            try:
                done.result()
            except Exception:
                logger.exception("Background search #%s failed", search.id)

        task.add_done_callback(finished)

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
        user_ids = {search.telegram_id for search in searches}
        await asyncio.gather(*(self.db.clear_poll_logs(user_id) for user_id in user_ids))
        await asyncio.gather(*(self._poll_search(search) for search in searches))

    async def _poll_search(self, search: Search) -> None:
        semaphore = self._source_semaphores[search.source]
        async with semaphore:
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
        lock = self._search_locks.setdefault(search.id, asyncio.Lock())
        async with lock:
            return await self._process_search_locked(
                search,
                notify=notify,
                record_log=record_log,
            )

    async def _process_search_locked(
        self,
        search: Search,
        *,
        notify: bool,
        record_log: bool,
    ) -> int:
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
                if search.source is Source.ETSY:
                    listings = await self._search_etsy_with_timeout(
                        provider,
                        search,
                    )
                else:
                    listings = await provider.search(search, limit=20)
            except TimeoutError:
                message = (
                    f"Etsy не ответил за {ETSY_SEARCH_TIMEOUT_SEC} сек. "
                    "Браузер перезапущен, поиск повторится в следующем цикле."
                )
                logger.error("Etsy timeout search #%s; restarting browser", search.id)
                await write_log(status="error", message=message)
                await self._restart_etsy_browser(reason=f"search #{search.id} timeout")
                return 0
            except ProviderError as exc:
                logger.warning("Provider error search #%s: %s", search.id, exc)
                await write_log(status="error", message=str(exc)[:400])
                if exc.code == "ETSY_CAPTCHA":
                    await self._notify_etsy_captcha(
                        extra_ids={search.telegram_id},
                    )
                return 0
        finally:
            if search.source not in {Source.EBAY_PARSER, Source.POSHMARK}:
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
        has_seen = await self.db.has_seen(search.id)
        new_ids = await self.db.filter_new_ids(search.id, item_ids)

        if not has_seen:
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
        new_count = len(new_listings)

        if not notify:
            await self.db.mark_seen(search.id, [item.id for item in new_listings])
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
                await self.db.mark_seen(search.id, [listing.id])
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
            message=(
                f"Опрос завершён, ещё ожидают отправки: {new_count - sent}"
                if new_count > sent
                else "Опрос завершён"
            )
            if sent
            else "Новые найдены, уведомления не отправлены",
        )
        return sent

    async def _search_etsy_with_timeout(
        self,
        provider: BaseProvider,
        search: Search,
    ) -> list[Listing]:
        # asyncio.wait_for ждёт завершения отмены и само может зависнуть на
        # оборванном Playwright pipe после OOM. asyncio.wait даёт жёсткий лимит.
        task = asyncio.create_task(
            provider.search(search, limit=20),
            name=f"etsy-search-{search.id}",
        )
        done, _ = await asyncio.wait(
            {task},
            timeout=ETSY_SEARCH_TIMEOUT_SEC,
        )
        if task in done:
            return task.result()
        task.cancel()

        def consume_result(finished: asyncio.Task) -> None:
            try:
                finished.result()
            except BaseException:
                pass

        task.add_done_callback(consume_result)
        raise TimeoutError

    async def _restart_etsy_browser(self, *, reason: str) -> None:
        try:
            from bot.providers.etsy_browser import restart_browser

            await asyncio.wait_for(
                restart_browser(reason=reason),
                timeout=ETSY_BROWSER_RESTART_TIMEOUT_SEC,
            )
        except TimeoutError:
            logger.error(
                "Etsy browser restart timed out after %ss",
                ETSY_BROWSER_RESTART_TIMEOUT_SEC,
            )
        except Exception:
            logger.exception("Etsy browser restart failed")

    async def send_etsy_captcha_link(
        self,
        telegram_id: int,
        *,
        force: bool = True,
    ) -> str | None:
        """Отправить ссылку CAPTCHA конкретному пользователю. Возвращает URL или None."""
        if not self.etsy_vnc_access:
            logger.error("Etsy CAPTCHA link requested but ETSY_NOVNC_TOKEN is not configured")
            return None
        url = self.etsy_vnc_access.create_ticket_url()
        try:
            await self.bot.send_message(
                telegram_id,
                "Etsy: откройте браузер, пройдите проверку и закройте вкладку. "
                "Ссылка действует ограниченное время:",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🔐 Пройти CAPTCHA Etsy",
                                url=url,
                            )
                        ]
                    ]
                ),
            )
            if force:
                self._etsy_captcha_notified_at = time.monotonic()
            return url
        except Exception:
            logger.exception(
                "Failed to send Etsy CAPTCHA link to user=%s",
                telegram_id,
            )
            return None

    async def _notify_etsy_captcha(
        self,
        *,
        extra_ids: set[int] | None = None,
        force: bool = False,
    ) -> None:
        if not self.etsy_vnc_access:
            logger.error(
                "Etsy CAPTCHA detected but ETSY_NOVNC_TOKEN is empty — link not sent"
            )
            return
        recipients = set(self.etsy_captcha_notify_ids)
        if extra_ids:
            recipients |= {int(item) for item in extra_ids if item}
        if not recipients:
            logger.error("Etsy CAPTCHA detected but no recipients configured")
            return
        now = time.monotonic()
        if (
            not force
            and self._etsy_captcha_notified_at is not None
            and now - self._etsy_captcha_notified_at
            < ETSY_CAPTCHA_NOTIFICATION_COOLDOWN_SEC
        ):
            logger.info(
                "Etsy CAPTCHA notification skipped (cooldown %.0fs left)",
                ETSY_CAPTCHA_NOTIFICATION_COOLDOWN_SEC
                - (now - self._etsy_captcha_notified_at),
            )
            return
        sent = False
        for telegram_id in sorted(recipients):
            try:
                await self.bot.send_message(
                    telegram_id,
                    "Etsy запросил проверку. Ссылка действует ограниченное время:",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="🔐 Пройти CAPTCHA Etsy",
                                    url=self.etsy_vnc_access.create_ticket_url(),
                                )
                            ]
                        ]
                    ),
                )
                sent = True
            except Exception:
                logger.exception(
                    "Failed to send Etsy CAPTCHA link to user=%s",
                    telegram_id,
                )
        if sent:
            self._etsy_captcha_notified_at = now
            logger.info(
                "Etsy CAPTCHA link sent to %s recipient(s)",
                len(recipients),
            )
        else:
            logger.error("Etsy CAPTCHA link was not delivered to any recipient")

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
        if search.source in {Source.EBAY_PARSER, Source.POSHMARK}:
            provider = self._public_providers.get(search.source)
            if provider is None:
                provider = get_provider(search.source, proxy=self.proxy or None)
                self._public_providers[search.source] = provider
            return provider
        return get_provider(search.source, proxy=self.proxy or None)
