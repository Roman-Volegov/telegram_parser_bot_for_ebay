from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

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
    Остальное — только approved.
    """

    PUBLIC_COMMANDS = {"/start"}
    ADMIN_COMMANDS = {"/users", "/approve", "/reject", "/block"}

    def __init__(self, admin_ids: set[int]) -> None:
        self.admin_ids = admin_ids

    @staticmethod
    def _unwrap(event: TelegramObject) -> tuple[Message | None, CallbackQuery | None]:
        if isinstance(event, Update):
            return event.message or event.edited_message, event.callback_query
        if isinstance(event, Message):
            return event, None
        if isinstance(event, CallbackQuery):
            return None, event
        return None, None

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        message, callback = self._unwrap(event)
        db: Database = data["db"]

        text = ""
        if message and message.text:
            text = message.text.strip()
        command = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
        callback_data = callback.data or "" if callback else ""

        if command in self.PUBLIC_COMMANDS:
            return await handler(event, data)

        if command in self.ADMIN_COMMANDS or callback_data.startswith("admin:"):
            if user.id not in self.admin_ids:
                if message:
                    await message.answer("Команда только для админа.")
                elif callback:
                    await callback.answer("Только для админа", show_alert=True)
                return None
            return await handler(event, data)

        db_user = await db.get_user(user.id)
        if db_user is None:
            if message:
                await message.answer("Сначала нажмите /start")
            elif callback:
                await callback.answer("Сначала /start", show_alert=True)
            return None

        if db_user.status is UserStatus.BLOCKED:
            if message:
                await message.answer("Доступ заблокирован.")
            elif callback:
                await callback.answer("Доступ заблокирован", show_alert=True)
            return None

        if db_user.status is UserStatus.REJECTED:
            if message:
                await message.answer("Заявка отклонена. Обратитесь к администратору.")
            elif callback:
                await callback.answer("Заявка отклонена", show_alert=True)
            return None

        if db_user.status is UserStatus.PENDING:
            if message:
                await message.answer("Заявка на рассмотрении. Дождитесь одобрения админа.")
            elif callback:
                await callback.answer("Ожидайте одобрения", show_alert=True)
            return None

        data["user"] = db_user
        return await handler(event, data)
