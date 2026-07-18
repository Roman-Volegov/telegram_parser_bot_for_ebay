from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Marketplace(StrEnum):
    EBAY = "ebay"
    POSHMARK = "poshmark"


@dataclass(slots=True, frozen=True)
class Listing:
    marketplace: Marketplace
    external_id: str
    title: str
    price: float | None
    currency: str | None
    url: str
    image_url: str | None = None
    seller: str | None = None

    @property
    def price_label(self) -> str:
        if self.price is None:
            return "цена не указана"
        currency = self.currency or ""
        if currency:
            return f"{self.price:.2f} {currency}"
        return f"{self.price:.2f}"


@dataclass(slots=True)
class WatchFilter:
    id: int
    user_id: int
    marketplace: Marketplace
    query: str
    max_price: float | None = None
    min_price: float | None = None
    active: bool = True
