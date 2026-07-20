from __future__ import annotations

import asyncio
import json
import logging
import re
from urllib.parse import urlencode

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


class EtsyProvider(BaseProvider):
    source = Source.ETSY

    def __init__(self, *, proxy: str | None = None) -> None:
        self._client = build_client(proxy)
        self._warmed = False

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(self, search: Search, *, limit: int = 20) -> list[Listing]:
        query = search.keywords.strip()
        if not query:
            return []
        params: dict[str, str] = {
            "q": query,
            "order": "date_desc",
            "ref": "search_bar",
        }
        if search.min_price is not None:
            params["min"] = str(search.min_price)
        if search.max_price is not None:
            params["max"] = str(search.max_price)
        url = f"https://www.etsy.com/search?{urlencode(params)}"
        try:
            await self._warmup()
            response = await request_with_retries(
                self._client,
                "GET",
                url,
                min_delay=2.0,
                max_delay=4.0,
                headers={
                    "Referer": "https://www.etsy.com/",
                    "Sec-Fetch-Site": "same-origin",
                },
            )
            if response.status_code == 403:
                raise ProviderError(
                    "Etsy вернул 403 (антибот). Для парсера нужен HTTP_PROXY "
                    "(желательно residential)."
                )
            response.raise_for_status()
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"Etsy request failed: {exc}") from exc

        listings = _parse_search_html(response.text, limit=limit)
        if not listings:
            logger.warning("Etsy returned 0 items for %r", query)
            return []

        # Доставка обычно только на странице лота
        sem = asyncio.Semaphore(5)

        async def enrich(item: Listing) -> Listing:
            async with sem:
                return await self._enrich_shipping(item)

        return list(await asyncio.gather(*[enrich(item) for item in listings]))

    async def _warmup(self) -> None:
        if self._warmed:
            return
        try:
            response = await self._client.get(
                "https://www.etsy.com/",
                headers={"Referer": "https://www.google.com/"},
            )
            if response.status_code < 400:
                self._warmed = True
        except Exception as exc:
            logger.debug("Etsy warmup failed: %s", exc)

    async def _enrich_shipping(self, listing: Listing) -> Listing:
        if listing.shipping_free or listing.shipping_cost is not None:
            return listing
        try:
            response = await self._client.get(
                listing.item_url,
                headers={"Referer": "https://www.etsy.com/"},
            )
            if response.status_code >= 400:
                return listing
            cost, currency, is_free = _extract_shipping_from_detail(response.text)
            if not is_free and cost is None:
                return listing
            return Listing(
                id=listing.id,
                title=listing.title,
                description=listing.description,
                price=listing.price,
                currency=listing.currency,
                image_url=listing.image_url,
                item_url=listing.item_url,
                source=listing.source,
                shipping_cost=cost,
                shipping_currency=currency or listing.shipping_currency or listing.currency,
                shipping_free=is_free,
                listing_type=listing.listing_type,
            )
        except Exception as exc:
            logger.debug("Etsy shipping enrich failed for %s: %s", listing.id, exc)
            return listing


def _parse_search_html(html: str, *, limit: int) -> list[Listing]:
    soup = BeautifulSoup(html or "", "lxml")
    listings = _parse_json_ld_listings(soup, limit=limit)
    if listings:
        return listings

    listings = []
    seen: set[str] = set()
    for anchor in soup.select("a[href*='/listing/']"):
        if len(listings) >= limit:
            break
        href = anchor.get("href") or ""
        item_id = _extract_etsy_listing_id(href)
        if not item_id or item_id in seen:
            continue
        title = _extract_title(anchor)
        if not title:
            continue
        # Пропускаем служебные/пустые карточки
        if title.lower() in {"etsy", "home"}:
            continue
        price, currency = _extract_price_near(anchor)
        image_url = _extract_image(anchor)
        absolute = href if href.startswith("http") else f"https://www.etsy.com{href}"
        absolute = absolute.split("?")[0]
        seen.add(item_id)
        listings.append(
            Listing(
                id=item_id,
                title=title,
                description=truncate(title, 450),
                price=price,
                currency=currency or "USD",
                image_url=image_url,
                item_url=absolute,
                source=Source.ETSY,
                shipping_currency=currency or "USD",
            )
        )
    return listings


