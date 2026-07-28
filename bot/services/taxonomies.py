from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from base64 import b64encode
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx

from bot.models import EBAY_MARKETPLACES, Source
from bot.providers.http_utils import build_client
from bot.services.categories import EBAY_SOURCES

logger = logging.getLogger(__name__)

TAXONOMY_TTL_SEC = 30 * 24 * 60 * 60  # месяц
EBAY_OAUTH_SCOPE = "https://api.ebay.com/oauth/api_scope"
ETSY_TAXONOMY_URL = "https://openapi.etsy.com/v3/application/seller-taxonomy/nodes"


class TaxonomyService:
    """Кэш деревьев категорий + поиск + ручное/месячное обновление."""

    def __init__(
        self,
        cache_dir: str | Path,
        *,
        proxy: str | None = None,
        ebay_client_id: str = "",
        ebay_client_secret: str = "",
        etsy_api_key: str = "",
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._proxy = proxy
        self._ebay_client_id = (ebay_client_id or "").strip()
        self._ebay_client_secret = (ebay_client_secret or "").strip()
        self._etsy_api_key = (etsy_api_key or "").strip()
        self._lock = asyncio.Lock()
        self._refreshing = False
        self._memory: dict[str, dict[str, Any]] = {}
        self._task: asyncio.Task[None] | None = None
        self._ensure_seeds()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._monthly_loop(), name="taxonomy-refresh")

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    @property
    def is_refreshing(self) -> bool:
        return self._refreshing

    def status(self) -> dict[str, Any]:
        trees: list[dict[str, Any]] = []
        for key in sorted({*self._memory, *self._list_cache_keys()}):
            tree = self._load(key)
            if not tree:
                continue
            trees.append(
                {
                    "key": key,
                    "source": tree.get("source"),
                    "marketplace": tree.get("marketplace"),
                    "nodes": len(tree.get("nodes") or []),
                    "updated_at": tree.get("updated_at"),
                    "stale": self._is_stale(tree),
                }
            )
        return {
            "ttl_days": TAXONOMY_TTL_SEC // 86400,
            "refreshing": self._refreshing,
            "trees": trees,
        }

    async def search(
        self,
        *,
        source: str,
        q: str,
        marketplace: str | None = None,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        tree = await self._tree_for(source, marketplace)
        query = (q or "").strip().lower()
        nodes = list(tree.get("nodes") or [])
        if not query:
            roots = [n for n in nodes if not n.get("parent_id")]
            return [_public_node(n) for n in roots[:limit]]
        scored: list[tuple[int, dict[str, Any]]] = []
        for node in nodes:
            name = str(node.get("name") or "").lower()
            path = str(node.get("path") or "").lower()
            if query in name:
                score = 0 if name.startswith(query) else 1
            elif query in path:
                score = 2
            else:
                continue
            scored.append((score, node))
        scored.sort(key=lambda item: (item[0], str(item[1].get("path") or "")))
        return [_public_node(node) for _, node in scored[: max(1, min(limit, 50))]]

    async def children(
        self,
        *,
        source: str,
        parent_id: str | None = None,
        marketplace: str | None = None,
    ) -> list[dict[str, Any]]:
        tree = await self._tree_for(source, marketplace)
        nodes = list(tree.get("nodes") or [])
        parent = parent_id or None
        if parent == "" or parent == "null":
            parent = None
        children = [n for n in nodes if (n.get("parent_id") or None) == parent]
        children.sort(key=lambda n: str(n.get("name") or ""))
        return [_public_node(n) for n in children]

    async def refresh(
        self,
        *,
        ebay_client_id: str | None = None,
        ebay_client_secret: str | None = None,
        etsy_api_key: str | None = None,
        force: bool = True,
    ) -> dict[str, Any]:
        async with self._lock:
            if self._refreshing:
                return {"ok": False, "message": "Обновление уже выполняется", "refreshing": True}
            self._refreshing = True
        try:
            results: dict[str, Any] = {}
            ebay_id = (ebay_client_id or self._ebay_client_id or "").strip()
            ebay_secret = (ebay_client_secret or self._ebay_client_secret or "").strip()
            etsy_key = (etsy_api_key or self._etsy_api_key or "").strip()

            if ebay_id and ebay_secret:
                try:
                    results["ebay"] = await self._refresh_ebay(ebay_id, ebay_secret)
                except Exception as exc:
                    logger.exception("eBay taxonomy refresh failed")
                    results["ebay"] = {"ok": False, "error": str(exc)}
            else:
                results["ebay"] = {
                    "ok": False,
                    "error": "Нет eBay Client ID/Secret для обновления taxonomy",
                }

            if etsy_key:
                try:
                    results["etsy"] = await self._refresh_etsy(etsy_key)
                except Exception as exc:
                    logger.exception("Etsy taxonomy refresh failed")
                    results["etsy"] = {"ok": False, "error": str(exc)}
            else:
                results["etsy"] = {
                    "ok": False,
                    "error": "Нет Etsy API key для обновления taxonomy",
                }

            try:
                results["poshmark"] = await self._refresh_poshmark()
            except Exception as exc:
                logger.exception("Poshmark taxonomy refresh failed")
                results["poshmark"] = {"ok": False, "error": str(exc)}

            ok_any = any(
                isinstance(value, dict) and value.get("ok") for value in results.values()
            )
            return {
                "ok": ok_any,
                "message": "Каталоги обновлены" if ok_any else "Не удалось обновить каталоги",
                "results": results,
                "status": self.status(),
            }
        finally:
            self._refreshing = False

    async def ensure_fresh_background(self) -> None:
        """Если кэш старше месяца — обновить в фоне (best-effort)."""
        stale = False
        for key in ("etsy", "poshmark", *(f"ebay:{m}" for m in EBAY_MARKETPLACES)):
            tree = self._load(key)
            if tree is None or self._is_stale(tree):
                stale = True
                break
        if not stale:
            return
        try:
            await self.refresh(force=True)
        except Exception:
            logger.exception("Background taxonomy refresh failed")

    async def _monthly_loop(self) -> None:
        await asyncio.sleep(15)
        while True:
            try:
                await self.ensure_fresh_background()
            except Exception:
                logger.exception("taxonomy monthly loop error")
            await asyncio.sleep(24 * 60 * 60)

    async def _tree_for(self, source: str, marketplace: str | None) -> dict[str, Any]:
        key = self._cache_key(source, marketplace)
        tree = self._load(key)
        if tree is None:
            # fallback: ebay without market → EBAY_US; seed
            if key.startswith("ebay:"):
                tree = self._load("ebay:EBAY_US")
            if tree is None:
                tree = self._seed_tree(key)
                self._save(key, tree)
        return tree

    def _cache_key(self, source: str, marketplace: str | None) -> str:
        value = (source or "").strip().lower()
        if value in {"ebay", "ebay_api", "ebay_parser"}:
            market = marketplace if marketplace in EBAY_MARKETPLACES else "EBAY_US"
            return f"ebay:{market}"
        if value == "etsy":
            return "etsy"
        if value == "poshmark":
            return "poshmark"
        raise ValueError(f"Неизвестный source для категорий: {source}")

    def _path_for(self, key: str) -> Path:
        safe = key.replace(":", "_")
        return self.cache_dir / f"{safe}.json"

    def _list_cache_keys(self) -> list[str]:
        keys: list[str] = []
        for path in self.cache_dir.glob("*.json"):
            name = path.stem
            if name.startswith("ebay_"):
                keys.append("ebay:" + name.removeprefix("ebay_"))
            else:
                keys.append(name)
        return keys

    def _load(self, key: str) -> dict[str, Any] | None:
        if key in self._memory:
            return self._memory[key]
        path = self._path_for(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Broken taxonomy cache %s", path)
            return None
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
            return None
        self._memory[key] = data
        return data

    def _save(self, key: str, tree: dict[str, Any]) -> None:
        path = self._path_for(key)
        path.write_text(
            json.dumps(tree, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        self._memory[key] = tree

    def _is_stale(self, tree: dict[str, Any]) -> bool:
        updated = tree.get("updated_at")
        if not updated:
            return True
        try:
            ts = datetime.fromisoformat(str(updated).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return True
        return (time.time() - ts) > TAXONOMY_TTL_SEC

    def _ensure_seeds(self) -> None:
        for market in EBAY_MARKETPLACES:
            key = f"ebay:{market}"
            if self._load(key) is None:
                self._save(key, self._seed_tree(key))
        for key in ("etsy", "poshmark"):
            if self._load(key) is None:
                self._save(key, self._seed_tree(key))

    def _seed_tree(self, key: str) -> dict[str, Any]:
        now = _utcnow()
        if key.startswith("ebay:"):
            market = key.split(":", 1)[1]
            return {
                "source": "ebay",
                "marketplace": market,
                "updated_at": now,
                "nodes": _ebay_seed_nodes(),
            }
        if key == "etsy":
            return {
                "source": "etsy",
                "marketplace": None,
                "updated_at": now,
                "nodes": _etsy_seed_nodes(),
            }
        return {
            "source": "poshmark",
            "marketplace": None,
            "updated_at": now,
            "nodes": _poshmark_seed_nodes(),
        }

    async def _refresh_ebay(self, client_id: str, client_secret: str) -> dict[str, Any]:
        client = build_client(self._proxy)
        try:
            token = await _ebay_app_token(client, client_id, client_secret)
            updated = 0
            for market in EBAY_MARKETPLACES:
                tree_id = await _ebay_default_tree_id(client, token, market)
                nodes = await _ebay_flatten_tree(client, token, tree_id)
                self._save(
                    f"ebay:{market}",
                    {
                        "source": "ebay",
                        "marketplace": market,
                        "updated_at": _utcnow(),
                        "tree_id": tree_id,
                        "nodes": nodes,
                    },
                )
                updated += 1
            return {"ok": True, "markets": updated}
        finally:
            await client.aclose()

    async def _refresh_etsy(self, api_key: str) -> dict[str, Any]:
        client = build_client(self._proxy)
        try:
            response = await client.get(
                ETSY_TAXONOMY_URL,
                headers={"Accept": "application/json", "x-api-key": api_key},
                timeout=120.0,
            )
            if response.status_code >= 400:
                raise RuntimeError(f"Etsy taxonomy HTTP {response.status_code}")
            payload = response.json()
            roots = payload.get("results") or payload.get("nodes") or []
            nodes = _flatten_etsy_nodes(roots)
            self._save(
                "etsy",
                {
                    "source": "etsy",
                    "marketplace": None,
                    "updated_at": _utcnow(),
                    "nodes": nodes,
                },
            )
            return {"ok": True, "nodes": len(nodes)}
        finally:
            await client.aclose()

    async def _refresh_poshmark(self) -> dict[str, Any]:
        client = build_client(self._proxy)
        try:
            nodes = await _fetch_poshmark_nodes(client)
            if len(nodes) < 20:
                # слишком мало — оставить seed/текущий
                current = self._load("poshmark") or self._seed_tree("poshmark")
                nodes = list(current.get("nodes") or [])
                return {"ok": False, "error": "Poshmark вернул слишком мало категорий", "nodes": len(nodes)}
            self._save(
                "poshmark",
                {
                    "source": "poshmark",
                    "marketplace": None,
                    "updated_at": _utcnow(),
                    "nodes": nodes,
                },
            )
            return {"ok": True, "nodes": len(nodes)}
        finally:
            await client.aclose()


def _public_node(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(node.get("id")),
        "name": node.get("name"),
        "path": node.get("path"),
        "parent_id": node.get("parent_id"),
        "has_children": bool(node.get("has_children")),
        "meta": node.get("meta") or {},
    }


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


async def _ebay_app_token(client: httpx.AsyncClient, client_id: str, client_secret: str) -> str:
    credentials = b64encode(f"{client_id}:{client_secret}".encode()).decode()
    response = await client.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "client_credentials", "scope": EBAY_OAUTH_SCOPE},
        timeout=60.0,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"eBay OAuth HTTP {response.status_code}")
    return str(response.json()["access_token"])


async def _ebay_default_tree_id(
    client: httpx.AsyncClient, token: str, marketplace: str
) -> str:
    response = await client.get(
        "https://api.ebay.com/commerce/taxonomy/v1/get_default_category_tree_id",
        params={"marketplace_id": marketplace},
        headers={"Authorization": f"Bearer {token}"},
        timeout=60.0,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"eBay tree id HTTP {response.status_code} for {marketplace}")
    return str(response.json()["categoryTreeId"])


async def _ebay_flatten_tree(
    client: httpx.AsyncClient, token: str, tree_id: str
) -> list[dict[str, Any]]:
    response = await client.get(
        f"https://api.ebay.com/commerce/taxonomy/v1/category_tree/{tree_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept-Encoding": "gzip",
        },
        timeout=180.0,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"eBay category tree HTTP {response.status_code}")
    root = response.json().get("rootCategoryNode") or {}
    nodes: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], parent_id: str | None, path_prefix: str) -> None:
        category = node.get("category") or {}
        category_id = str(category.get("categoryId") or "")
        name = str(category.get("categoryName") or "").strip()
        if not category_id or not name:
            return
        path = f"{path_prefix} > {name}" if path_prefix else name
        children = list(node.get("childCategoryTreeNodes") or [])
        nodes.append(
            {
                "id": category_id,
                "name": name,
                "path": path,
                "parent_id": parent_id,
                "has_children": bool(children),
                "meta": {"category_id": category_id},
            }
        )
        for child in children:
            walk(child, category_id, path)

    # eBay root often is placeholder — walk children as top-level if name is generic
    root_cat = root.get("category") or {}
    root_name = str(root_cat.get("categoryName") or "")
    root_id = str(root_cat.get("categoryId") or "")
    children = list(root.get("childCategoryTreeNodes") or [])
    if root_name.lower() in {"root", "ebay", ""} and children:
        for child in children:
            walk(child, None, "")
    elif root_id:
        walk(root, None, "")
    return nodes


