from __future__ import annotations

from html import escape

from bot.models import Listing, Marketplace, WatchFilter


MARKETPLACE_LABELS = {
    Marketplace.EBAY: "eBay",
    Marketplace.POSHMARK: "Poshmark",
}


def marketplace_label(marketplace: Marketplace) -> str:
    return MARKETPLACE_LABELS[marketplace]


def format_listing(listing: Listing, *, prefix: str = "") -> str:
    market = marketplace_label(listing.marketplace)
    lines = [
        f"{prefix}<b>{escape(listing.title)}</b>",
        f"{market} · {escape(listing.price_label)}",
        f'<a href="{escape(listing.url, quote=True)}">Открыть лот</a>',
    ]
    if listing.seller:
        lines.insert(2, f"Продавец: {escape(listing.seller)}")
    return "\n".join(lines)


def format_listings(listings: list[Listing], *, header: str) -> str:
    if not listings:
        return f"{header}\n\nНичего не найдено."
    blocks = [header, ""]
    for index, listing in enumerate(listings, start=1):
        blocks.append(format_listing(listing, prefix=f"{index}. "))
        blocks.append("")
    return "\n".join(blocks).strip()


def format_watch(watch: WatchFilter) -> str:
    market = marketplace_label(watch.marketplace)
    price_parts: list[str] = []
    if watch.min_price is not None:
        price_parts.append(f"от {watch.min_price:g}")
    if watch.max_price is not None:
        price_parts.append(f"до {watch.max_price:g}")
    price = f" · {' '.join(price_parts)}" if price_parts else ""
    return f"#{watch.id} · {market} · <code>{escape(watch.query)}</code>{price}"


def format_watch_list(watches: list[WatchFilter]) -> str:
    if not watches:
        return "Активных подписок нет.\nДобавьте через /watch"
    lines = ["Ваши подписки:", ""]
    lines.extend(format_watch(watch) for watch in watches)
    lines.append("")
    lines.append("Удалить: /unwatch &lt;id&gt;")
    return "\n".join(lines)
