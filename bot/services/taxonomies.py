from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.models import EBAY_MARKETPLACES, Source
from bot.services.categories import EBAY_SOURCES
from bot.services.taxonomy_parsers import (
    crawl_ebay_marketplace,
    crawl_etsy_categories,
    crawl_poshmark_categories,
    make_parser_client,
)

logger = logging.getLogger(__name__)

TAXONOMY_TTL_SEC = 30 * 24 * 60 * 60  # месяц


class TaxonomyService:
    """Кэш деревьев категорий + поиск + ручное/месячное обновление парсером."""

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
        # legacy kwargs ignored — refresh только парсером, без API-ключей
        _ = (ebay_client_id, ebay_client_secret, etsy_api_key)
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
                    "method": tree.get("method") or "seed",
                }
            )
        return {
            "ttl_days": TAXONOMY_TTL_SEC // 86400,
            "refreshing": self._refreshing,
            "mode": "parser",
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
        _ = (ebay_client_id, ebay_client_secret, etsy_api_key, force)
        async with self._lock:
            if self._refreshing:
                return {"ok": False, "message": "Обновление уже выполняется", "refreshing": True}
            self._refreshing = True
        try:
            results: dict[str, Any] = {}
            try:
                results["ebay"] = await self._refresh_ebay_parser()
            except Exception as exc:
                logger.exception("eBay parser taxonomy refresh failed")
                results["ebay"] = {"ok": False, "error": str(exc)}
            try:
                results["etsy"] = await self._refresh_etsy_parser()
            except Exception as exc:
                logger.exception("Etsy parser taxonomy refresh failed")
                results["etsy"] = {"ok": False, "error": str(exc)}
            try:
                results["poshmark"] = await self._refresh_poshmark_parser()
            except Exception as exc:
                logger.exception("Poshmark taxonomy refresh failed")
                results["poshmark"] = {"ok": False, "error": str(exc)}

            ok_any = any(
                isinstance(value, dict) and value.get("ok") for value in results.values()
            )
            return {
                "ok": ok_any,
                "message": (
                    "Каталоги обновлены парсером"
                    if ok_any
                    else "Не удалось обновить каталоги парсером"
                ),
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
                "method": "seed",
                "nodes": _ebay_seed_nodes(),
            }
        if key == "etsy":
            return {
                "source": "etsy",
                "marketplace": None,
                "updated_at": now,
                "method": "seed",
                "nodes": _etsy_seed_nodes(),
            }
        return {
            "source": "poshmark",
            "marketplace": None,
            "updated_at": now,
            "method": "seed",
            "nodes": _poshmark_seed_nodes(),
        }

    async def _refresh_ebay_parser(self) -> dict[str, Any]:
        client = make_parser_client(self._proxy)
        try:
            updated = 0
            total_nodes = 0
            errors: list[str] = []
            for market in EBAY_MARKETPLACES:
                try:
                    # Полный expand только для US — остальные быстрее с all-categories.
                    expand = 20 if market == "EBAY_US" else 0
                    nodes = await crawl_ebay_marketplace(
                        client, market, max_expand=expand
                    )
                    self._save(
                        f"ebay:{market}",
                        {
                            "source": "ebay",
                            "marketplace": market,
                            "updated_at": _utcnow(),
                            "method": "parser",
                            "nodes": nodes,
                        },
                    )
                    updated += 1
                    total_nodes += len(nodes)
                except Exception as exc:
                    logger.warning("eBay parser refresh failed for %s: %s", market, exc)
                    errors.append(f"{market}: {exc}")
            if updated == 0:
                return {"ok": False, "error": "; ".join(errors) or "eBay parser failed"}
            return {
                "ok": True,
                "markets": updated,
                "nodes": total_nodes,
                "errors": errors or None,
            }
        finally:
            await client.aclose()

    async def _refresh_etsy_parser(self) -> dict[str, Any]:
        client = make_parser_client(self._proxy)
        try:
            nodes = await crawl_etsy_categories(client, proxy=self._proxy)
            self._save(
                "etsy",
                {
                    "source": "etsy",
                    "marketplace": None,
                    "updated_at": _utcnow(),
                    "method": "parser",
                    "nodes": nodes,
                },
            )
            return {"ok": True, "nodes": len(nodes)}
        finally:
            await client.aclose()

    async def _refresh_poshmark_parser(self) -> dict[str, Any]:
        client = make_parser_client(self._proxy)
        try:
            nodes = await crawl_poshmark_categories(client)
            self._save(
                "poshmark",
                {
                    "source": "poshmark",
                    "marketplace": None,
                    "updated_at": _utcnow(),
                    "method": "parser",
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


def _ebay_seed_nodes() -> list[dict[str, Any]]:
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