def _flatten_etsy_nodes(roots: list[Any], parent_id: str | None = None, path_prefix: str = "") -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for item in roots:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id") or item.get("taxonomy_id")
        name = str(item.get("name") or "").strip()
        if raw_id is None or not name:
            continue
        node_id = str(raw_id)
        path = f"{path_prefix} > {name}" if path_prefix else name
        children = list(item.get("children") or [])
        nodes.append(
            {
                "id": node_id,
                "name": name,
                "path": path,
                "parent_id": parent_id,
                "has_children": bool(children),
                "meta": {"taxonomy_id": int(raw_id)},
            }
        )
        if children:
            nodes.extend(_flatten_etsy_nodes(children, parent_id=node_id, path_prefix=path))
    return nodes


async def _fetch_poshmark_nodes(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Собирает дерево из публичных category URL Poshmark."""
    response = await client.get(
        "https://poshmark.com/",
        headers={"Accept": "text/html"},
        timeout=60.0,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Poshmark home HTTP {response.status_code}")
    hrefs = set(re.findall(r'href="(/category/[^"?#]+)"', response.text))
    # также иногда в JSON
    hrefs.update(re.findall(r'"(/category/[^"?#]+)"', response.text))
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
        # keep longer path/name if needed
        if len(path) > len(str(existing.get("path") or "")):
            existing["path"] = path
            existing["name"] = name

    for href in sorted(hrefs):
        slug = unquote(href.split("/category/", 1)[-1]).strip("/")
        if not slug:
            continue
        parts = [part for part in slug.split("-") if part]
        # Poshmark encodes spaces as underscores inside segments joined by '-'
        # e.g. Women-Bags-Crossbody_Bags → Women / Bags / Crossbody Bags
        labels = [part.replace("_", " ").strip() for part in parts if part.replace("_", " ").strip()]
        if not labels:
            continue
        department = labels[0]
        category = labels[1] if len(labels) > 1 else None
        subcategory = labels[2] if len(labels) > 2 else None
        # department
        dep_id = f"d:{department}"
        ensure(
            dep_id,
            name=department,
            path=department,
            parent_id=None,
            meta={"department": department},
        )
        parent = dep_id
        path = department
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


def _ebay_seed_nodes() -> list[dict[str, Any]]:
    # Основные корни eBay US + популярные ветки (полный каталог — через refresh).
    roots = [
        ("1", "Collectibles"),
        ("220", "Toys & Hobbies"),
        ("237", "Dolls & Bears"),
        ("260", "Stamps"),
        ("267", "Books & Magazines"),
        ("281", "Jewelry & Watches"),
        ("293", "Electronics"),
        ("550", "Art"),
        ("619", "Musical Instruments & Gear"),
        ("625", "Cameras & Photo"),
        ("870", "Pottery & Glass"),
        ("888", "Sporting Goods"),
        ("1249", "Video Games & Consoles"),
        ("1281", "Pet Supplies"),
        ("14339", "Crafts"),
        ("2984", "Baby"),
        ("11700", "Home & Garden"),
        ("11450", "Clothing, Shoes & Accessories"),
        ("58058", "Computers/Tablets & Networking"),
        ("6000", "eBay Motors"),
        ("26395", "Health & Beauty"),
        ("45100", "Entertainment Memorabilia"),
        ("64482", "Sporting Memorabilia"),
    ]
    clothing = [
        ("1059", "11450", "Men"),
        ("15724", "11450", "Women"),
        ("11507", "11450", "Kids"),
        ("4250", "11450", "Shoes"),
        ("45258", "11450", "Handbags"),
        ("163570", "11450", "Accessories"),
    ]
    nodes = [
        {
            "id": cid,
            "name": name,
            "path": name,
            "parent_id": None,
            "has_children": cid == "11450",
            "meta": {"category_id": cid},
        }
        for cid, name in roots
    ]
    for cid, parent, name in clothing:
        nodes.append(
            {
                "id": cid,
                "name": name,
                "path": f"Clothing, Shoes & Accessories > {name}",
                "parent_id": parent,
                "has_children": False,
                "meta": {"category_id": cid},
            }
        )
    return nodes


def _etsy_seed_nodes() -> list[dict[str, Any]]:
    roots = [
        (1, "Accessories"),
        (2, "Art & Collectibles"),
        (3, "Bags & Purses"),
        (4, "Bath & Beauty"),
        (5, "Books, Movies & Music"),
        (6, "Clothing"),
        (7, "Craft Supplies & Tools"),
        (8, "Electronics & Accessories"),
        (9, "Home & Living"),
        (10, "Jewelry"),
        (11, "Paper & Party Supplies"),
        (12, "Pet Supplies"),
        (13, "Shoes"),
        (14, "Toys & Games"),
        (15, "Weddings"),
    ]
    return [
        {
            "id": str(tid),
            "name": name,
            "path": name,
            "parent_id": None,
            "has_children": False,
            "meta": {"taxonomy_id": tid},
        }
        for tid, name in roots
    ]


def _poshmark_seed_nodes() -> list[dict[str, Any]]:
    tree = {
        "Women": [
            "Bags",
            "Dresses",
            "Jackets & Coats",
            "Jeans",
            "Jewelry",
            "Pants",
            "Shoes",
            "Tops",
            "Accessories",
        ],
        "Men": ["Bags", "Jackets & Coats", "Jeans", "Shoes", "Tops", "Accessories"],
        "Kids": ["Boys", "Girls", "Toys", "Shoes"],
        "Home": ["Accent", "Bath", "Bedding", "Kitchen"],
        "Electronics": ["Cell Phones", "Computers", "Cameras"],
        "Pets": ["Dog", "Cat"],
    }
    nodes: list[dict[str, Any]] = []
    for department, categories in tree.items():
        dep_id = f"d:{department}"
        nodes.append(
            {
                "id": dep_id,
                "name": department,
                "path": department,
                "parent_id": None,
                "has_children": True,
                "meta": {"department": department},
            }
        )
        for category in categories:
            cat_id = f"c:{department}|{category}"
            nodes.append(
                {
                    "id": cat_id,
                    "name": category,
                    "path": f"{department} > {category}",
                    "parent_id": dep_id,
                    "has_children": False,
                    "meta": {"department": department, "category": category},
                }
            )
    return nodes


def taxonomy_source_for_api(source: Source | str) -> str:
    value = source.value if isinstance(source, Source) else str(source)
    if value in {s.value for s in EBAY_SOURCES} or value == "ebay":
        return "ebay"
    return value
