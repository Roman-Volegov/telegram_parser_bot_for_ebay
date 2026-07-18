from __future__ import annotations

from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)


class Btn:
    """Подписи кнопок reply-меню."""

    ADD = "➕ Новый поиск"
    LIST = "📋 Мои поиски"
    SETUP = "⚙️ Настройки"
    KEYS = "🔑 Ключи API"
    REVOKE_KEYS = "🗑 Отозвать ключи"
    HELP = "❓ Справка"
    ADMIN_USERS = "👥 Пользователи"
    MENU = "🏠 Меню"


USER_COMMANDS = [
    BotCommand(command="start", description="Старт / обновление меню"),
    BotCommand(command="add", description="Новый поиск"),
    BotCommand(command="list", description="Мои поиски"),
    BotCommand(command="setup", description="Настройки источников"),
    BotCommand(command="settings", description="Настройки (алиас)"),
    BotCommand(command="keys_status", description="Статус ключей eBay"),
    BotCommand(command="revoke_keys", description="Удалить ключи eBay"),
    BotCommand(command="help", description="Справка"),
]

ADMIN_EXTRA_COMMANDS = [
    BotCommand(command="users", description="Список пользователей"),
    BotCommand(command="approve", description="Одобрить user id"),
    BotCommand(command="reject", description="Отклонить user id"),
    BotCommand(command="block", description="Заблокировать user id"),
]


def main_menu_kb(*, is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=Btn.ADD), KeyboardButton(text=Btn.LIST)],
        [KeyboardButton(text=Btn.SETUP), KeyboardButton(text=Btn.KEYS)],
        [KeyboardButton(text=Btn.HELP)],
    ]
    if is_admin:
        rows.append([KeyboardButton(text=Btn.ADMIN_USERS)])
    rows.append([KeyboardButton(text=Btn.MENU)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
    )


def remove_menu_kb() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


async def setup_bot_commands(bot: Bot, admin_ids: set[int]) -> None:
    await bot.set_my_commands(USER_COMMANDS, scope=BotCommandScopeDefault())
    admin_commands = USER_COMMANDS + ADMIN_EXTRA_COMMANDS
    for admin_id in admin_ids:
        try:
            await bot.set_my_commands(
                admin_commands,
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception:
            # Админ ещё не писал боту — scope chat недоступен до первого контакта
            pass
