from __future__ import annotations

import logging
import re
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from bot.models import Listing, Marketplace
from bot.parsers.base import BaseParser, ParserError

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class PoshmarkParser(BaseParser):
    marketplace = Marketplace.POSHMARK

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(
        self,
        query: str,
        *,
        min_price: float | None = None,
        max_price: float | None = None,
        limit: int = 20,
    ) -> list[Listing]:
        query = query.strip()
        if not query:
            return []
        try:
            listings = await self._search_html(query, limit=max(limit * 2, 40))
        except httpx.HTTPError as exc:
            raise ParserError(f"Poshmark request failed: {exc}") from exc

        filtered: list[Listing] = []
        for listing in listings:
            if listing.price is not None:
                if min_price is not None and listing.price < min_price:
                    continue
                if max_price is not None and listing.price > max_price:
                    continue
            filtered.append(listing)
            if len(filtered) >= limit:
                break
        return filtered

    async def _search_html(self, query: str, *, limit: int) -> list[Listing]:
        url = f"https://poshmark.com/search?query={quote_plus(query)}&type=listings&src=dir"
        response = await self._client.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")

        cards = soup.select(
            "div.tile, div.card, div[data-test='tile'], a.tile__covershot"
        )
        listings: list[Listing] = []
        seen_ids: set[str] = set()

        # Prefer listing anchors that look like /listing/<slug>-<id>
        anchors = soup.select("a[href*='/listing/']")
        for anchor in anchors:
            if len(listings) >= limit:
                break
            href = anchor.get("href") or ""
            external_id = _extract_poshmark_id(href)
            if not external_id or external_id in seen_ids:
                continue

            title = _extract_title(anchor)
            if not title:
                continue

            price, currency = _extract_price_near(anchor)
            image_url = _extract_image(anchor)
            absolute_url = href if href.startswith("http") else f"https://poshmark.com{href}"

            seen_ids.add(external_id)
            listings.append(
                Listing(
                    marketplace=Marketplace.POSHMARK,
                    external_id=external_id,
                    title=title,
                    price=price,
                    currency=currency,
                    url=absolute_url.split("?")[0],
                    image_url=image_url,
                )
            )

        if not listings:
            logger.warning(
                "Poshmark HTML search returned 0 items for query=%r (cards=%s)",
                query,
                len(cards),
            )
        return listings


def _extract_poshmark_id(href: str) -> str | None:
    match = re.search(r"/listing/[^/]+-([a-f0-9]{24})", href)
    if match:
        return match.group(1)
    match = re.search(r"/listing/([a-f0-9]{24})", href)
    return match.group(1) if match else None


def _extract_title(anchor) -> str | None:
    for selector in ("img",):
        image = anchor.select_one(selector)
        if image and image.get("alt"):
            alt = image.get("alt", "").strip()
            if alt:
                return alt
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
        price, currency = _parse_price_text(text)
        if price is not None:
            return price, currency
        container = container.parent
    return None, None


def _extract_image(anchor) -> str | None:
    image = anchor.select_one("img")
    if not image:
        return None
    return image.get("src") or image.get("data-src")


def _parse_price_text(text: str) -> tuple[float | None, str | None]:
    match = re.search(r"\$(\d+(?:\.\d{1,2})?)", text)
    if not match:
        return None, None
    return float(match.group(1)), "USD"
