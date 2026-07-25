from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from bot.db import Database
from bot.models import (
    EBAY_MARKETPLACE_LABELS,
    EBAY_MARKETPLACES,
    NON_EBAY_SOURCES,
    SOURCE_LABELS,
    Source,
    User,
    UserStatus,
)
from bot.providers.ebay_api import EbayApiProvider
from bot.providers.etsy import EtsyProvider
from bot.services.credentials import CredentialsService, normalize_etsy_api_key
from bot.services.poller import PollerService
from bot.web.telegram_auth import TelegramAuthError, validate_init_data


class SearchCreateIn(BaseModel):
    source: Source
    keywords: str = Field(min_length=2, max_length=200)
    min_price: float | None = None
    max_price: float | None = None
    condition: str | None = None
    buy_it_now: bool = True
    marketplace: str | None = None


class SearchUpdateIn(BaseModel):
    keywords: str | None = Field(default=None, min_length=2, max_length=200)
    min_price: float | None = None
    max_price: float | None = None
    condition: str | None = None
    buy_it_now: bool | None = None
    paused: bool | None = None
    clear_prices: bool = False


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
    http_proxy: str = "",
    poller: PollerService | None = None,
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
            deletion_url = f"{public_base_url.rstrip('/')}/ebay/deletion/{user.telegram_id}"
        return {
            "telegram_id": user.telegram_id,
            "username": user.username,
            "full_name": user.full_name,
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
        return {
            "items": [
                {
                    "id": s.id,
                    "source": s.source.value,
                    "source_label": SOURCE_LABELS[s.source],
                    "keywords": s.keywords,
                    "min_price": s.min_price,
                    "max_price": s.max_price,
                    "condition": s.condition,
                    "buy_it_now": s.buy_it_now,
                    "paused": s.paused,
                    "marketplace": s.marketplace,
                    "marketplace_label": (
                        EBAY_MARKETPLACE_LABELS.get(s.marketplace, s.marketplace)
                        if s.marketplace
                        else None
                    ),
                }
                for s in searches
            ]
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
        if payload.source not in user.enabled_sources:
            raise HTTPException(status_code=400, detail="Источник не включён в настройках")

        marketplace: str | None = None
        buy_it_now = payload.buy_it_now
        if payload.source in NON_EBAY_SOURCES:
            buy_it_now = False
            marketplace = None
        else:
            marketplace = payload.marketplace or user.ebay_marketplace or "EBAY_US"
            if marketplace not in EBAY_MARKETPLACES:
                raise HTTPException(status_code=400, detail="Неизвестный marketplace")

        duplicate = await db.find_identical_search(
            user.telegram_id,
            payload.source,
            payload.keywords,
            max_price=payload.max_price,
            min_price=payload.min_price,
            condition=payload.condition,
            buy_it_now=buy_it_now,
            marketplace=marketplace,
        )
        if duplicate is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Такой поиск уже есть (#{duplicate.id}). "
                    "Измените ключевые слова, фильтры или источник."
                ),
            )

        search = await db.add_search(
            user.telegram_id,
            payload.source,
            payload.keywords,
            max_price=payload.max_price,
            min_price=payload.min_price,
            condition=payload.condition,
            buy_it_now=buy_it_now,
            marketplace=marketplace,
        )
        if poller is not None:
            poller.schedule_search(search, notify=False, record_log=False)
            try:
                market_bit = (
                    f" · {EBAY_MARKETPLACE_LABELS.get(search.marketplace, search.marketplace)}"
                    if search.marketplace
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
                        f"{SOURCE_LABELS[search.source]}{market_bit}\n"
                        f"<code>{search.keywords}</code>{price_bit}\n\n"
                        "Новые лоты будут приходить в этот чат."
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                pass
        return {
            "id": search.id,
            "ok": True,
            "message": f"Новый поиск создан #{search.id}",
            "keywords": search.keywords,
            "source": search.source.value,
            "source_label": SOURCE_LABELS[search.source],
        }

    @router.patch("/searches/{search_id}")
    async def api_update_search(
        search_id: int,
        payload: SearchUpdateIn,
        user: User = Depends(current_user),
    ) -> dict[str, Any]:
        search = await db.get_search(search_id)
        if search is None or search.telegram_id != user.telegram_id:
            raise HTTPException(status_code=404, detail="Поиск не найден")

        if payload.paused is not None:
            await db.set_search_paused(search_id, user.telegram_id, payload.paused)

        updated = await db.update_search(
            search_id,
            user.telegram_id,
            keywords=payload.keywords,
            min_price=payload.min_price,
            max_price=payload.max_price,
            condition=payload.condition,
            buy_it_now=payload.buy_it_now,
            clear_max_price=payload.clear_prices,
            clear_min_price=payload.clear_prices,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Поиск не найден")
        return {"ok": True, "paused": updated.paused}

    @router.delete("/searches/{search_id}")
    async def api_delete_search(
        search_id: int,
        user: User = Depends(current_user),
    ) -> dict[str, Any]:
        ok = await db.delete_search(search_id, user.telegram_id)
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

    return router
