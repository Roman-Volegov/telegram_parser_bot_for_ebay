from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class UserStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    BLOCKED = "blocked"


class Source(StrEnum):
    EBAY_API = "ebay_api"
    EBAY_PARSER = "ebay_parser"
    POSHMARK = "poshmark"


SOURCE_LABELS = {
    Source.EBAY_API: "eBay API",
    Source.EBAY_PARSER: "eBay Parser",
    Source.POSHMARK: "Poshmark",
}

EBAY_MARKETPLACES = (
    "EBAY_US",
    "EBAY_GB",
    "EBAY_DE",
    "EBAY_AU",
    "EBAY_CA",
    "EBAY_FR",
    "EBAY_IT",
    "EBAY_ES",
)

EBAY_MARKETPLACE_LABELS = {
    "EBAY_US": "США — ebay.com",
    "EBAY_GB": "Великобритания — ebay.co.uk",
    "EBAY_DE": "Германия — ebay.de",
    "EBAY_AU": "Австралия — ebay.com.au",
    "EBAY_CA": "Канада — ebay.ca",
    "EBAY_FR": "Франция — ebay.fr",
    "EBAY_IT": "Италия — ebay.it",
    "EBAY_ES": "Испания — ebay.es",
}

EBAY_MARKETPLACE_HOSTS = {
    "EBAY_US": "www.ebay.com",
    "EBAY_GB": "www.ebay.co.uk",
    "EBAY_DE": "www.ebay.de",
    "EBAY_AU": "www.ebay.com.au",
    "EBAY_CA": "www.ebay.ca",
    "EBAY_FR": "www.ebay.fr",
    "EBAY_IT": "www.ebay.it",
    "EBAY_ES": "www.ebay.es",
}


@dataclass(slots=True, frozen=True)
class Listing:
    id: str
    title: str
    description: str
    price: float | None
    currency: str | None
    image_url: str | None
    item_url: str
    source: Source
    shipping_cost: float | None = None
    shipping_currency: str | None = None
    shipping_free: bool = False
    listing_type: str | None = None  # eBay: Buy It Now / Аукцион / …

    @property
    def price_label(self) -> str:
        if self.price is None:
            return "цена не указана"
        if self.currency:
            return f"{self.price:.2f} {self.currency}"
        return f"{self.price:.2f}"

    @property
    def shipping_label(self) -> str:
        if self.shipping_free:
            return "бесплатно"
        if self.shipping_cost is None:
            return "не указана"
        currency = self.shipping_currency or self.currency or ""
        if currency:
            return f"{self.shipping_cost:.2f} {currency}"
        return f"{self.shipping_cost:.2f}"


@dataclass(slots=True)
class User:
    telegram_id: int
    username: str | None
    full_name: str | None
    status: UserStatus
    setup_completed: bool
    enabled_sources: list[Source] = field(default_factory=list)
    ebay_marketplace: str = "EBAY_US"
    ebay_deletion_token: str | None = None


@dataclass(slots=True)
class Search:
    id: int
    telegram_id: int
    source: Source
    keywords: str
    max_price: float | None = None
    min_price: float | None = None
    condition: str | None = None
    buy_it_now: bool = True
    paused: bool = False
    filters_json: dict[str, Any] = field(default_factory=dict)

    @property
    def marketplace(self) -> str | None:
        """Регион eBay для этого поиска (None / не для Poshmark)."""
        value = self.filters_json.get("marketplace")
        if isinstance(value, str) and value in EBAY_MARKETPLACES:
            return value
        return None


@dataclass(slots=True)
class PollLog:
    id: int
    telegram_id: int
    search_id: int | None
    source: Source
    keywords: str
    status: str  # ok | empty | seed | error
    found: int
    new_items: int
    notified: int
    message: str | None
    created_at: str
