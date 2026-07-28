from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, model_validator

from bot.db import Database
from bot.models import (
    EBAY_MARKETPLACE_LABELS,
    EBAY_MARKETPLACES,
    NON_EBAY_SOURCES,
    SOURCE_LABELS,
    Search,
    Source,
    User,
    UserStatus,
)
from bot.providers.ebay_api import EbayApiProvider
from bot.providers.etsy import EtsyProvider
from bot.services.categories import categories_for_search, normalize_categories_payload
from bot.services.credentials import CredentialsService, normalize_etsy_api_key
from bot.services.poller import PollerService
from bot.services.taxonomies import TaxonomyService, taxonomy_source_for_api
from bot.web.deletion import deletion_endpoint
from bot.web.telegram_auth import TelegramAuthError, validate_init_data

logger = logging.getLogger(__name__)


class SearchCreateIn(BaseModel):
    source: Source | None = None
    sources: list[Source] = Field(default_factory=list, max_length=len(Source))
    keywords: str = Field(min_length=2, max_length=200)
    min_price: float | None = None
    max_price: float | None = None
    condition: str | None = None
    buy_it_now: bool = True
    marketplace: str | None = None
    categories: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_sources(self):
        selected = list(dict.fromkeys([*self.sources, *([self.source] if self.source else [])]))
        if not selected:
            raise ValueError("Выберите хотя бы один источник")
        self.sources = selected
        return self


class SearchUpdateIn(BaseModel):
    sources: list[Source] | None = Field(default=None, min_length=1, max_length=len(Source))
    keywords: str | None = Field(default=None, min_length=2, max_length=200)
    min_price: float | None = None
    max_price: float | None = None
    condition: str | None = None
    buy_it_now: bool | None = None
    paused: bool | None = None
    clear_prices: bool = False
    clear_min_price: bool = False
    clear_max_price: bool = False
    marketplace: str | None = None
    categories: dict[str, Any] | None = None


class SetupIn(BaseModel):
    enabled_sources: list[Source] = Field(min_length=1)
    ebay_marketplace: str = "EBAY_US"
    ebay_client_id: str | None = None
    ebay_client_secret: str | None = None
    etsy_keystring: str | None = None
    etsy_shared_secret: str | None = None


