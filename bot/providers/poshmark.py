from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import replace
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from bot.models import Listing, Search, Source
from bot.providers.base import BaseProvider, ProviderError
from bot.providers.http_utils import (
    build_client,
    parse_price_text,
    request_with_retries,
    truncate,
)
from bot.providers.listing_meta import parse_shipping_info

logger = logging.getLogger(__name__)
SHIPPING_CACHE_TTL_SEC = 24 * 60 * 60
_SHIPPING_CACHE: dict[str, tuple[float, float | None, str | None, bool]] = {}


class PoshmarkProvider(BaseProvider):
    source = Source.POSHMARK

    def __init__(self, *, proxy: str | None = None) -> None:
        self._client = build_client(proxy)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(self, search: Search, *, limit: int = 20) -> list[Listing]:
        if len(_SHIPPING_CACHE) > 2000:
            now = time.monotonic()
            expired = [key for key, value in _SHIPPING_CACHE.items() if value[0] <= now]
            for key in expired:
                _SHIPPING_CACHE.pop(key, None)
        query = search.keywords.strip()
        if not query:
            return []
        url = (
            f"https://poshmark.com/search?query={quote_plus(query)}"
            f"&type=listings&src=dir"
        )
        try:
            response = await request_with_retries(
                self._client,
                "GET",
                url,
                min_delay=2.5,
                max_delay=5.0,
            )
            response.raise_for_status()
        except Exception as exc:
            raise ProviderError(f"Poshmark request failed: {exc}") from exc

        soup = BeautifulSoup(response.text, "lxml")
        listings: list[Listing] = []
        seen: set[str] = set()

        for anchor in soup.select("a[href*='/listing/']"):
            if len(listings) >= max(limit * 2, 40):
                break
            href = anchor.get("href") or ""
            item_id = _extract_poshmark_id(href)
            if not item_id or item_id in seen:
                continue
            title = _extract_title(anchor)
            if not title:
                continue
            price, currency = _extract_price_near(anchor)
            if search.min_price is not None and price is not None and price < search.min_price:
                continue
            if search.max_price is not None and price is not None and price > search.max_price:
                continue
            image_url = _extract_image(anchor)
            ship_cost, ship_cur, ship_free = _extract_shipping_near(anchor)
            absolute = href if href.startswith("http") else f"https://poshmark.com{href}"
            seen.add(item_id)
            listings.append(
                Listing(
                    id=item_id,
                    title=title,
                    description=truncate(title, 450),
                    price=price,
                    currency=currency or "USD",
                    image_url=image_url,
                    item_url=absolute.split("?")[0],
                    source=Source.POSHMARK,
                    shipping_cost=ship_cost,
                    shipping_currency=ship_cur or currency or "USD",
                    shipping_free=ship_free,
                )
            )
            if len(listings) >= limit:
                break

        if not listings:
            logger.warning("Poshmark returned 0 items for %r", query)
            return []

        # В выдаче поиска доставки нет — подтягиваем со страницы лота
        sem = asyncio.Semaphore(5)

        async def enrich(item: Listing) -> Listing:
            async with sem:
                return await self._enrich_shipping(item)

        enriched = await asyncio.gather(*[enrich(item) for item in listings[:limit]])
        return list(enriched)

    async def _enrich_shipping(self, listing: Listing) -> Listing:
        if listing.shipping_free or listing.shipping_cost is not None:
            return listing
        cached = _SHIPPING_CACHE.get(listing.id)
        if cached and cached[0] > time.monotonic():
            _, cost, currency, is_free = cached
            return replace(
                listing,
                shipping_cost=cost,
                shipping_currency=currency or listing.shipping_currency or listing.currency,
                shipping_free=is_free,
            )
        try:
            response = await self._client.get(
                listing.item_url,
                headers={"Referer": "https://poshmark.com/"},
            )
            if response.status_code >= 400:
                return listing
            cost, currency, is_free = _extract_shipping_from_detail(response.text)
            _SHIPPING_CACHE[listing.id] = (
                time.monotonic() + SHIPPING_CACHE_TTL_SEC,
                cost,
                currency,
                is_free,
            )
            if not is_free and cost is None:
                return listing
            return replace(
                listing,
                shipping_cost=cost,
                shipping_currency=currency or listing.shipping_currency or listing.currency,
                shipping_free=is_free,
            )
        except Exception as exc:
            logger.debug("Poshmark shipping enrich failed for %s: %s", listing.id, exc)
            return listing


