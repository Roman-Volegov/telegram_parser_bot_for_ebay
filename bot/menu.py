from __future__ import annotations

from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    KeyboardButton,
    MenuButtonCommands,
    MenuButtonWebApp,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
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
    APP = "📱 Mini App"


USER_COMMANDS = [
    BotCommand(command="start", description="Старт / обновление меню"),
    BotCommand(command="app", description="Открыть Mini App"),
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


def main_menu_kb(*, is_admin: bool = False, webapp_url: str | None = None) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = []
    if webapp_url:
        rows.append(
            [
                KeyboardButton(
                    text=Btn.APP,
                    web_app=WebAppInfo(url=webapp_url),
                )
            ]
        )
    rows.extend(
        [
            [KeyboardButton(text=Btn.ADD), KeyboardButton(text=Btn.LIST)],
            [KeyboardButton(text=Btn.SETUP), KeyboardButton(text=Btn.KEYS)],
            [KeyboardButton(text=Btn.HELP)],
        ]
    )
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


def webapp_url_from_base(public_base_url: str) -> str:
    return f"{public_base_url.rstrip('/')}/app/"


def menu_kb(*, is_admin: bool, public_base_url: str) -> ReplyKeyboardMarkup:
    url = webapp_url_from_base(public_base_url)
    return main_menu_kb(
        is_admin=is_admin,
        webapp_url=url if url.startswith("https://") else None,
    )


async def setup_bot_commands(
    bot: Bot,
    admin_ids: set[int],
    *,
    webapp_url: str | None = None,
) -> None:
    await bot.set_my_commands(USER_COMMANDS, scope=BotCommandScopeDefault())
    admin_commands = USER_COMMANDS + ADMIN_EXTRA_COMMANDS
    for admin_id in admin_ids:
        try:
            await bot.set_my_commands(
                admin_commands,
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception:
            pass

    if webapp_url and webapp_url.startswith("https://"):
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Mini App",
                web_app=WebAppInfo(url=webapp_url),
            )
        )
    else:
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