def create_api_router(
    db: Database,
    bot_token: str,
    *,
    credentials: CredentialsService,
    public_base_url: str,
    bot_username: str = "",
    http_proxy: str = "",
    poller: PollerService | None = None,
    taxonomies: TaxonomyService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["miniapp"])

    async def current_user(
        x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
    ) -> User:
        if not x_telegram_init_data:
            raise HTTPException(status_code=401, detail="Нужен X-Telegram-Init-Data")
        try:
            tg_user = validate_init_data(x_telegram_init_data, bot_token)
        except TelegramAuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

        user = await db.get_user(tg_user.id)
        if user is None:
            raise HTTPException(status_code=403, detail="Сначала нажмите /start в боте")
        if user.status is UserStatus.BLOCKED:
            raise HTTPException(status_code=403, detail="Доступ заблокирован")
        if user.status is UserStatus.REJECTED:
            raise HTTPException(status_code=403, detail="Заявка отклонена")
        if user.status is UserStatus.PENDING:
            raise HTTPException(status_code=403, detail="Ожидайте одобрения админа")
        return user

    async def _settings_payload(user: User) -> dict[str, Any]:
        has_keys = await db.has_credentials(user.telegram_id)
        has_etsy_keys = await db.has_etsy_credentials(user.telegram_id)
        deletion_token = None
        deletion_url = None
        if has_keys or Source.EBAY_API in user.enabled_sources:
            deletion_token = await db.ensure_deletion_token(user.telegram_id)
            deletion_url = deletion_endpoint(
                public_base_url,
                user.telegram_id,
                deletion_token,
            )
        return {
            "telegram_id": user.telegram_id,
            "username": user.username,
            "full_name": user.full_name,
            "bot_username": bot_username,
            "status": user.status.value,
            "setup_completed": user.setup_completed,
            "enabled_sources": [s.value for s in user.enabled_sources],
            "source_labels": {s.value: SOURCE_LABELS[s] for s in Source},
            "ebay_marketplace": user.ebay_marketplace,
            "ebay_marketplaces": list(EBAY_MARKETPLACES),
            "ebay_marketplace_labels": dict(EBAY_MARKETPLACE_LABELS),
            "has_ebay_keys": has_keys,
            "has_etsy_keys": has_etsy_keys,
            "deletion_url": deletion_url,
            "deletion_token": deletion_token,
            "ebay_checklist": [
                "Зайдите на https://developer.ebay.com",
                "Application Keys → создайте/откройте приложение",
                "Скопируйте App ID (Client ID) и Cert ID (Client Secret)",
                "Для Production укажите Marketplace Account Deletion URL и token ниже",
                "Scope: https://api.ebay.com/oauth/api_scope",
            ],
            "etsy_checklist": [
                "Etsy работает через Playwright — API-ключ не обязателен",
                "При желании можно сохранить Open API ключ (быстрее)",
                "developers.etsy.com → Keystring и Shared Secret",
            ],
        }

    @router.get("/me")
    async def api_me(user: User = Depends(current_user)) -> dict[str, Any]:
        return await _settings_payload(user)

    @router.get("/searches")
    async def api_list_searches(user: User = Depends(current_user)) -> dict[str, Any]:
        searches = await db.list_searches(user.telegram_id)
        grouped: dict[str, list[Search]] = {}
        for search in searches:
            grouped.setdefault(search.group_key, []).append(search)
        items: list[dict[str, Any]] = []
        for group in grouped.values():
            representative = group[0]
            marketplaces = [
                item.marketplace for item in group if item.marketplace is not None
            ]
            categories: dict[str, list[dict[str, Any]]] = {}
            for item in group:
                cats = categories_for_search(item.filters_json, item.source)
                if cats:
                    categories[item.source.value] = cats
            items.append(
                {
                    "id": representative.id,
                    "group_key": representative.group_key,
                    "sources": [item.source.value for item in group],
                    "source_labels": [SOURCE_LABELS[item.source] for item in group],
                    # Поля совместимости для старых клиентов.
                    "source": representative.source.value,
                    "source_label": SOURCE_LABELS[representative.source],
                    "keywords": representative.keywords,
                    "min_price": representative.min_price,
                    "max_price": representative.max_price,
                    "condition": representative.condition,
                    "buy_it_now": any(item.buy_it_now for item in group),
                    "paused": all(item.paused for item in group),
                    "marketplace": marketplaces[0] if marketplaces else None,
                    "marketplace_label": (
                        EBAY_MARKETPLACE_LABELS.get(marketplaces[0], marketplaces[0])
                        if marketplaces
                        else None
                    ),
                    "categories": categories,
                }
            )
        return {
            "items": items
        }

    @router.get("/poll-logs")
    async def api_list_poll_logs(user: User = Depends(current_user)) -> dict[str, Any]:
        logs = await db.list_poll_logs(user.telegram_id, limit=40)
        return {
            "items": [
                {
                    "id": item.id,
                    "search_id": item.search_id,
                    "source": item.source.value,
                    "source_label": SOURCE_LABELS[item.source],
                    "keywords": item.keywords,
                    "status": item.status,
                    "found": item.found,
                    "new_items": item.new_items,
                    "notified": item.notified,
                    "message": item.message,
                    "created_at": item.created_at,
                }
                for item in logs
            ]
        }

    @router.post("/searches")
    async def api_create_search(
        payload: SearchCreateIn,
        user: User = Depends(current_user),
    ) -> dict[str, Any]:
        if not user.setup_completed or not user.enabled_sources:
            raise HTTPException(status_code=400, detail="Сначала завершите настройки")
        selected_sources = payload.sources
        unavailable = [
            source for source in selected_sources if source not in user.enabled_sources
        ]
        if unavailable:
            raise HTTPException(status_code=400, detail="Источник не включён в настройках")

        marketplace: str | None = None
        if any(source not in NON_EBAY_SOURCES for source in selected_sources):
            marketplace = payload.marketplace or user.ebay_marketplace or "EBAY_US"
            if marketplace not in EBAY_MARKETPLACES:
                raise HTTPException(status_code=400, detail="Неизвестный marketplace")

        for source in selected_sources:
            source_marketplace = None if source in NON_EBAY_SOURCES else marketplace
            duplicate = await db.find_identical_search(
                user.telegram_id,
                source,
                payload.keywords,
                max_price=payload.max_price,
                min_price=payload.min_price,
                condition=payload.condition,
                buy_it_now=payload.buy_it_now if source not in NON_EBAY_SOURCES else False,
                marketplace=source_marketplace,
            )
            if duplicate is not None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Такой поиск уже есть для {SOURCE_LABELS[source]} "
                        f"(#{duplicate.id})."
                    ),
                )

        searches = await db.add_search_group(
            user.telegram_id,
            selected_sources,
            payload.keywords,
            max_price=payload.max_price,
            min_price=payload.min_price,
            condition=payload.condition,
            buy_it_now=payload.buy_it_now,
            marketplace=marketplace,
            categories_by_source=normalize_categories_payload(payload.categories),
        )
        search = searches[0]
        if poller is not None:
            for item in searches:
                poller.schedule_search(item, notify=False, record_log=False)
            try:
                market_bit = (
                    f" · {EBAY_MARKETPLACE_LABELS.get(marketplace, marketplace)}"
                    if marketplace
                    else ""
                )
                price_bits: list[str] = []
                if search.min_price is not None:
                    price_bits.append(f"от {search.min_price:g}")
                if search.max_price is not None:
                    price_bits.append(f"до {search.max_price:g}")
                price_bit = f" · {' '.join(price_bits)}" if price_bits else ""
                await poller.bot.send_message(
                    user.telegram_id,
                    (
                        f"✅ Новый поиск создан #{search.id}\n"
                        f"{', '.join(SOURCE_LABELS[item.source] for item in searches)}"
                        f"{market_bit}\n"
                        f"<code>{search.keywords}</code>{price_bit}\n\n"
                        "Новые лоты будут приходить в этот чат."
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                logger.warning(
                    "Failed to notify user=%s about new search=%s",
                    user.telegram_id,
                    search.id,
                    exc_info=True,
                )
        return {
            "id": search.id,
            "ok": True,
            "message": f"Новый поиск создан #{search.id}",
            "keywords": search.keywords,
            "sources": [item.source.value for item in searches],
            "source_labels": [SOURCE_LABELS[item.source] for item in searches],
        }

    @router.patch("/searches/{search_id}")
    async def api_update_search(
        search_id: int,
        payload: SearchUpdateIn,
        user: User = Depends(current_user),
    ) -> dict[str, Any]:
        group = await db.get_search_group_by_id(search_id, user.telegram_id)
        if not group:
            raise HTTPException(status_code=404, detail="Поиск не найден")
        search = group[0]
        selected_sources = payload.sources or [item.source for item in group]
        if any(source not in user.enabled_sources for source in selected_sources):
            raise HTTPException(status_code=400, detail="Источник не включён в настройках")
        marketplace = None
        if any(source not in NON_EBAY_SOURCES for source in selected_sources):
            marketplace = (
                payload.marketplace
                or next((item.marketplace for item in group if item.marketplace), None)
                or user.ebay_marketplace
                or "EBAY_US"
            )
            if marketplace not in EBAY_MARKETPLACES:
                raise HTTPException(status_code=400, detail="Неизвестный marketplace")

        new_keywords = payload.keywords or search.keywords
        new_min = None if payload.clear_prices or payload.clear_min_price else (
            payload.min_price if payload.min_price is not None else search.min_price
        )
        new_max = None if payload.clear_prices or payload.clear_max_price else (
            payload.max_price if payload.max_price is not None else search.max_price
        )
        new_condition = (
            payload.condition if payload.condition is not None else search.condition
        )
        for source in selected_sources:
            existing = next((item for item in group if item.source is source), None)
            source_bin = False if source in NON_EBAY_SOURCES else (
                payload.buy_it_now
                if payload.buy_it_now is not None
                else existing.buy_it_now if existing is not None else True
            )
            duplicate = await db.find_identical_search(
                user.telegram_id,
                source,
                new_keywords,
                max_price=new_max,
                min_price=new_min,
                condition=new_condition,
                buy_it_now=source_bin,
                marketplace=None if source in NON_EBAY_SOURCES else marketplace,
                exclude_group_key=search.group_key,
            )
            if duplicate is not None:
                raise HTTPException(
                    status_code=409,
                    detail=f"Такой поиск уже есть для {SOURCE_LABELS[source]}.",
                )

        if payload.paused is not None:
            await db.set_search_group_paused(
                search_id,
                user.telegram_id,
                payload.paused,
            )

        updated = await db.update_search_group(
            search_id,
            user.telegram_id,
            sources=selected_sources,
            keywords=payload.keywords,
            min_price=payload.min_price,
            max_price=payload.max_price,
            condition=payload.condition,
            buy_it_now=payload.buy_it_now,
            clear_max_price=payload.clear_prices or payload.clear_max_price,
            clear_min_price=payload.clear_prices or payload.clear_min_price,
            marketplace=marketplace,
            categories_by_source=(
                normalize_categories_payload(payload.categories)
                if payload.categories is not None
                else None
            ),
            update_categories=payload.categories is not None,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Поиск не найден")
        criteria_changed = any(
            (
                payload.sources is not None,
                payload.keywords is not None,
                payload.min_price is not None,
                payload.max_price is not None,
                payload.condition is not None,
                payload.buy_it_now is not None,
                payload.clear_prices,
                payload.clear_min_price,
                payload.clear_max_price,
                payload.categories is not None,
            )
        )
        if criteria_changed and poller is not None:
            for item in updated:
                poller.schedule_search(item, notify=False, record_log=False)
        return {
            "ok": True,
            "paused": all(item.paused for item in updated),
            "sources": [item.source.value for item in updated],
        }

    @router.delete("/searches/{search_id}")
    async def api_delete_search(
        search_id: int,
        user: User = Depends(current_user),
    ) -> dict[str, Any]:
        ok = await db.delete_search_group(search_id, user.telegram_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Поиск не найден")
        return {"ok": True}

    @router.post("/setup")
    async def api_setup(
        payload: SetupIn,
        user: User = Depends(current_user),
    ) -> dict[str, Any]:
        marketplace = payload.ebay_marketplace or "EBAY_US"
        if marketplace not in EBAY_MARKETPLACES:
            raise HTTPException(status_code=400, detail="Неизвестный marketplace")

        client_id = (payload.ebay_client_id or "").strip()
        client_secret = (payload.ebay_client_secret or "").strip()
        wants_api = Source.EBAY_API in payload.enabled_sources
        has_keys = await db.has_credentials(user.telegram_id)

        etsy_keystring = (payload.etsy_keystring or "").strip()
        etsy_secret = (payload.etsy_shared_secret or "").strip()
        etsy_api_key = normalize_etsy_api_key(etsy_keystring, etsy_secret)
        wants_etsy = Source.ETSY in payload.enabled_sources
        etsy_verified = False

        if wants_api:
            if client_id and client_secret:
                provider = EbayApiProvider(
                    client_id=client_id,
                    client_secret=client_secret,
                    marketplace_id=marketplace,
                    telegram_id=user.telegram_id,
                    proxy=http_proxy or None,
                )
                try:
                    await provider.verify_credentials()
                except Exception as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=f"OAuth не прошёл: {exc}",
                    ) from exc
                finally:
                    await provider.aclose()
                await credentials.save_ebay_keys(
                    user.telegram_id, client_id, client_secret
                )
                has_keys = True
            elif not has_keys:
                raise HTTPException(
                    status_code=400,
                    detail="Для eBay API укажите Client ID и Client Secret",
                )

        if wants_etsy and etsy_api_key:
            provider = EtsyProvider(
                proxy=http_proxy or None,
                api_key=etsy_api_key,
            )
            try:
                await provider.verify_credentials()
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Etsy API ключ не принят: {exc}",
                ) from exc
            finally:
                await provider.aclose()
            try:
                await credentials.save_etsy_key(
                    user.telegram_id, etsy_keystring, etsy_secret
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            etsy_verified = True

        await db.save_setup(
            user.telegram_id,
            enabled_sources=payload.enabled_sources,
            ebay_marketplace=marketplace,
            setup_completed=True,
        )
        user = await db.get_user(user.telegram_id)
        assert user is not None
        result = await _settings_payload(user)
        result["ok"] = True
        result["oauth_verified"] = bool(client_id and client_secret and wants_api)
        result["etsy_verified"] = etsy_verified
        return result

    @router.delete("/keys")
    async def api_revoke_keys(user: User = Depends(current_user)) -> dict[str, Any]:
        removed = await credentials.revoke(user.telegram_id)
        # Если eBay API был включён — убираем его из источников
        fresh = await db.get_user(user.telegram_id)
        assert fresh is not None
        if Source.EBAY_API in fresh.enabled_sources:
            remaining = [s for s in fresh.enabled_sources if s is not Source.EBAY_API]
            await db.save_setup(
                fresh.telegram_id,
                enabled_sources=remaining,
                ebay_marketplace=fresh.ebay_marketplace,
                setup_completed=bool(remaining) and fresh.setup_completed,
            )
        return {"ok": True, "removed": removed}

    @router.delete("/keys/etsy")
    async def api_revoke_etsy_keys(user: User = Depends(current_user)) -> dict[str, Any]:
        removed = await credentials.revoke_etsy(user.telegram_id)
        fresh = await db.get_user(user.telegram_id)
        assert fresh is not None
        if Source.ETSY in fresh.enabled_sources:
            remaining = [s for s in fresh.enabled_sources if s is not Source.ETSY]
            await db.save_setup(
                fresh.telegram_id,
                enabled_sources=remaining,
                ebay_marketplace=fresh.ebay_marketplace,
                setup_completed=bool(remaining) and fresh.setup_completed,
            )
        return {"ok": True, "removed": removed}

    def _require_taxonomies() -> TaxonomyService:
        if taxonomies is None:
            raise HTTPException(status_code=503, detail="Сервис категорий недоступен")
        return taxonomies

    @router.get("/categories/status")
    async def api_categories_status(
        user: User = Depends(current_user),
    ) -> dict[str, Any]:
        service = _require_taxonomies()
        return service.status()

    @router.get("/categories/search")
    async def api_categories_search(
        source: str,
        q: str = "",
        marketplace: str | None = None,
        limit: int = 30,
        user: User = Depends(current_user),
    ) -> dict[str, Any]:
        service = _require_taxonomies()
        try:
            items = await service.search(
                source=taxonomy_source_for_api(source),
                q=q,
                marketplace=marketplace,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"items": items}

    @router.get("/categories")
    async def api_categories_children(
        source: str,
        parent_id: str | None = None,
        marketplace: str | None = None,
        user: User = Depends(current_user),
    ) -> dict[str, Any]:
        service = _require_taxonomies()
        try:
            items = await service.children(
                source=taxonomy_source_for_api(source),
                parent_id=parent_id,
                marketplace=marketplace,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"items": items}

    @router.post("/categories/refresh")
    async def api_categories_refresh(
        user: User = Depends(current_user),
    ) -> dict[str, Any]:
        service = _require_taxonomies()
        ebay_id = ""
        ebay_secret = ""
        etsy_key = ""
        try:
            pair = await credentials.get_ebay_keys(user.telegram_id)
            if pair:
                ebay_id, ebay_secret = pair
        except Exception:
            logger.debug("No eBay credentials for taxonomy refresh", exc_info=True)
        try:
            etsy_key = (await credentials.get_etsy_key(user.telegram_id)) or ""
        except Exception:
            logger.debug("No Etsy credentials for taxonomy refresh", exc_info=True)
        result = await service.refresh(
            ebay_client_id=ebay_id,
            ebay_client_secret=ebay_secret,
            etsy_api_key=etsy_key,
            force=True,
        )
        return result

    return router
