from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()
_navigation_lock = asyncio.Lock()
_playwright: Any = None
_context: Any = None
_page: Any = None


def _headless() -> bool:
    return os.getenv("ETSY_BROWSER_HEADLESS", "true").strip().lower() not in {
        "0",
        "false",
        "no",
    }


async def _get_context(*, proxy: str | None = None):
    global _playwright, _context, _page
    async with _lock:
        if _context is not None:
            return _context
        from playwright.async_api import async_playwright

        if _playwright is None:
            _playwright = await async_playwright().start()
        profile_dir = Path(
            os.getenv("ETSY_BROWSER_PROFILE_DIR", "/app/data/etsy-browser-profile")
        )
        profile_dir.mkdir(parents=True, exist_ok=True)
        launch_kwargs: dict[str, Any] = {
            "headless": _headless(),
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
            "ignore_default_args": ["--enable-automation"],
            "locale": "en-US",
            "timezone_id": "America/New_York",
            "viewport": {"width": 1366, "height": 900},
        }
        if proxy:
            launch_kwargs["proxy"] = {"server": proxy}
        _context = await _playwright.chromium.launch_persistent_context(
            str(profile_dir),
            **launch_kwargs,
        )
        await _context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        _page = _context.pages[0] if _context.pages else await _context.new_page()
        logger.info(
            "Persistent Playwright Chromium started for Etsy (profile=%s, headless=%s)",
            profile_dir,
            _headless(),
        )
        return _context


async def fetch_search_html(url: str, *, proxy: str | None = None) -> str:
    """Открывает Etsy в постоянном профиле и возвращает HTML."""
    global _page
    context = await _get_context(proxy=proxy)
    async with _navigation_lock:
        if _page is None or _page.is_closed():
            _page = await context.new_page()
        page = _page
        await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        deadline = asyncio.get_running_loop().time() + 40
        html = await page.content()
        while asyncio.get_running_loop().time() < deadline:
            if "/listing/" in html:
                break
            await page.wait_for_timeout(1500)
            html = await page.content()
        if any("captcha-delivery.com" in frame.url for frame in page.frames):
            html += "\n<!-- DATADOME_CHALLENGE -->"
        return html


async def close_browser() -> None:
    global _playwright, _context, _page
    async with _lock:
        if _context is not None:
            try:
                await _context.close()
            except Exception:
                logger.debug("Playwright browser close failed", exc_info=True)
            _context = None
            _page = None
        if _playwright is not None:
            try:
                await _playwright.stop()
            except Exception:
                logger.debug("Playwright stop failed", exc_info=True)
            _playwright = None
