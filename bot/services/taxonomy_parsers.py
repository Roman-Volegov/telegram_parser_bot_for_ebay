from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from bot.models import EBAY_MARKETPLACE_HOSTS
from bot.providers.http_utils import build_client

logger = logging.getLogger(__name__)

EBAY_CAT_ID_RE = re.compile(
    r"(?:[?&]_sacat=|/sch/|/b/[^/]+/)(\d{1,12})(?:/|&|#|$)",
    re.IGNORECASE,
)
ETSY_TAXONOMY_RE = re.compile(r"[?&]taxonomy_id=(\d+)", re.IGNORECASE)
ETSY_PATH_RE = re.compile(r"/c/([a-z0-9\-/]+)", re.IGNORECASE)
POSH_CAT_RE = re.compile(r"/category/([^\"'?#]+)", re.IGNORECASE)


def extract_ebay_category_id(href: str) -> str | None:
    match = EBAY_CAT_ID_RE.search(href or "")
    if not match:
        return None
    value = match.group(1)
    if value in {"0", "1"} and "_sacat=0" in href:
        return None
    return value


def parse_ebay_all_categories_html(html: str, *, host: str) -> list[dict[str, Any]]:
    """Разбор страницы all-categories → узлы с category_id (_sacat)."""
    soup = BeautifulSoup(html or "", "lxml")
    nodes_map: dict[str, dict[str, Any]] = {}

    def upsert(
        category_id: str,
        name: str,
        *,
        parent_id: str | None,
        path: str,
    ) -> None:
        name = _clean_name(name)
        if not category_id or not name:
            return
        existing = nodes_map.get(category_id)
        if existing is None:
            nodes_map[category_id] = {
                "id": category_id,
                "name": name,
                "path": path,
                "parent_id": parent_id,
                "has_children": False,
                "meta": {"category_id": category_id},
            }
            return
        if len(path) > len(str(existing.get("path") or "")):
            existing["name"] = name
            existing["path"] = path
        if parent_id and not existing.get("parent_id"):
            existing["parent_id"] = parent_id

    # Вложенные списки: li > a + ul > li
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "")
        category_id = extract_ebay_category_id(href)
        if not category_id:
            continue
        name = anchor.get_text(" ", strip=True) or _name_from_ebay_href(href)
        parent_id = None
        path = name
        parent_li = anchor.find_parent("li")
        if isinstance(parent_li, Tag):
            parent_ul = parent_li.find_parent("ul")
            if isinstance(parent_ul, Tag):
                parent_holder = parent_ul.find_parent("li")
                if isinstance(parent_holder, Tag):
                    parent_a = parent_holder.find("a", href=True, recursive=False)
                    if parent_a is None:
                        parent_a = parent_holder.find("a", href=True)
                    if parent_a is not None and parent_a is not anchor:
                        parent_href = str(parent_a.get("href") or "")
                        parent_id = extract_ebay_category_id(parent_href)
                        parent_name = parent_a.get_text(" ", strip=True)
                        if parent_id and parent_name:
                            upsert(
                                parent_id,
                                parent_name,
                                parent_id=None,
                                path=parent_name,
                            )
                            path = f"{parent_name} > {name}"
        upsert(category_id, name, parent_id=parent_id, path=path)

    # Проставить has_children
    children_of: set[str] = set()
    for node in nodes_map.values():
        parent = node.get("parent_id")
        if parent:
            children_of.add(str(parent))
    for node_id, node in nodes_map.items():
        node["has_children"] = node_id in children_of

    # Если иерархия не распознана — оставить плоский список корней
    if nodes_map and sum(1 for n in nodes_map.values() if n.get("parent_id")) == 0:
        for node in nodes_map.values():
            node["parent_id"] = None
            node["path"] = node["name"]
            node["has_children"] = False

    _ = host  # reserved for absolute URL joins if needed later
    return list(nodes_map.values())


def parse_ebay_subcategory_html(
    html: str,
    *,
    parent_id: str,
    parent_path: str,
) -> list[dict[str, Any]]:
    """Подкатегории с страницы категории / левой колонки refine."""
    soup = BeautifulSoup(html or "", "lxml")
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    selectors = (
        "ul.x-categories__list a[href]",
        "div.dialog__cell a[href]",
        "nav a[href*='_sacat=']",
        "a[href*='/_sacat=']",
        "a[href*='/sch/']",
        "a[href*='/b/']",
    )
    for selector in selectors:
        for anchor in soup.select(selector):
            href = str(anchor.get("href") or "")
            category_id = extract_ebay_category_id(href)
            if not category_id or category_id == parent_id or category_id in seen:
                continue
            name = _clean_name(anchor.get_text(" ", strip=True) or _name_from_ebay_href(href))
            if not name or name.lower() in {"all", "see all", "shop all"}:
                continue
            seen.add(category_id)
            nodes.append(
                {
                    "id": category_id,
                    "name": name,
                    "path": f"{parent_path} > {name}" if parent_path else name,
                    "parent_id": parent_id,
                    "has_children": False,
                    "meta": {"category_id": category_id},
                }
            )
    return nodes


