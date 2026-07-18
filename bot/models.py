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
