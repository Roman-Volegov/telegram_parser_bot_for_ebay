from __future__ import annotations

import logging
import re
import time
from base64 import b64encode

import httpx

from bot.models import Listing, Search, Source
from bot.providers.base import BaseProvider, ProviderError
from bot.providers.http_utils import build_client, truncate
from bot.providers.listing_meta import format_ebay_listing_type, shipping_from_ebay_api

logger = logging.getLogger(__name__)

EBAY_SCOPE = "https://api.ebay.com/oauth/api_scope"


class EbayTokenCache:
    def __init__(self) -> None:
        self._tokens: dict[int, tuple[str, float]] = {}

    def get(self, telegram_id: int) -> str | None:
        item = self._tokens.get(telegram_id)
        if not item:
            return None
        token, expires_at = item
        if time.time() >= expires_at - 60:
            self._tokens.pop(telegram_id, None)
            return None
        return token

    def set(self, telegram_id: int, token: str, expires_in: int) -> None:
        self._tokens[telegram_id] = (token, time.time() + max(expires_in, 60))

    def invalidate(self, telegram_id: int) -> None:
        self._tokens.pop(telegram_id, None)


_TOKEN_CACHE = EbayTokenCache()


class EbayApiProvider(BaseProvider):
    source = Source.EBAY_API

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        marketplace_id: str = "EBAY_US",
        telegram_id: int,
        proxy: str | None = None,
        token_cache: EbayTokenCache | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.marketplace_id = marketplace_id
        self.telegram_id = telegram_id
        self._cache = token_cache or _TOKEN_CACHE
        self._client = build_client(proxy)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def verify_credentials(self) -> str:
        return await self._get_token(force=True)

    async def search(self, search: Search, *, limit: int = 20) -> list[Listing]:
        try:
            token = await self._get_token()
            items = await self._search_summaries(token, search, limit=limit)
            listings: list[Listing] = []
            for item in items:
                listing = await self._to_listing(token, item)
                if listing is not None:
                    listings.append(listing)
            return listings
        except httpx.HTTPError as exc:
            raise ProviderError(f"eBay API request failed: {exc}") from exc

    async def _get_token(self, *, force: bool = False) -> str:
        if not force:
            cached = self._cache.get(self.telegram_id)
            if cached:
                return cached
        credentials = b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()
        response = await self._client.post(
            "https://api.ebay.com/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials", "scope": EBAY_SCOPE},
        )
        if response.status_code >= 400:
            self._cache.invalidate(self.telegram_id)
            raise ProviderError(
                f"eBay OAuth failed HTTP {response.status_code}: проверьте Client ID/Secret"
            )
        payload = response.json()
        token = payload["access_token"]
        self._cache.set(self.telegram_id, token, int(payload.get("expires_in", 7200)))
        return token

    async def _search_summaries(
        self, token: str, search: Search, *, limit: int
    ) -> list[dict]:
        filters: list[str] = []
        if search.min_price is not None:
            filters.append(f"price:[{search.min_price}..]")
        if search.max_price is not None:
            filters.append(f"price:[..{search.max_price}]")
        if search.min_price is not None or search.max_price is not None:
            filters.append("priceCurrency:USD")
        if search.buy_it_now:
            filters.append("buyingOptions:{FIXED_PRICE}")
        if search.condition:
            filters.append(f"conditions:{{{search.condition}}}")

        params: dict[str, str | int] = {
            "q": search.keywords,
            "limit": min(limit, 50),
            "sort": "newlyListed",
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
        if response.status_code >= 400:
            raise ProviderError(f"eBay Browse search failed HTTP {response.status_code}")
        return list(response.json().get("itemSummaries") or [])

    async def _to_listing(self, token: str, item: dict) -> Listing | None:
        item_id = str(item.get("itemId") or item.get("legacyItemId") or "")
        if not item_id:
            return None
        title = (item.get("title") or "Untitled").strip()
        price_block = item.get("price") or {}
        image = (item.get("image") or {}).get("imageUrl")
        url = item.get("itemWebUrl") or item.get("itemHref") or ""
        description = title
        short = item.get("shortDescription")
        if short:
            description = f"{title}. {short}"

        shipping_cost, shipping_currency, shipping_free = shipping_from_ebay_api(item)
        listing_type = format_ebay_listing_type(item.get("buyingOptions"))
        detail = None

        # Дозапрос описания/картинки/доставки при необходимости
        need_detail = bool(item.get("itemId")) and (
            not image
            or not short
            or (shipping_cost is None and not shipping_free)
            or not listing_type
        )
        if need_detail:
            detail = await self._get_item(token, str(item["itemId"]))
            if detail:
                if not image:
                    image = (detail.get("image") or {}).get("imageUrl")
                desc = detail.get("shortDescription") or detail.get("description")
                if desc:
                    # description может быть HTML — обрежем теги грубо
                    clean = truncate(re_strip_html(str(desc)), 450)
                    description = f"{title}. {clean}" if clean else title
                if not url:
                    url = detail.get("itemWebUrl") or url
                if shipping_cost is None and not shipping_free:
                    shipping_cost, shipping_currency, shipping_free = shipping_from_ebay_api(
                        detail
                    )
                if not listing_type:
                    listing_type = format_ebay_listing_type(detail.get("buyingOptions"))

        return Listing(
            id=item_id,
            title=title,
            description=truncate(description, 450),
            price=_to_float(price_block.get("value")),
            currency=price_block.get("currency"),
            image_url=image,
            item_url=url,
            source=Source.EBAY_API,
            shipping_cost=shipping_cost,
            shipping_currency=shipping_currency,
            shipping_free=shipping_free,
            listing_type=listing_type,
        )

    async def _get_item(self, token: str, item_id: str) -> dict | None:
        try:
            response = await self._client.get(
                f"https://api.ebay.com/buy/browse/v1/item/{item_id}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-EBAY-C-MARKETPLACE-ID": self.marketplace_id,
                },
            )
            if response.status_code >= 400:
                return None
            return response.json()
        except httpx.HTTPError:
            return None


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def re_strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text)