def parse_etsy_categories_html(html: str) -> list[dict[str, Any]]:
    """Разбор Etsy /categories и /c/... страниц."""
    soup = BeautifulSoup(html or "", "lxml")
    nodes_map: dict[str, dict[str, Any]] = {}

    # JSON-LD / inline taxonomy_id hints
    for match in ETSY_TAXONOMY_RE.finditer(html or ""):
        taxonomy_id = match.group(1)
        # name неизвестно — дополним из якорей ниже
        nodes_map.setdefault(
            taxonomy_id,
            {
                "id": taxonomy_id,
                "name": f"Category {taxonomy_id}",
                "path": f"Category {taxonomy_id}",
                "parent_id": None,
                "has_children": False,
                "meta": {"taxonomy_id": int(taxonomy_id)},
            },
        )

    for anchor in soup.select("a[href*='/c/'], a[href*='taxonomy_id=']"):
        href = str(anchor.get("href") or "")
        name = _clean_name(anchor.get_text(" ", strip=True))
        taxonomy_id = _etsy_taxonomy_id(href)
        slug = _etsy_slug(href)
        if not taxonomy_id and not slug:
            continue
        node_id = taxonomy_id or f"slug:{slug}"
        if not name:
            name = (slug or node_id).replace("-", " ").title()
        path = name
        parent_id = None
        # /c/parent/child → parent slug
        if slug and "/" in slug:
            parts = [p for p in slug.split("/") if p]
            if len(parts) >= 2:
                parent_slug = "/".join(parts[:-1])
                parent_id = f"slug:{parent_slug}"
                parent_name = parts[-2].replace("-", " ").title()
                nodes_map.setdefault(
                    parent_id,
                    {
                        "id": parent_id,
                        "name": parent_name,
                        "path": parent_name,
                        "parent_id": None,
                        "has_children": True,
                        "meta": {"slug": parent_slug},
                    },
                )
                path = f"{nodes_map[parent_id]['path']} > {name}"
        meta: dict[str, Any] = {}
        if taxonomy_id:
            meta["taxonomy_id"] = int(taxonomy_id)
        if slug:
            meta["slug"] = slug
        existing = nodes_map.get(node_id)
        if existing is None:
            nodes_map[node_id] = {
                "id": node_id,
                "name": name,
                "path": path,
                "parent_id": parent_id,
                "has_children": False,
                "meta": meta,
            }
        else:
            if not str(existing.get("name") or "").startswith("Category ") or name:
                if not str(existing.get("name") or "").startswith("Category "):
                    pass
                elif name and not name.startswith("Category "):
                    existing["name"] = name
                    existing["path"] = path
            if taxonomy_id:
                existing["meta"]["taxonomy_id"] = int(taxonomy_id)
            if slug:
                existing["meta"]["slug"] = slug
            if parent_id and not existing.get("parent_id"):
                existing["parent_id"] = parent_id

    children_of = {
        str(n.get("parent_id"))
        for n in nodes_map.values()
        if n.get("parent_id")
    }
    for node_id, node in nodes_map.items():
        node["has_children"] = node_id in children_of
    return list(nodes_map.values())


