from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup

from bot.models import Listing, Search, Source
from bot.providers.base import BaseProvider, ProviderError
from bot.providers.http_utils import (
    USER_AGENT,
    build_client,
    parse_price_text,
    request_with_retries,
    truncate,
)
from bot.providers.etsy_browser import get_browser
from bot.providers.listing_meta import parse_shipping_info

logger = logging.getLogger(__name__)

ETSY_OPENAPI_BASE = "https://openapi.etsy.com/v3/application"


class EtsyProvider(BaseProvider):
    source = Source.ETSY

    def __init__(
        self,
        *,
        proxy: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._api_key = (api_key or "").strip() or None
        self._proxy = (proxy or "").strip() or None
        self._client = build_client(proxy)
        self._warmed = False

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(self, search: Search, *, limit: int = 20) -> list[Listing]:
        query = search.keywords.strip()
        if not query:
            return []
        # Open API — только если ключ реально есть; иначе Playwright (обход DataDome)
        if self._api_key:
            return await self._search_via_api(search, limit=limit)
        return await self._search_via_playwright(search, limit=limit)

    async def _search_via_api(self, search: Search, *, limit: int) -> list[Listing]:
        params: dict[str, str | int | float] = {
            "keywords": search.keywords.strip(),
            "limit": min(max(limit, 1), 100),
            "sort_on": "created",
            "sort_order": "desc",
            "includes": "Images",
        }
        if search.min_price is not None:
            params["min_price"] = search.min_price
        if search.max_price is not None:
            params["max_price"] = search.max_price
        url = f"{ETSY_OPENAPI_BASE}/listings/active"
        try:
            response = await self._client.get(
                url,
                params=params,
                headers={
                    "Accept": "application/json",
                    "x-api-key": self._api_key or "",
                },
            )
        except Exception as exc:
            raise ProviderError(f"Etsy API request failed: {exc}") from exc

        if response.status_code in {401, 403}:
            detail = _api_error_detail(response)
            raise ProviderError(
                "Etsy API отклонил ключ (проверьте keystring:shared_secret). "
                f"{detail}"
            )
        if response.status_code >= 400:
            detail = _api_error_detail(response)
            raise ProviderError(f"Etsy API HTTP {response.status_code}: {detail}")

        try:
            payload = response.json()
        except Exception as exc:
            raise ProviderError(f"Etsy API: невалидный JSON: {exc}") from exc

        listings = _parse_api_listings(payload, limit=limit)
        if not listings:
            logger.warning("Etsy API returned 0 items for %r", search.keywords)
        return listings

    async def verify_credentials(self) -> None:
        """Лёгкая проверка ключа через Open API."""
        if not self._api_key:
            raise ProviderError("Etsy API key не задан")
        try:
            response = await self._client.get(
                f"{ETSY_OPENAPI_BASE}/listings/active",
                params={"limit": 1, "keywords": "test"},
                headers={
                    "Accept": "application/json",
                    "x-api-key": self._api_key,
                },
            )
        except Exception as exc:
            raise ProviderError(f"Etsy API request failed: {exc}") from exc
        if response.status_code in {401, 403}:
            detail = _api_error_detail(response)
            raise ProviderError(f"Etsy API ключ отклонён: {detail}")
        if response.status_code >= 400:
            detail = _api_error_detail(response)
            raise ProviderError(f"Etsy API HTTP {response.status_code}: {detail}")

    def _search_url(self, search: Search) -> str:
        params: dict[str, str] = {
            "q": search.keywords.strip(),
            "order": "date_desc",
            "ref": "search_bar",
        }
        if search.min_price is not None:
            params["min"] = str(search.min_price)
        if search.max_price is not None:
            params["max"] = str(search.max_price)
        return f"https://www.etsy.com/search?{urlencode(params)}"

    async def _search_via_playwright(self, search: Search, *, limit: int) -> list[Listing]:
        """Загрузка выдачи через реальный Chromium — проходит DataDome на VPS."""
        url = self._search_url(search)
        context = None
        try:
            browser = await get_browser()
            context_kwargs: dict[str, Any] = {
                "user_agent": USER_AGENT,
                "viewport": {"width": 1366, "height": 900},
                "locale": "en-US",
                "timezone_id": "America/New_York",
            }
            if self._proxy:
                context_kwargs["proxy"] = {"server": self._proxy}
            context = await browser.new_context(**context_kwargs)
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            # DataDome / гидрация карточек
            try:
                await page.wait_for_selector(
                    "a[href*='/listing/'], [data-search-results] a[href*='/listing/']",
                    timeout=35_000,
                )
            except Exception:
                # подождём ещё чуть-чуть и снимем HTML как есть
                await page.wait_for_timeout(2500)
            html = await page.content()
        except Exception as exc:
            raise ProviderError(f"Etsy Playwright failed: {exc}") from exc
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass

        if _looks_like_datadome(html) and "/listing/" not in html:
            raise ProviderError(
                "Etsy Playwright: страница заблокирована DataDome. "
                "Попробуйте позже или HTTP_PROXY."
            )

        listings = _parse_search_html(html, limit=limit)
        if not listings:
            logger.warning("Etsy Playwright returned 0 items for %r", search.keywords)
        return listings

    async def _search_via_html(self, search: Search, *, limit: int) -> list[Listing]:
        """Legacy httpx-путь (обычно 403 без proxy)."""
        url = self._search_url(search)
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
            if response.status_code == 403 or _looks_like_datadome(response.text):
                raise ProviderError(
                    "Etsy HTML заблокирован DataDome (403). "
                    "Используется Playwright; при ошибке задайте HTTP_PROXY."
                )
            response.raise_for_status()
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"Etsy request failed: {exc}") from exc

        listings = _parse_search_html(response.text, limit=limit)
        if not listings:
            logger.warning("Etsy returned 0 items for %r", search.keywords)
            return []

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


