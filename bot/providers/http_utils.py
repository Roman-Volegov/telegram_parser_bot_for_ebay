from __future__ import annotations

import asyncio
import logging
import random
import re

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def build_client(proxy: str | None = None, *, timeout: float = 30.0) -> httpx.AsyncClient:
    kwargs: dict = {
        "timeout": timeout,
        "headers": {
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        },
        "follow_redirects": True,
    }
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.AsyncClient(**kwargs)


async def request_with_retries(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    retries: int = 3,
    min_delay: float = 2.0,
    max_delay: float = 5.0,
    **kwargs,
) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            await asyncio.sleep(random.uniform(min_delay, max_delay))
            response = await client.request(method, url, **kwargs)
            if response.status_code in {429, 503}:
                wait = 2 ** attempt + random.uniform(0.5, 1.5)
                logger.warning(
                    "HTTP %s for %s, retry in %.1fs",
                    response.status_code,
                    url,
                    wait,
                )
                await asyncio.sleep(wait)
                continue
            return response
        except httpx.HTTPError as exc:
            last_exc = exc
            wait = 2 ** attempt + random.uniform(0.5, 1.5)
            logger.warning("HTTP error %s, retry in %.1fs", exc, wait)
            await asyncio.sleep(wait)
    if last_exc:
        raise last_exc
    raise RuntimeError(f"Failed request to {url}")


def parse_price_text(text: str) -> tuple[float | None, str | None]:
    if not text:
        return None, None
    currency = None
    upper = text.upper()
    if "US $" in text or text.strip().startswith("$") or "USD" in upper:
        currency = "USD"
    elif "€" in text or "EUR" in upper:
        currency = "EUR"
    elif "£" in text or "GBP" in upper:
        currency = "GBP"
    first = re.split(r"\bto\b|-", text, maxsplit=1)[0]
    match = re.search(r"(\d+[.,]\d+|\d+)", first.replace(",", ""))
    if not match:
        return None, currency
    return float(match.group(1).replace(",", ".")), currency


def truncate(text: str, limit: int = 400) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
