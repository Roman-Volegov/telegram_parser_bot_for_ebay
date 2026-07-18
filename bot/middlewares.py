from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.db import Database
from bot.models import UserStatus


class InjectMiddleware(BaseMiddleware):
    def __init__(self, **deps: Any) -> None:
        self.deps = deps

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data.update(self.deps)
        return await handler(event, data)


class AccessMiddleware(BaseMiddleware):
    """Ограничивает пользовательские команды статусом approved.

    Публичные: /start
    Админские: /users /approve /reject /block (+ admin callbacks)
    Остальное — только approved (и для setup — тоже approved).
    """

    PUBLIC_COMMANDS = {"/start"}
    ADMIN_COMMANDS = {"/users", "/approve", "/reject", "/block"}

    def __init__(self, admin_ids: set[int]) -> None:
        self.admin_ids = admin_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        db: Database = data["db"]
        text = ""
        if isinstance(event, Message) and event.text:
            text = event.text.strip()
        command = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""

        if command in self.PUBLIC_COMMANDS:
            return await handler(event, data)

        if command in self.ADMIN_COMMANDS or (
            isinstance(event, CallbackQuery)
            and (event.data or "").startswith("admin:")
        ):
            if user.id not in self.admin_ids:
                if isinstance(event, Message):
                    await event.answer("Команда только для админа.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("Только для админа", show_alert=True)
                return None
            return await handler(event, data)

        db_user = await db.get_user(user.id)
        if db_user is None:
            if isinstance(event, Message):
                await event.answer("Сначала нажмите /start")
            elif isinstance(event, CallbackQuery):
                await event.answer("Сначала /start", show_alert=True)
            return None

        if db_user.status is UserStatus.BLOCKED:
            if isinstance(event, Message):
                await event.answer("Доступ заблокирован.")
            elif isinstance(event, CallbackQuery):
                await event.answer("Доступ заблокирован", show_alert=True)
            return None

        if db_user.status is UserStatus.REJECTED:
            if isinstance(event, Message):
                await event.answer("Заявка отклонена. Обратитесь к администратору.")
            elif isinstance(event, CallbackQuery):
                await event.answer("Заявка отклонена", show_alert=True)
            return None

        if db_user.status is UserStatus.PENDING:
            if isinstance(event, Message):
                await event.answer("Заявка на рассмотрении. Дождитесь одобрения админа.")
            elif isinstance(event, CallbackQuery):
                await event.answer("Ожидайте одобрения", show_alert=True)
            return None

        # approved
        data["user"] = db_user
        return await handler(event, data)