def _api_error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
        if isinstance(data, dict):
            return str(data.get("error") or data.get("error_description") or data)[:240]
    except Exception:
        pass
    return (response.text or "")[:240]


def _looks_like_datadome(html: str) -> bool:
    low = (html or "").lower()
    return "datadome" in low or "please enable js and disable any ad blocker" in low


def _parse_api_listings(payload: Any, *, limit: int) -> list[Listing]:
    results = []
    if isinstance(payload, dict):
        results = payload.get("results") or []
    if not isinstance(results, list):
        return []

    listings: list[Listing] = []
    for item in results:
        if len(listings) >= limit:
            break
        if not isinstance(item, dict):
            continue
        listing_id = item.get("listing_id")
        if listing_id is None:
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        price, currency = _price_from_api(item.get("price"))
        url = str(item.get("url") or "").strip()
        if not url:
            url = f"https://www.etsy.com/listing/{listing_id}"
        image_url = _image_from_api(item)
        description = str(item.get("description") or title)
        listings.append(
            Listing(
                id=str(listing_id),
                title=title[:200],
                description=truncate(description, 450),
                price=price,
                currency=currency or "USD",
                image_url=image_url,
                item_url=url.split("?")[0],
                source=Source.ETSY,
                shipping_currency=currency or "USD",
            )
        )
    return listings


def _price_from_api(price_obj: Any) -> tuple[float | None, str | None]:
    if isinstance(price_obj, dict):
        currency = price_obj.get("currency_code")
        amount = price_obj.get("amount")
        divisor = price_obj.get("divisor") or 100
        try:
            if amount is not None:
                return float(amount) / float(divisor), str(currency) if currency else None
        except (TypeError, ValueError, ZeroDivisionError):
            return None, str(currency) if currency else None
    if isinstance(price_obj, (int, float, str)):
        try:
            return float(price_obj), None
        except (TypeError, ValueError):
            return None, None
    return None, None


def _image_from_api(item: dict[str, Any]) -> str | None:
    images = item.get("images")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict):
            for key in (
                "url_570xN",
                "url_fullxfull",
                "url_75x75",
                "url_170x135",
            ):
                value = first.get(key)
                if value:
                    return str(value)
    return None


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
