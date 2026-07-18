from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import get_settings
from bot.crypto import CredentialsCrypto
from bot.db import Database
from bot.handlers import build_root_router
from bot.menu import setup_bot_commands, webapp_url_from_base
from bot.middlewares import AccessMiddleware, InjectMiddleware
from bot.services.cleanup import CleanupService
from bot.services.credentials import CredentialsService
from bot.services.poller import PollerService
from bot.web.app import create_app


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

    crypto = CredentialsCrypto(settings.credentials_encryption_key)
    credentials = CredentialsService(db, crypto)

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    poller = PollerService(
        bot,
        db,
        credentials,
        interval_sec=settings.poll_interval_sec,
        proxy=settings.http_proxy or None,
    )
    cleanup = CleanupService(db, ttl_days=settings.seen_items_ttl_days)

    dp.update.middleware(InjectMiddleware(
        db=db,
        settings=settings,
        credentials=credentials,
        poller=poller,
    ))
    dp.update.middleware(AccessMiddleware(settings.admin_ids))
    dp.include_router(build_root_router())

    app = create_app(
        db,
        settings.public_base_url,
        bot_token=settings.telegram_bot_token,
        poller=poller,
    )
    config = uvicorn.Config(
        app,
        host=settings.web_host,
        port=settings.web_port,
        log_level=settings.log_level.lower(),
        loop="asyncio",
    )
    server = uvicorn.Server(config)

    poller.start()
    cleanup.start()
    webapp_url = webapp_url_from_base(settings.public_base_url)
    await setup_bot_commands(bot, settings.admin_ids, webapp_url=webapp_url)
    logger.info(
        "Starting bot+web (poll=%ss, web=%s:%s, miniapp=%s)",
        settings.poll_interval_sec,
        settings.web_host,
        settings.web_port,
        webapp_url,
    )

    try:
        await asyncio.gather(
            dp.start_polling(bot),
            server.serve(),
        )
    finally:
        await poller.stop()
        await cleanup.stop()
        await db.close()
        await bot.session.close()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
