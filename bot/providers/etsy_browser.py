from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()
_playwright: Any = None
_browser: Any = None


async def get_browser():
    """Ленивый singleton Chromium для Etsy (Playwright)."""
    global _playwright, _browser
    async with _lock:
        if _browser is not None and _browser.is_connected():
            return _browser
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright не установлен. Добавьте пакет playwright в образ."
            ) from exc

        if _playwright is None:
            _playwright = await async_playwright().start()
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ]
        _browser = await _playwright.chromium.launch(
            headless=True,
            args=launch_args,
        )
        logger.info("Playwright Chromium started for Etsy")
        return _browser


async def close_browser() -> None:
    global _playwright, _browser
    async with _lock:
        if _browser is not None:
            try:
                await _browser.close()
            except Exception:
                logger.debug("Playwright browser close failed", exc_info=True)
            _browser = None
        if _playwright is not None:
            try:
                await _playwright.stop()
            except Exception:
                logger.debug("Playwright stop failed", exc_info=True)
            _playwright = None
