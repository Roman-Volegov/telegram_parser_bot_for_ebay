from __future__ import annotations

import logging
import re
from urllib.parse import urlencode

import feedparser
from bs4 import BeautifulSoup, Tag

from bot.models import Listing, Search, Source
from bot.providers.base import BaseProvider, ProviderError
from bot.providers.http_utils import (
    build_client,
    parse_price_text,
    request_with_retries,
    truncate,
)
from bot.providers.listing_meta import parse_ebay_html_listing_type, parse_shipping_info

logger = logging.getLogger(__name__)


class EbayParserProvider(BaseProvider):
    source = Source.EBAY_PARSER

    def __init__(self, *, proxy: str | None = None) -> None:
        self._client = build_client(proxy)
        self._warmed = False

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(self, search: Search, *, limit: int = 20) -> list[Listing]:
        try:
            await self._warmup()
            # eBay часто отдаёт HTML вместо RSS на _rss=1 — сначала HTML.
            listings = await self._search_html(search, limit=limit)
            if listings:
                return listings
            logger.info("eBay HTML empty for %r, trying RSS", search.keywords)
            return await self._search_rss(search, limit=limit)
        except Exception as exc:
            raise ProviderError(f"eBay parser failed: {exc}") from exc

    async def _warmup(self) -> None:
        if self._warmed:
            return
        try:
            response = await self._client.get(
                "https://www.ebay.com/",
                headers={"Referer": "https://www.google.com/"},
            )
            if response.status_code < 400:
                self._warmed = True
        except Exception as exc:
            logger.debug("eBay warmup failed: %s", exc)

    def _build_params(self, search: Search) -> dict[str, str]:
        params: dict[str, str] = {
            "_nkw": search.keywords,
            "_sop": "10",  # newly listed
        }
        if search.min_price is not None:
            params["_udlo"] = str(search.min_price)
        if search.max_price is not None:
            params["_udhi"] = str(search.max_price)
        if search.buy_it_now:
            params["LH_BIN"] = "1"
        if search.condition:
            # eBay condition IDs often passed as LH_ItemCondition
            params["LH_ItemCondition"] = search.condition
        return params

    async def _search_rss(self, search: Search, *, limit: int) -> list[Listing]:
        params = self._build_params(search)
        params["_rss"] = "1"
        url = f"https://www.ebay.com/sch/i.html?{urlencode(params)}"
        response = await request_with_retries(
            self._client,
            "GET",
            url,
            min_delay=1.0,
            max_delay=2.5,
            headers={
                "Accept": "application/rss+xml, application/xml, text/xml, */*;q=0.8",
                "Referer": "https://www.ebay.com/",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        response.raise_for_status()
        body = response.text or ""
        if "<rss" not in body.lower() and "<feed" not in body.lower():
            logger.info("eBay _rss=1 returned non-RSS for %r", search.keywords)
            return []
        feed = feedparser.parse(body)
        listings: list[Listing] = []
        for entry in feed.entries[:limit]:
            link = getattr(entry, "link", "") or ""
            title = getattr(entry, "title", "") or "Untitled"
            item_id = _extract_ebay_item_id(link)
            if not item_id:
                continue
            summary = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
            price, currency = _extract_price_from_summary(summary)
            image_url = _extract_image_from_summary(summary)
            plain = _strip_html(summary)
            ship_cost, ship_cur, ship_free = parse_shipping_info(plain)
            listing_type = parse_ebay_html_listing_type(plain)
            description = truncate(f"{title}. {plain}", 450)
            listings.append(
                Listing(
                    id=item_id,
                    title=title.strip(),
                    description=description,
                    price=price,
                    currency=currency,
                    image_url=image_url,
                    item_url=link.split("?")[0],
                    source=Source.EBAY_PARSER,
                    shipping_cost=ship_cost,
                    shipping_currency=ship_cur,
                    shipping_free=ship_free,
                    listing_type=listing_type,
                )
            )
        return listings

    async def _search_html(self, search: Search, *, limit: int) -> list[Listing]:
        params = self._build_params(search)
        params["_ipg"] = str(min(max(limit, 20), 60))
        url = f"https://www.ebay.com/sch/i.html?{urlencode(params)}"
        response = await request_with_retries(
            self._client,
            "GET",
            url,
            min_delay=1.0,
            max_delay=2.5,
            headers={
                "Referer": "https://www.ebay.com/",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        response.raise_for_status()
        return _parse_html_listings(response.text, limit=limit)


def _parse_html_listings(html: str, *, limit: int) -> list[Listing]:
    soup = BeautifulSoup(html, "lxml")
    listings: list[Listing] = []
    seen_ids: set[str] = set()
    # Новая выдача eBay: li.s-card; старая: li.s-item
    cards = soup.select("li.s-card, li.s-item")
    for item in cards:
        if len(listings) >= limit:
            break
        link = item.select_one(
            "a.s-card__link[href*='/itm/'], a.s-item__link[href*='/itm/'], a[href*='/itm/']"
        )
        title_el = item.select_one(
            ".s-card__title .su-styled-text, .s-card__title, .s-item__title"
        )
        if not link or not title_el:
            continue
        href = link.get("href") or ""
        title = _clean_ebay_title(title_el.get_text(" ", strip=True))
        if not href or not title or title.lower().startswith("shop on ebay"):
            continue
        item_id = _extract_ebay_item_id(href)
        if not item_id or item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        price_el = item.select_one(".s-card__price, .s-item__price")
        subtitle_el = item.select_one(".s-card__subtitle, .s-item__subtitle")
        ship_text = _extract_card_shipping_text(item)
        image_el = item.select_one("img")
        price, currency = parse_price_text(
            price_el.get_text(" ", strip=True) if price_el else ""
        )
        subtitle = subtitle_el.get_text(" ", strip=True) if subtitle_el else ""
        ship_cost, ship_cur, ship_free = parse_shipping_info(ship_text)
        listing_type = parse_ebay_html_listing_type(item.get_text(" ", strip=True))
        description = truncate(f"{title}. {subtitle}".strip(". "), 450)
        image_url = None
        if image_el:
            image_url = image_el.get("src") or image_el.get("data-src")
        listings.append(
            Listing(
                id=item_id,
                title=title,
                description=description,
                price=price,
                currency=currency,
                image_url=image_url,
                item_url=href.split("?")[0],
                source=Source.EBAY_PARSER,
                shipping_cost=ship_cost,
                shipping_currency=ship_cur or currency,
                shipping_free=ship_free,
                listing_type=listing_type,
            )
        )
    return listings


def _clean_ebay_title(title: str) -> str:
    title = re.sub(
        r"\s*Opens in a new window or tab\s*$",
        "",
        title or "",
        flags=re.IGNORECASE,
    )
    return title.strip()


def _extract_card_shipping_text(item: Tag) -> str:
    shipping_el = item.select_one(
        ".s-item__shipping, .s-item__logisticsCost, .s-item__freeXDays"
    )
    if shipping_el:
        return shipping_el.get_text(" ", strip=True)
    for row in item.select(".s-card__attribute-row, .su-styled-text"):
        text = row.get_text(" ", strip=True)
        lower = text.lower()
        if any(token in lower for token in ("delivery", "shipping", "postage")):
            return text
    return ""


def _extract_ebay_item_id(url: str) -> str | None:
    match = re.search(r"/itm/(?:[^/]+/)?(\d+)", url)
    if match:
        item_id = match.group(1)
        # Заглушки в разметке eBay (не реальные лоты)
        if item_id in {"123456", "0"}:
            return None
        return item_id
    match = re.search(r"[?&]item=(\d+)", url)
    if not match:
        return None
    item_id = match.group(1)
    if item_id in {"123456", "0"}:
        return None
    return item_id


def _strip_html(text: str) -> str:
    return BeautifulSoup(text or "", "lxml").get_text(" ", strip=True)


def _extract_price_from_summary(summary: str) -> tuple[float | None, str | None]:
    text = _strip_html(summary)
    return parse_price_text(text)


def _extract_image_from_summary(summary: str) -> str | None:
    soup = BeautifulSoup(summary or "", "lxml")
    img = soup.select_one("img")
    if not img:
        return None
    return img.get("src") or img.get("data-src")
