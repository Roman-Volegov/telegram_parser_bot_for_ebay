from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()
_navigation_lock = asyncio.Lock()
_playwright: Any = None
_context: Any = None
_page: Any = None
_context_proxy: str | None = None
_html_cache: dict[str, tuple[float, str]] = {}
_idle_close_task: asyncio.Task[None] | None = None
HTML_CACHE_TTL_SEC = 60
DEFAULT_IDLE_CLOSE_SEC = 90


class EtsyBrowserError(RuntimeError):
    """Ошибка браузера Etsy (профиль/запуск) — нужна ручная проверка."""

    def __init__(self, message: str, *, needs_human: bool = False) -> None:
        super().__init__(message)
        self.needs_human = needs_human


def _headless() -> bool:
    return os.getenv("ETSY_BROWSER_HEADLESS", "true").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def _profile_dir() -> Path:
    return Path(
        os.getenv("ETSY_BROWSER_PROFILE_DIR", "/app/data/etsy-browser-profile")
    )


def _idle_close_sec() -> int:
    try:
        return max(30, int(os.getenv("ETSY_BROWSER_IDLE_CLOSE_SEC", DEFAULT_IDLE_CLOSE_SEC)))
    except ValueError:
        return DEFAULT_IDLE_CLOSE_SEC


def _clear_profile_locks(profile_dir: Path) -> None:
    for name in (
        "SingletonLock",
        "SingletonCookie",
        "SingletonSocket",
        "lockfile",
    ):
        path = profile_dir / name
        try:
            if path.exists() or path.is_symlink():
                path.unlink()
                logger.info("Removed stale Chromium lock %s", path)
        except OSError:
            logger.debug("Failed to remove Chromium lock %s", path, exc_info=True)


def _is_profile_busy_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return (
        "opening in existing browser session" in text
        or "singletonlock" in text
        or "profile is already in use" in text
    )


def _is_browser_failure_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "page crashed",
            "target page, context or browser has been closed",
            "browser has been closed",
            "browser closed",
            "connection closed",
            "connection is closed",
        )
    )


def _purge_expired_cache(now: float) -> None:
    expired = [url for url, (expires_at, _) in _html_cache.items() if expires_at <= now]
    for url in expired:
        _html_cache.pop(url, None)


def _playwright_process_ids() -> list[int]:
    """PID процессов Chrome/Playwright внутри текущего контейнера."""
    own_pid = os.getpid()
    result: list[int] = []
    proc = Path("/proc")
    if not proc.exists():
        return result
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == own_pid:
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", errors="ignore"
            )
        except OSError:
            continue
        if (
            "/ms-playwright/" in cmdline
            or "playwright/driver/node" in cmdline
        ):
            result.append(pid)
    return result


async def _terminate_playwright_processes() -> None:
    pids = _playwright_process_ids()
    if not pids:
        return
    logger.warning("Terminating stale Etsy browser processes: %s", pids)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    await asyncio.sleep(0.5)
    for pid in _playwright_process_ids():
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


async def _get_context(*, proxy: str | None = None):
    global _playwright, _context, _page, _context_proxy
    async with _lock:
        if _context is not None:
            browser = _context.browser
            if (
                browser is not None
                and browser.is_connected()
                and _context_proxy == proxy
            ):
                return _context
            try:
                await asyncio.wait_for(_context.close(), timeout=5)
            except Exception:
                logger.debug("Stale Etsy browser close failed", exc_info=True)
            _context = None
            _page = None
        from playwright.async_api import async_playwright

        if _playwright is None:
            _playwright = await async_playwright().start()
        profile_dir = _profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)
        launch_kwargs: dict[str, Any] = {
            "headless": _headless(),
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--renderer-process-limit=2",
            ],
            "ignore_default_args": ["--enable-automation"],
            "locale": "en-US",
            "timezone_id": "America/New_York",
            "viewport": {"width": 1366, "height": 900},
        }
        if proxy:
            launch_kwargs["proxy"] = {"server": proxy}

        last_error: BaseException | None = None
        for attempt in range(2):
            try:
                _context = await _playwright.chromium.launch_persistent_context(
                    str(profile_dir),
                    **launch_kwargs,
                )
                break
            except Exception as exc:
                last_error = exc
                if attempt == 0 and _is_profile_busy_error(exc):
                    logger.warning(
                        "Etsy browser profile locked; clearing Singleton* and retrying"
                    )
                    _clear_profile_locks(profile_dir)
                    await asyncio.sleep(0.5)
                    continue
                raise EtsyBrowserError(
                    f"Не удалось запустить браузер Etsy: {exc}",
                    needs_human=_is_profile_busy_error(exc),
                ) from exc
        else:
            raise EtsyBrowserError(
                f"Не удалось запустить браузер Etsy: {last_error}",
                needs_human=True,
            )

        _context_proxy = proxy
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
    for attempt in range(2):
        try:
            html = await _fetch_search_html_once(url, proxy=proxy)
            _schedule_idle_close()
            return html
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if attempt == 0 and _is_browser_failure_error(exc):
                logger.warning(
                    "Etsy browser crashed during navigation; restarting",
                    exc_info=True,
                )
                await restart_browser(reason=str(exc)[:200])
                continue
            raise
    raise EtsyBrowserError("Браузер Etsy не восстановился после перезапуска")


