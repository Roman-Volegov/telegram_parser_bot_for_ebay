from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()
_playwright: Any = None
_pw_browser: Any = None


async def _fetch_with_camoufox(url: str, *, proxy: str | None) -> str:
    from camoufox.async_api import AsyncCamoufox

    kwargs: dict[str, Any] = {
        "headless": True,
        "humanize": True,
    }
    if proxy:
        kwargs["proxy"] = {"server": proxy}
        kwargs["geoip"] = True

    async with AsyncCamoufox(**kwargs) as browser:
        page = await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        deadline = asyncio.get_running_loop().time() + 40
        html = await page.content()
        while asyncio.get_running_loop().time() < deadline:
            if "/listing/" in html:
                break
            await page.wait_for_timeout(1500)
            html = await page.content()
        return html


async def _get_playwright_browser(*, proxy: str | None = None):
    global _playwright, _pw_browser
    async with _lock:
        if _pw_browser is not None and _pw_browser.is_connected():
            return _pw_browser
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
        _pw_browser = await _playwright.chromium.launch(**launch_kwargs)
        logger.info("Playwright Chromium started for Etsy")
        return _pw_browser


async def _fetch_with_playwright(url: str, *, proxy: str | None) -> str:
    from bot.providers.http_utils import USER_AGENT

    browser = await _get_playwright_browser(proxy=proxy)
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


async def fetch_search_html(url: str, *, proxy: str | None = None) -> str:
    """Camoufox first, then Playwright Chromium."""
    try:
        html = await _fetch_with_camoufox(url, proxy=proxy)
        logger.info("Etsy fetched via Camoufox (%s bytes)", len(html))
        return html
    except Exception as exc:
        logger.warning("Camoufox failed (%s), trying Playwright", exc)

    html = await _fetch_with_playwright(url, proxy=proxy)
    logger.info("Etsy fetched via Playwright (%s bytes)", len(html))
    return html


async def close_browser() -> None:
    global _playwright, _pw_browser
    async with _lock:
        if _pw_browser is not None:
            try:
                await _pw_browser.close()
            except Exception:
                logger.debug("Playwright browser close failed", exc_info=True)
            _pw_browser = None
        if _playwright is not None:
            try:
                await _playwright.stop()
            except Exception:
                logger.debug("Playwright stop failed", exc_info=True)
            _playwright = None
