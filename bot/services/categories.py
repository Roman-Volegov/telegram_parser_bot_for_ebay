from __future__ import annotations

from typing import Any

from bot.models import NON_EBAY_SOURCES, Source

MAX_CATEGORIES_PER_SOURCE = 10

EBAY_SOURCES = frozenset({Source.EBAY_API, Source.EBAY_PARSER})


def normalize_category_list(raw: Any, *, source: Source) -> list[dict[str, Any]]:
    """Нормализует список категорий для одного источника."""
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        normalized = _normalize_one(entry, source=source)
        if normalized is None:
            continue
        key = _dedupe_key(normalized, source=source)
        if key in seen:
            continue
        seen.add(key)
        items.append(normalized)
        if len(items) >= MAX_CATEGORIES_PER_SOURCE:
            break
    return items


def normalize_categories_payload(raw: Any) -> dict[str, list[dict[str, Any]]]:
    """categories: { source_value: [ ... ] } → очищенный словарь."""
    if not isinstance(raw, dict):
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for key, value in raw.items():
        try:
            source = Source(str(key))
        except ValueError:
            continue
        items = normalize_category_list(value, source=source)
        result[source.value] = items
    # Общий ключ "ebay" из UI → на оба eBay-источника, если передали так.
    if "ebay" in raw and "ebay_api" not in result and "ebay_parser" not in result:
        ebay_items = normalize_category_list(raw.get("ebay"), source=Source.EBAY_API)
        result[Source.EBAY_API.value] = ebay_items
        result[Source.EBAY_PARSER.value] = list(ebay_items)
    return result


def categories_for_search(search_filters: dict[str, Any], source: Source) -> list[dict[str, Any]]:
    """Категории из filters_json строки поиска."""
    raw = search_filters.get("categories")
    if raw is None:
        # legacy single-object fields
        if source in EBAY_SOURCES and search_filters.get("category_id"):
            return normalize_category_list(
                {
                    "category_id": search_filters.get("category_id"),
                    "category_path": search_filters.get("category_path"),
                },
                source=source,
            )
        if source is Source.ETSY and search_filters.get("taxonomy_id") is not None:
            return normalize_category_list(
                {
                    "taxonomy_id": search_filters.get("taxonomy_id"),
                    "category_path": search_filters.get("category_path"),
                },
                source=source,
            )
        if source is Source.POSHMARK and search_filters.get("department"):
            return normalize_category_list(
                {
                    "department": search_filters.get("department"),
                    "category": search_filters.get("category"),
                    "subcategory": search_filters.get("subcategory"),
                    "category_path": search_filters.get("category_path"),
                },
                source=source,
            )
        return []
    return normalize_category_list(raw, source=source)


def apply_categories_to_filters(
    filters: dict[str, Any],
    *,
    source: Source,
    categories_by_source: dict[str, list[dict[str, Any]]] | None,
) -> dict[str, Any]:
    """Пишет categories в filters_json для конкретной строки источника."""
    out = dict(filters)
    # убрать legacy single keys
    for key in (
        "category_id",
        "taxonomy_id",
        "department",
        "category",
        "subcategory",
        "category_path",
    ):
        out.pop(key, None)
    if categories_by_source is None:
        return out
    items = categories_by_source.get(source.value)
    if items is None and source in EBAY_SOURCES:
        # общий ebay-блок
        items = categories_by_source.get("ebay")
        if items is None:
            other = (
                Source.EBAY_PARSER.value
                if source is Source.EBAY_API
                else Source.EBAY_API.value
            )
            items = categories_by_source.get(other)
    if items is None:
        out.pop("categories", None)
        return out
    normalized = normalize_category_list(items, source=source)
    if normalized:
        out["categories"] = normalized
    else:
        out.pop("categories", None)
    return out


def categories_equal(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    *,
    source: Source,
) -> bool:
    left_keys = [_dedupe_key(item, source=source) for item in left]
    right_keys = [_dedupe_key(item, source=source) for item in right]
    return left_keys == right_keys


def merge_listings_by_id(batches: list[list[Any]], *, limit: int) -> list[Any]:
    """OR-merge результатов нескольких category-запросов."""
    merged: list[Any] = []
    seen: set[str] = set()
    for batch in batches:
        for item in batch:
            item_id = getattr(item, "id", None)
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)
            merged.append(item)
            if len(merged) >= limit:
                return merged
    return merged


def _normalize_one(entry: dict[str, Any], *, source: Source) -> dict[str, Any] | None:
    path = str(entry.get("category_path") or entry.get("path") or "").strip()
    if source in EBAY_SOURCES:
        category_id = str(entry.get("category_id") or entry.get("id") or "").strip()
        if not category_id:
            return None
        out: dict[str, Any] = {"category_id": category_id}
        if path:
            out["category_path"] = path
        return out
    if source is Source.ETSY:
        raw_id = entry.get("taxonomy_id", entry.get("id"))
        try:
            taxonomy_id = int(raw_id)
        except (TypeError, ValueError):
            return None
        out = {"taxonomy_id": taxonomy_id}
        if path:
            out["category_path"] = path
        return out
    if source is Source.POSHMARK:
        department = str(entry.get("department") or "").strip()
        if not department:
            return None
        category = str(entry.get("category") or "").strip() or None
        subcategory = str(entry.get("subcategory") or "").strip() or None
        out = {"department": department}
        if category:
            out["category"] = category
        if subcategory:
            out["subcategory"] = subcategory
        if path:
            out["category_path"] = path
        elif category:
            bits = [department, category]
            if subcategory:
                bits.append(subcategory)
            out["category_path"] = " > ".join(bits)
        else:
            out["category_path"] = department
        return out
    if source in NON_EBAY_SOURCES:
        return None
    return None


def _dedupe_key(entry: dict[str, Any], *, source: Source) -> str:
    if source in EBAY_SOURCES:
        return f"ebay:{entry.get('category_id')}"
    if source is Source.ETSY:
        return f"etsy:{entry.get('taxonomy_id')}"
    return (
        f"poshmark:{entry.get('department')}|{entry.get('category') or ''}|"
        f"{entry.get('subcategory') or ''}"
    )