def _extract_poshmark_id(href: str) -> str | None:
    match = re.search(r"/listing/[^/]+-([a-f0-9]{24})", href)
    if match:
        return match.group(1)
    match = re.search(r"/listing/([a-f0-9]{24})", href)
    return match.group(1) if match else None


def _extract_title(anchor) -> str | None:
    image = anchor.select_one("img")
    if image and image.get("alt"):
        alt = str(image.get("alt")).strip()
        if alt:
            return alt[:200]
    text = anchor.get_text(" ", strip=True)
    if text and len(text) > 2:
        return text[:200]
    parent = anchor.parent
    if parent is not None:
        title_el = parent.select_one(
            ".tile__title, .title, [data-test='tile-title'], h1, h2, h3"
        )
        if title_el:
            value = title_el.get_text(" ", strip=True)
            if value:
                return value[:200]
    return None


def _extract_price_near(anchor) -> tuple[float | None, str | None]:
    # Предпочитаем текущую цену в карточке, а не весь текст (там бывает original)
    for sel in (
        ".tile-grid-redesign__price-current",
        ".tile__price",
        "[class*='price-current']",
    ):
        el = None
        node = anchor
        for _ in range(5):
            if node is None:
                break
            el = node.select_one(sel)
            if el:
                break
            node = node.parent
        if el:
            price, currency = parse_price_text(el.get_text(" ", strip=True))
            if price is not None:
                return price, currency
    container = anchor
    for _ in range(4):
        if container is None:
            break
        text = container.get_text(" ", strip=True)
        price, currency = parse_price_text(text)
        if price is not None:
            return price, currency
        container = container.parent
    return None, None


def _extract_image(anchor) -> str | None:
    image = anchor.select_one("img")
    if not image:
        return None
    return image.get("src") or image.get("data-src")


def _extract_shipping_near(anchor) -> tuple[float | None, str | None, bool]:
    container = anchor
    for _ in range(5):
        if container is None:
            break
        text = container.get_text(" ", strip=True)
        lower = text.lower()
        if "ship" in lower or "достав" in lower:
            cost, currency, is_free = parse_shipping_info(text)
            if is_free or cost is not None:
                return cost, currency, is_free
        container = container.parent
    return None, None, False


def _extract_shipping_from_detail(html: str) -> tuple[float | None, str | None, bool]:
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    # Явные короткие подписи вроде "$6.49 Shipping"
    for node in soup.find_all(string=re.compile(r"\$\s*\d", re.I)):
        text = (node.parent.get_text(" ", strip=True) if node.parent else str(node)).strip()
        if len(text) > 80:
            continue
        if "ship" not in text.lower():
            continue
        cost, currency, is_free = parse_shipping_info(text)
        if is_free or cost is not None:
            return cost, currency, is_free

    for node in soup.find_all(string=re.compile(r"ship", re.I)):
        text = (node.parent.get_text(" ", strip=True) if node.parent else str(node)).strip()
        if len(text) > 80:
            continue
        cost, currency, is_free = parse_shipping_info(text)
        if is_free or cost is not None:
            return cost, currency, is_free

    # fallback по HTML без скриптов
    cleaned = soup.get_text(" ", strip=True)
    for pattern in (
        r"Buyer pays\s*\$?\s*(\d+(?:\.\d+)?)\s*standard shipping",
        r"\$(\d+(?:\.\d+)?)\s*Shipping",
    ):
        match = re.search(pattern, cleaned, re.I)
        if match:
            try:
                amount = float(match.group(1))
            except (TypeError, ValueError):
                continue
            return amount, "USD", amount == 0.0

    if re.search(r"\bfree shipping\b", cleaned, re.I):
        return 0.0, "USD", True
    return None, None, False
