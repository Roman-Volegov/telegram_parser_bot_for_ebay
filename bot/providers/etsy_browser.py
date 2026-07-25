from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()
_playwright: Any = None
_browser: Any = None


async def _get_browser(*, proxy: str | None = None):
    global _playwright, _browser
    async with _lock:
        if _browser is not None and _browser.is_connected():
            return _browser
        from playwright.async_api import async_playwright

        if _playwright is None:
            _playwright = await async_playwright().start()
        launch_kwargs: dict[str, Any] = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
            "ignore_default_args": ["--enable-automation"],
        }
        if proxy:
            launch_kwargs["proxy"] = {"server": proxy}
        _browser = await _playwright.chromium.launch(**launch_kwargs)
        logger.info("Playwright Chromium started for Etsy")
        return _browser


async def fetch_search_html(url: str, *, proxy: str | None = None) -> str:
    """Открывает поиск Etsy в headless Chromium и возвращает HTML."""
    from bot.providers.http_utils import USER_AGENT

    browser = await _get_browser(proxy=proxy)
    context = await browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1366, "height": 900},
        locale="en-US",
        timezone_id="America/New_York",
    )
    try:
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        deadline = asyncio.get_running_loop().time() + 40
        html = await page.content()
        while asyncio.get_running_loop().time() < deadline:
            if "/listing/" in html:
                break
            await page.wait_for_timeout(1500)
            html = await page.content()
        return html
    finally:
        await context.close()


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