def merge_taxonomy_nodes(*batches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for batch in batches:
        for node in batch:
            node_id = str(node.get("id") or "")
            if not node_id:
                continue
            existing = merged.get(node_id)
            if existing is None:
                merged[node_id] = dict(node)
                continue
            # предпочтительнее узел с taxonomy_id / более длинным path
            existing_meta = existing.get("meta") or {}
            new_meta = node.get("meta") or {}
            if new_meta.get("taxonomy_id") and not existing_meta.get("taxonomy_id"):
                existing["meta"] = {**existing_meta, **new_meta}
            if len(str(node.get("path") or "")) > len(str(existing.get("path") or "")):
                existing["name"] = node.get("name") or existing.get("name")
                existing["path"] = node.get("path")
                if node.get("parent_id"):
                    existing["parent_id"] = node.get("parent_id")
            existing["has_children"] = bool(
                existing.get("has_children") or node.get("has_children")
            )
    children_of = {
        str(n.get("parent_id")) for n in merged.values() if n.get("parent_id")
    }
    for node_id, node in merged.items():
        node["has_children"] = node_id in children_of or bool(node.get("has_children"))
    return list(merged.values())


def parse_poshmark_category_hrefs(html: str) -> set[str]:
    hrefs = set(POSH_CAT_RE.findall(html or ""))
    return {unquote(item.strip("/")) for item in hrefs if item.strip("/")}


def poshmark_nodes_from_slugs(slugs: set[str]) -> list[dict[str, Any]]:
    nodes_map: dict[str, dict[str, Any]] = {}

    def ensure(
        node_id: str,
        *,
        name: str,
        path: str,
        parent_id: str | None,
        meta: dict[str, Any],
    ) -> None:
        existing = nodes_map.get(node_id)
        if existing is None:
            nodes_map[node_id] = {
                "id": node_id,
                "name": name,
                "path": path,
                "parent_id": parent_id,
                "has_children": False,
                "meta": meta,
            }
            return
        if len(path) > len(str(existing.get("path") or "")):
            existing["path"] = path
            existing["name"] = name

    for slug in sorted(slugs):
        parts = [part for part in slug.split("-") if part]
        labels = [
            part.replace("_", " ").strip()
            for part in parts
            if part.replace("_", " ").strip()
        ]
        if not labels:
            continue
        department = labels[0]
        category = labels[1] if len(labels) > 1 else None
        subcategory = labels[2] if len(labels) > 2 else None
        dep_id = f"d:{department}"
        ensure(
            dep_id,
            name=department,
            path=department,
            parent_id=None,
            meta={"department": department},
        )
        parent = dep_id
        if category:
            cat_id = f"c:{department}|{category}"
            path = f"{department} > {category}"
            ensure(
                cat_id,
                name=category,
                path=path,
                parent_id=dep_id,
                meta={"department": department, "category": category},
            )
            nodes_map[dep_id]["has_children"] = True
            parent = cat_id
        if subcategory:
            sub_id = f"s:{department}|{category}|{subcategory}"
            path = f"{department} > {category} > {subcategory}"
            ensure(
                sub_id,
                name=subcategory,
                path=path,
                parent_id=parent,
                meta={
                    "department": department,
                    "category": category,
                    "subcategory": subcategory,
                },
            )
            nodes_map[parent]["has_children"] = True
    return list(nodes_map.values())


async def crawl_ebay_marketplace(
    client: httpx.AsyncClient,
    marketplace: str,
    *,
    max_expand: int = 25,
    delay_sec: float = 0.7,
) -> list[dict[str, Any]]:
    host = EBAY_MARKETPLACE_HOSTS.get(marketplace, "www.ebay.com")
    urls = [
        f"https://{host}/n/all-categories",
        f"https://{host}/sch/allcategories/all-categories",
    ]
    html = ""
    for url in urls:
        try:
            response = await client.get(url, timeout=60.0)
            if response.status_code < 400 and len(response.text) > 1000:
                html = response.text
                break
        except Exception as exc:
            logger.debug("eBay categories fetch failed %s: %s", url, exc)
    if not html:
        raise RuntimeError(f"eBay all-categories недоступен для {marketplace}")

    nodes = parse_ebay_all_categories_html(html, host=host)
    if len(nodes) < 30 and max_expand > 0:
        # Расширяем топ-корни подкатегориями
        roots = [n for n in nodes if not n.get("parent_id")][:max_expand]
        if not roots:
            # fallback: взять любые найденные id как корни для expand
            roots = nodes[:max_expand]
        extra: list[dict[str, Any]] = []
        for root in roots:
            await asyncio.sleep(delay_sec)
            category_id = str(root["id"])
            page_url = f"https://{host}/sch/{category_id}/i.html"
            try:
                response = await client.get(page_url, timeout=45.0)
                if response.status_code >= 400:
                    continue
                children = parse_ebay_subcategory_html(
                    response.text,
                    parent_id=category_id,
                    parent_path=str(root.get("path") or root.get("name") or ""),
                )
                if children:
                    root["has_children"] = True
                    extra.extend(children)
            except Exception as exc:
                logger.debug("eBay subcategory fetch failed %s: %s", category_id, exc)
        nodes = merge_taxonomy_nodes(nodes, extra)
    if len(nodes) < 10:
        raise RuntimeError(f"eBay parser: слишком мало категорий ({len(nodes)})")
    return nodes


async def crawl_etsy_categories(
    client: httpx.AsyncClient,
    *,
    proxy: str | None = None,
    max_pages: int = 40,
    delay_sec: float = 0.8,
) -> list[dict[str, Any]]:
    html = await _fetch_etsy_html(client, "https://www.etsy.com/categories", proxy=proxy)
    nodes = parse_etsy_categories_html(html)
    # Обходим найденные /c/ ссылки без taxonomy_id, чтобы добрать id
    soup = BeautifulSoup(html, "lxml")
    paths: list[str] = []
    for anchor in soup.select("a[href*='/c/']"):
        href = str(anchor.get("href") or "")
        slug = _etsy_slug(href)
        if slug and slug not in paths:
            paths.append(slug)
    for slug in paths[:max_pages]:
        await asyncio.sleep(delay_sec)
        url = f"https://www.etsy.com/c/{slug}"
        try:
            page_html = await _fetch_etsy_html(client, url, proxy=proxy)
            nodes = merge_taxonomy_nodes(nodes, parse_etsy_categories_html(page_html))
        except Exception as exc:
            logger.debug("Etsy category page failed %s: %s", slug, exc)
    # Оставляем узлы с taxonomy_id в приоритете; slug-only тоже полезны для UI,
    # но поиск Etsy сейчас требует taxonomy_id — отфильтруем slug-only без id
    usable = [
        n
        for n in nodes
        if (n.get("meta") or {}).get("taxonomy_id") is not None
        or str(n.get("id") or "").isdigit()
    ]
    if len(usable) < 8:
        # если id мало — вернём всё, что нашли (seed останется fallback на уровне сервиса)
        usable = nodes
    if len(usable) < 5:
        raise RuntimeError(f"Etsy parser: слишком мало категорий ({len(usable)})")
    return usable


async def crawl_poshmark_categories(
    client: httpx.AsyncClient,
    *,
    max_pages: int = 30,
    delay_sec: float = 0.7,
) -> list[dict[str, Any]]:
    response = await client.get("https://poshmark.com/", timeout=60.0)
    if response.status_code >= 400:
        raise RuntimeError(f"Poshmark home HTTP {response.status_code}")
    slugs = parse_poshmark_category_hrefs(response.text)
    # углубляем: обходим department-страницы
    departments = sorted({slug.split("-", 1)[0] for slug in slugs if slug})[:20]
    for department in departments[:max_pages]:
        await asyncio.sleep(delay_sec)
        url = f"https://poshmark.com/category/{department}"
        try:
            page = await client.get(url, timeout=45.0)
            if page.status_code < 400:
                slugs |= parse_poshmark_category_hrefs(page.text)
        except Exception as exc:
            logger.debug("Poshmark category page failed %s: %s", department, exc)
    nodes = poshmark_nodes_from_slugs(slugs)
    if len(nodes) < 15:
        raise RuntimeError(f"Poshmark parser: слишком мало категорий ({len(nodes)})")
    return nodes


async def _fetch_etsy_html(
    client: httpx.AsyncClient,
    url: str,
    *,
    proxy: str | None = None,
) -> str:
    try:
        response = await client.get(url, timeout=60.0)
        text = response.text or ""
        if response.status_code < 400 and not _looks_blocked(text) and len(text) > 800:
            return text
    except Exception as exc:
        logger.debug("Etsy httpx fetch failed %s: %s", url, exc)
    # Playwright fallback (тот же профиль, что для поиска)
    try:
        from bot.providers.etsy_browser import fetch_search_html

        return await fetch_search_html(url, proxy=proxy)
    except Exception as exc:
        raise RuntimeError(f"Etsy HTML недоступен ({url}): {exc}") from exc


def _looks_blocked(html: str) -> bool:
    low = (html or "").lower()
    return "datadome" in low or "captcha" in low and "etsy" in low


def _clean_name(value: str) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    text = re.sub(r"\s*\(\d[\d,]*\)\s*$", "", text)
    return text[:120]


def _name_from_ebay_href(href: str) -> str:
    path = urlparse(href).path
    for part in reversed(path.split("/")):
        if part and not part.isdigit() and part not in {"sch", "i.html", "b", "bn"}:
            return part.replace("-", " ").replace("_", " ").strip()
    return ""


def _etsy_taxonomy_id(href: str) -> str | None:
    match = ETSY_TAXONOMY_RE.search(href or "")
    if match:
        return match.group(1)
    parsed = urlparse(href)
    values = parse_qs(parsed.query).get("taxonomy_id") or []
    return values[0] if values else None


def _etsy_slug(href: str) -> str | None:
    match = ETSY_PATH_RE.search(href or "")
    if not match:
        return None
    return match.group(1).strip("/")


def make_parser_client(proxy: str | None = None) -> httpx.AsyncClient:
    return build_client(proxy, timeout=60.0)
