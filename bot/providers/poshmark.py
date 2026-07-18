from __future__ import annotations

import logging
import re
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


class PoshmarkProvider(BaseProvider):
    source = Source.POSHMARK

    def __init__(self, *, proxy: str | None = None) -> None:
        self._client = build_client(proxy)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(self, search: Search, *, limit: int = 20) -> list[Listing]:
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
        return listings[:limit]


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
