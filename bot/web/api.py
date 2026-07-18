from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from bot.db import Database
from bot.models import SOURCE_LABELS, Source, User, UserStatus
from bot.services.poller import PollerService
from bot.web.telegram_auth import TelegramAuthError, validate_init_data


class SearchCreateIn(BaseModel):
    source: Source
    keywords: str = Field(min_length=2, max_length=200)
    min_price: float | None = None
    max_price: float | None = None
    condition: str | None = None
    buy_it_now: bool = True


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


def create_api_router(
    db: Database,
    bot_token: str,
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

    @router.get("/me")
    async def api_me(user: User = Depends(current_user)) -> dict[str, Any]:
        has_keys = await db.has_credentials(user.telegram_id)
        return {
            "telegram_id": user.telegram_id,
            "username": user.username,
            "full_name": user.full_name,
            "status": user.status.value,
            "setup_completed": user.setup_completed,
            "enabled_sources": [s.value for s in user.enabled_sources],
            "source_labels": {s.value: SOURCE_LABELS[s] for s in Source},
            "ebay_marketplace": user.ebay_marketplace,
            "has_ebay_keys": has_keys,
        }

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
                }
                for s in searches
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
        search = await db.add_search(
            user.telegram_id,
            payload.source,
            payload.keywords,
            max_price=payload.max_price,
            min_price=payload.min_price,
            condition=payload.condition,
            buy_it_now=payload.buy_it_now,
        )
        if poller is not None:
            await poller.process_search(search, notify=False)
        return {"id": search.id, "ok": True}

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
        if Source.EBAY_API in payload.enabled_sources:
            has_keys = await db.has_credentials(user.telegram_id)
            if not has_keys:
                raise HTTPException(
                    status_code=400,
                    detail="Для eBay API сначала сохраните ключи в боте (/setup)",
                )
        await db.save_setup(
            user.telegram_id,
            enabled_sources=payload.enabled_sources,
            ebay_marketplace=payload.ebay_marketplace or user.ebay_marketplace,
            setup_completed=True,
        )
        return {"ok": True}

    return router
