from __future__ import annotations

import asyncio
import logging
import sys

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import TelegramObject

from bot.config import get_settings
from bot.db import Database
from bot.handlers import router
from bot.middlewares import AccessMiddleware
from bot.watcher import WatcherService


class InjectMiddleware(BaseMiddleware):
    """Прокидывает зависимости в хендлеры через data."""

    def __init__(self, **deps: Any) -> None:
        self.deps = deps

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data.update(self.deps)
        return await handler(event, data)


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    logger = logging.getLogger("bot")

    db = Database(settings.database_path)
    await db.connect()

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.middleware(AccessMiddleware(settings.allowed_ids))
    dp.update.middleware(InjectMiddleware(db=db, settings=settings))
    dp.include_router(router)

    watcher = WatcherService(
        bot,
        db,
        interval_seconds=settings.watch_poll_interval_seconds,
        ebay_app_id=settings.ebay_app_id,
        ebay_cert_id=settings.ebay_cert_id,
        ebay_marketplace_id=settings.ebay_marketplace_id,
    )
    watcher.start()

    logger.info(
        "Starting bot (ebay_api=%s, poll=%ss)",
        settings.ebay_api_enabled,
        settings.watch_poll_interval_seconds,
    )
    try:
        await dp.start_polling(bot)
    finally:
        await watcher.stop()
        await db.close()
        await bot.session.close()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