def _parse_json_ld_listings(soup: BeautifulSoup, *, limit: int) -> list[Listing]:
    listings: list[Listing] = []
    seen: set[str] = set()
    for script in soup.select('script[type="application/ld+json"]'):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for item in _iter_ld_products(data):
            if len(listings) >= limit:
                return listings
            url = str(item.get("url") or item.get("@id") or "")
            item_id = _extract_etsy_listing_id(url)
            if not item_id or item_id in seen:
                continue
            title = str(item.get("name") or "").strip()
            if not title:
                continue
            price = None
            currency = None
            offers = item.get("offers")
            if isinstance(offers, dict):
                price, currency = _price_from_offer(offers)
            elif isinstance(offers, list) and offers:
                price, currency = _price_from_offer(offers[0])
            image = item.get("image")
            if isinstance(image, list) and image:
                image = image[0]
            if isinstance(image, dict):
                image = image.get("url") or image.get("contentUrl")
            image_url = str(image) if image else None
            absolute = url if url.startswith("http") else f"https://www.etsy.com{url}"
            seen.add(item_id)
            listings.append(
                Listing(
                    id=item_id,
                    title=title[:200],
                    description=truncate(
                        str(item.get("description") or title),
                        450,
                    ),
                    price=price,
                    currency=currency or "USD",
                    image_url=image_url,
                    item_url=absolute.split("?")[0],
                    source=Source.ETSY,
                    shipping_currency=currency or "USD",
                )
            )
    return listings


def _iter_ld_products(data: object):
    if isinstance(data, list):
        for item in data:
            yield from _iter_ld_products(item)
        return
    if not isinstance(data, dict):
        return
    types = data.get("@type")
    type_list = types if isinstance(types, list) else [types]
    if "Product" in type_list:
        yield data
    if data.get("@type") == "ItemList":
        for el in data.get("itemListElement") or []:
            if isinstance(el, dict):
                item = el.get("item") or el
                yield from _iter_ld_products(item)
    for key in ("mainEntity", "about", "itemListElement"):
        if key in data:
            yield from _iter_ld_products(data[key])


def _price_from_offer(offer: object) -> tuple[float | None, str | None]:
    if not isinstance(offer, dict):
        return None, None
    currency = offer.get("priceCurrency")
    value = offer.get("price")
    try:
        price = float(value) if value is not None else None
    except (TypeError, ValueError):
        price = None
    if isinstance(currency, str):
        return price, currency
    return price, None


def _extract_etsy_listing_id(url: str) -> str | None:
    match = re.search(r"/listing/(\d+)", url or "")
    return match.group(1) if match else None


def _extract_title(anchor) -> str | None:
    for sel in (
        "h3",
        "[data-listing-card-title]",
        ".v2-listing-card__title",
        "img[alt]",
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
        if el is None:
            continue
        if el.name == "img":
            alt = (el.get("alt") or "").strip()
            if alt:
                return alt[:200]
        text = el.get_text(" ", strip=True)
        if text:
            return text[:200]
    text = anchor.get_text(" ", strip=True)
    return text[:200] if text and len(text) > 2 else None


def _extract_price_near(anchor) -> tuple[float | None, str | None]:
    for sel in (
        ".currency-value",
        "[class*='currency-value']",
        "[data-buy-box-region] .currency-value",
        "p.lc-price",
        "[class*='price']",
    ):
        node = anchor
        for _ in range(6):
            if node is None:
                break
            el = node.select_one(sel)
            if el:
                price, currency = parse_price_text(el.get_text(" ", strip=True))
                if price is not None:
                    return price, currency or "USD"
            node = node.parent
    node = anchor
    for _ in range(5):
        if node is None:
            break
        price, currency = parse_price_text(node.get_text(" ", strip=True))
        if price is not None:
            return price, currency or "USD"
        node = node.parent
    return None, None


def _extract_image(anchor) -> str | None:
    image = anchor.select_one("img")
    if not image:
        node = anchor.parent
        for _ in range(4):
            if node is None:
                break
            image = node.select_one("img")
            if image:
                break
            node = node.parent
    if not image:
        return None
    return image.get("src") or image.get("data-src") or image.get("data-src-delay")


def _extract_shipping_from_detail(html: str) -> tuple[float | None, str | None, bool]:
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    for node in soup.find_all(string=re.compile(r"ship|deliver|postage", re.I)):
        text = (node.parent.get_text(" ", strip=True) if node.parent else str(node)).strip()
        if len(text) > 120:
            continue
        cost, currency, is_free = parse_shipping_info(text)
        if is_free or cost is not None:
            return cost, currency, is_free

    cleaned = soup.get_text(" ", strip=True)
    if re.search(r"\bfree shipping\b", cleaned, re.I):
        return 0.0, "USD", True
    match = re.search(
        r"(?:shipping|delivery)[^$]{0,40}\$?\s*(\d+(?:\.\d+)?)",
        cleaned,
        re.I,
    )
    if match:
        try:
            amount = float(match.group(1))
        except (TypeError, ValueError):
            return None, None, False
        return amount, "USD", amount == 0.0
    return None, None, False
