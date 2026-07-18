from __future__ import annotations

import re

from bot.providers.http_utils import parse_price_text

EBAY_BUYING_OPTION_LABELS = {
    "FIXED_PRICE": "Buy It Now",
    "AUCTION": "Аукцион",
    "BEST_OFFER": "Best Offer",
    "CLASSIFIED_AD": "Объявление",
}


def format_ebay_listing_type(buying_options: list[str] | None) -> str | None:
    if not buying_options:
        return None
    labels: list[str] = []
    for option in buying_options:
        label = EBAY_BUYING_OPTION_LABELS.get(option.upper(), option.replace("_", " ").title())
        if label not in labels:
            labels.append(label)
    return " / ".join(labels) if labels else None


def parse_shipping_info(
    text: str,
) -> tuple[float | None, str | None, bool]:
    """Возвращает (cost, currency, is_free)."""
    raw = (text or "").strip()
    if not raw:
        return None, None, False
    lower = raw.lower()
    if "free shipping" in lower or "бесплатн" in lower or lower in {"free", "бесплатно"}:
        return 0.0, None, True
    if "shipping not specified" in lower or "не указан" in lower:
        return None, None, False
    # "+$12.50 shipping", "Shipping: $5.99"
    price, currency = parse_price_text(raw)
    if price is None:
        return None, None, False
    return price, currency, price == 0.0


def parse_ebay_html_listing_type(item_text: str) -> str | None:
    lower = (item_text or "").lower()
    parts: list[str] = []
    if "buy it now" in lower:
        parts.append("Buy It Now")
    if "best offer" in lower:
        parts.append("Best Offer")
    if re.search(r"\b\d+\s+bids?\b", lower) or "auction" in lower:
        parts.append("Аукцион")
    # уникальные с сохранением порядка
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        if part not in seen:
            seen.add(part)
            ordered.append(part)
    return " / ".join(ordered) if ordered else None


def shipping_from_ebay_api(item: dict) -> tuple[float | None, str | None, bool]:
    options = item.get("shippingOptions") or []
    if not options:
        return None, None, False
    option = options[0] or {}
    cost_type = (option.get("shippingCostType") or "").upper()
    if cost_type == "FREE":
        return 0.0, None, True
    cost = option.get("shippingCost") or {}
    value = cost.get("value")
    currency = cost.get("currency")
    try:
        amount = float(value) if value is not None else None
    except (TypeError, ValueError):
        amount = None
    if amount is None:
        return None, currency, False
    return amount, currency, amount == 0.0