def _schedule_idle_close() -> None:
    global _idle_close_task
    if _idle_close_task is not None:
        _idle_close_task.cancel()
    _idle_close_task = asyncio.create_task(
        _close_browser_after_idle(),
        name="etsy-browser-idle-close",
    )


async def _close_browser_after_idle() -> None:
    global _idle_close_task
    current = asyncio.current_task()
    try:
        await asyncio.sleep(_idle_close_sec())
        await close_browser_if_no_captcha(reason="Etsy browser idle timeout")
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Failed to close idle Etsy browser")
    finally:
        if _idle_close_task is current:
            _idle_close_task = None


async def _fetch_search_html_once(url: str, *, proxy: str | None = None) -> str:
    global _page
    now = asyncio.get_running_loop().time()
    _purge_expired_cache(now)
    cached = _html_cache.get(url)
    if cached and cached[0] > now:
        return cached[1]
    context = await _get_context(proxy=proxy)
    async with _navigation_lock:
        cached = _html_cache.get(url)
        if cached and cached[0] > asyncio.get_running_loop().time():
            return cached[1]
        if _page is None or _page.is_closed():
            _page = await context.new_page()
        page = _page
        await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        try:
            await page.wait_for_selector('a[href*="/listing/"]', timeout=40_000)
        except Exception:
            # CAPTCHA/пустая выдача обрабатываются вызывающим кодом по HTML.
            pass
        html = await page.content()
        if any("captcha-delivery.com" in frame.url for frame in page.frames):
            html += "\n<!-- DATADOME_CHALLENGE -->"
        elif "/listing/" in html:
            _html_cache[url] = (
                asyncio.get_running_loop().time() + HTML_CACHE_TTL_SEC,
                html,
            )
        return html


async def restart_browser(*, reason: str = "") -> None:
    global _playwright, _context, _page, _context_proxy, _idle_close_task
    if reason:
        logger.warning("Restarting Etsy browser: %s", reason)
    idle_task = _idle_close_task
    _idle_close_task = None
    if idle_task is not None and idle_task is not asyncio.current_task():
        idle_task.cancel()
    async with _lock:
        context = _context
        playwright = _playwright
        # Сначала убрать ссылки на повреждённый браузер, чтобы следующий
        # запрос не переиспользовал контекст после OOM/краша renderer.
        _context = None
        _page = None
        _context_proxy = None
        _playwright = None
        _html_cache.clear()
        graceful = True
        if context is not None:
            try:
                await asyncio.wait_for(context.close(), timeout=5)
            except Exception:
                logger.debug("Playwright browser close failed", exc_info=True)
                graceful = False
        if playwright is not None:
            try:
                await asyncio.wait_for(playwright.stop(), timeout=5)
            except Exception:
                logger.debug("Playwright stop failed", exc_info=True)
                graceful = False
        # После OOM driver может считать browser connected, хотя renderer уже
        # убит. Удаляем оставшиеся процессы и lock-файлы профиля.
        if not graceful or _playwright_process_ids():
            await _terminate_playwright_processes()
        _clear_profile_locks(_profile_dir())
    logger.info("Etsy browser reset completed")


async def close_browser_if_no_captcha(*, reason: str) -> bool:
    """Освобождает Chromium после фоновой работы, не закрывая открытую CAPTCHA."""
    async with _navigation_lock:
        page = _page
        if page is not None and not page.is_closed():
            try:
                urls = [page.url, *(frame.url for frame in page.frames)]
                if any(
                    "captcha" in url.lower() or "challenge" in url.lower()
                    for url in urls
                ):
                    logger.info(
                        "Keeping Etsy browser open because CAPTCHA is active"
                    )
                    return False
            except Exception:
                logger.debug("Failed to inspect Etsy page before close", exc_info=True)
        await restart_browser(reason=reason)
        return True


async def close_browser() -> None:
    await restart_browser(reason="application shutdown")
