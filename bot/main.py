from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage

from bot.config import get_settings
from bot.crypto import CredentialsCrypto
from bot.db import Database
from bot.handlers import build_root_router
from bot.menu import setup_bot_commands, webapp_url_from_base
from bot.middlewares import AccessMiddleware, InjectMiddleware
from bot.services.cleanup import CleanupService
from bot.services.credentials import CredentialsService
from bot.services.etsy_access import EtsyVncAccess
from bot.services.poller import PollerService
from bot.services.taxonomies import TaxonomyService
from bot.web.app import create_app


async def main() -> None:
    settings = get_settings()
    if not settings.admin_ids:
        raise RuntimeError("ADMIN_TELEGRAM_IDS must contain at least one administrator")
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
    etsy_vnc_access = (
        EtsyVncAccess(
            settings.public_base_url,
            settings.etsy_novnc_token,
            ttl_sec=settings.etsy_novnc_ttl_sec,
        )
        if settings.etsy_novnc_token
        else None
    )

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    bot_info = await bot.get_me()
    storage = (
        RedisStorage.from_url(settings.redis_url)
        if settings.redis_url
        else MemoryStorage()
    )
    dp = Dispatcher(storage=storage)
    poller = PollerService(
        bot,
        db,
        credentials,
        interval_sec=settings.poll_interval_sec,
        proxy=settings.http_proxy or None,
        etsy_vnc_access=etsy_vnc_access,
        etsy_captcha_notify_ids=settings.admin_ids,
    )
    cleanup = CleanupService(db, ttl_days=settings.seen_items_ttl_days)
    taxonomy_dir = Path(settings.database_path).resolve().parent / "taxonomies"
    taxonomies = TaxonomyService(
        taxonomy_dir,
        proxy=settings.http_proxy or None,
    )

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
        credentials=credentials,
        bot_username=bot_info.username or "",
        poller=poller,
        http_proxy=settings.http_proxy,
        etsy_vnc_access=etsy_vnc_access,
        taxonomies=taxonomies,
    )
    config = uvicorn.Config(
        app,
        host=settings.web_host,
        port=settings.web_port,
        log_level=settings.log_level.lower(),
        loop="asyncio",
        proxy_headers=True,
        forwarded_allow_ips="*",
        access_log=False,
    )
    server = uvicorn.Server(config)

    poller.start()
    cleanup.start()
    taxonomies.start()
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
        await taxonomies.aclose()
        try:
            from bot.providers.etsy_browser import close_browser

            await close_browser()
        except Exception:
            logger.debug("Etsy browser shutdown skipped", exc_info=True)
        await db.close()
        await dp.storage.close()
        await bot.session.close()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
