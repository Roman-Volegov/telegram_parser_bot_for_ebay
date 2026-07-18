from __future__ import annotations

import logging
import re
from base64 import b64encode
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


class EbayParser(BaseParser):
    marketplace = Marketplace.EBAY

    def __init__(
        self,
        *,
        app_id: str = "",
        cert_id: str = "",
        marketplace_id: str = "EBAY_US",
    ) -> None:
        self.app_id = app_id
        self.cert_id = cert_id
        self.marketplace_id = marketplace_id
        self._token: str | None = None
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
            follow_redirects=True,
        )

    @property
    def api_enabled(self) -> bool:
        return bool(self.app_id and self.cert_id)

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
            if self.api_enabled:
                return await self._search_api(query, min_price, max_price, limit)
            return await self._search_html(query, min_price, max_price, limit)
        except httpx.HTTPError as exc:
            raise ParserError(f"eBay request failed: {exc}") from exc

    async def _get_app_token(self) -> str:
        if self._token:
            return self._token
        credentials = b64encode(f"{self.app_id}:{self.cert_id}".encode()).decode()
        response = await self._client.post(
            "https://api.ebay.com/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
        )
        response.raise_for_status()
        self._token = response.json()["access_token"]
        return self._token

    async def _search_api(
        self,
        query: str,
        min_price: float | None,
        max_price: float | None,
        limit: int,
    ) -> list[Listing]:
        token = await self._get_app_token()
        filters: list[str] = []
        if min_price is not None:
            filters.append(f"price:[{min_price}..]")
        if max_price is not None:
            filters.append(f"price:[..{max_price}]")
        if min_price is not None or max_price is not None:
            filters.append("priceCurrency:USD")

        params: dict[str, str | int] = {
            "q": query,
            "limit": min(limit, 50),
        }
        if filters:
            params["filter"] = ",".join(filters)

        response = await self._client.get(
            "https://api.ebay.com/buy/browse/v1/item_summary/search",
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": self.marketplace_id,
            },
            params=params,
        )
        response.raise_for_status()
        payload = response.json()
        listings: list[Listing] = []
        for item in payload.get("itemSummaries", []):
            price_block = item.get("price") or {}
            image = (item.get("image") or {}).get("imageUrl")
            listings.append(
                Listing(
                    marketplace=Marketplace.EBAY,
                    external_id=str(item.get("itemId") or item.get("legacyItemId")),
                    title=item.get("title") or "Untitled",
                    price=_to_float(price_block.get("value")),
                    currency=price_block.get("currency"),
                    url=item.get("itemWebUrl") or item.get("itemHref") or "",
                    image_url=image,
                    seller=(item.get("seller") or {}).get("username"),
                )
            )
        return listings

    async def _search_html(
        self,
        query: str,
        min_price: float | None,
        max_price: float | None,
        limit: int,
    ) -> list[Listing]:
        params = {
            "_nkw": query,
            "_ipg": min(max(limit, 20), 60),
            "rt": "nc",
        }
        if min_price is not None:
            params["_udlo"] = str(min_price)
        if max_price is not None:
            params["_udhi"] = str(max_price)

        response = await self._client.get("https://www.ebay.com/sch/i.html", params=params)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        items = soup.select("li.s-item")
        listings: list[Listing] = []

        for item in items:
            if len(listings) >= limit:
                break
            link = item.select_one("a.s-item__link")
            title_el = item.select_one(".s-item__title")
            price_el = item.select_one(".s-item__price")
            image_el = item.select_one("img")
            if not link or not title_el:
                continue
            href = link.get("href") or ""
            title = title_el.get_text(" ", strip=True)
            if not href or not title or title.lower().startswith("shop on ebay"):
                continue
            external_id = _extract_ebay_item_id(href)
            if not external_id:
                continue
            price, currency = _parse_price_text(price_el.get_text(" ", strip=True) if price_el else "")
            image_url = image_el.get("src") if image_el else None
            listings.append(
                Listing(
                    marketplace=Marketplace.EBAY,
                    external_id=external_id,
                    title=title,
                    price=price,
                    currency=currency,
                    url=href.split("?")[0],
                    image_url=image_url,
                )
            )
        if not listings:
            logger.warning("eBay HTML search returned 0 items for query=%r", query)
        return listings


def _extract_ebay_item_id(url: str) -> str | None:
    match = re.search(r"/itm/(?:[^/]+/)?(\d+)", url)
    if match:
        return match.group(1)
    match = re.search(r"[?&]item=(\d+)", url)
    return match.group(1) if match else None


def _parse_price_text(text: str) -> tuple[float | None, str | None]:
    if not text:
        return None, None
    # Examples: "$12.99", "US $12.99", "$10.00 to $20.00"
    first = text.split("to")[0].strip()
    currency = None
    if "US $" in text or first.startswith("$"):
        currency = "USD"
    elif "€" in text:
        currency = "EUR"
    elif "£" in text:
        currency = "GBP"
    number = re.search(r"(\d+[.,]\d+|\d+)", first.replace(",", ""))
    if not number:
        return None, currency
    return float(number.group(1).replace(",", ".")), currency


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_ebay_search_url(query: str) -> str:
    return f"https://www.ebay.com/sch/i.html?_nkw={quote_plus(query)}"
