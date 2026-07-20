from __future__ import annotations

from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    MenuButtonCommands,
    MenuButtonWebApp,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)


class Btn:
    """Подписи кнопок reply-меню."""

    SETUP = "⚙️ Настройки"
    # Сохранены для slash-команд / старых хендлеров
    ADD = "➕ Новый поиск"
    LIST = "📋 Мои поиски"
    KEYS = "🔑 Ключи API"
    REVOKE_KEYS = "🗑 Отозвать ключи"
    HELP = "❓ Справка"
    ADMIN_USERS = "👥 Пользователи"
    MENU = "🏠 Меню"
    APP = "📱 Mini App"


USER_COMMANDS = [
    BotCommand(command="start", description="Старт / обновить кнопку настроек"),
    BotCommand(command="app", description="Открыть Mini App"),
    BotCommand(command="add", description="Новый поиск"),
    BotCommand(command="list", description="Мои поиски"),
    BotCommand(command="setup", description="Настройки"),
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


def settings_webapp_url(webapp_url: str) -> str:
    """URL Mini App со вкладкой настроек (query, без #fragment)."""
    base = webapp_url.split("#", 1)[0].split("?", 1)[0]
    if not base.endswith("/"):
        base = f"{base}/"
    return f"{base}?tab=settings"


def open_miniapp_inline_kb(webapp_url: str, *, label: str = "⚙️ Открыть Mini App") -> InlineKeyboardMarkup | None:
    """Inline WebApp-кнопка — только так Telegram передаёт initData."""
    if not webapp_url.startswith("https://"):
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label,
                    web_app=WebAppInfo(url=settings_webapp_url(webapp_url)),
                )
            ]
        ]
    )


def main_menu_kb(*, is_admin: bool = False, webapp_url: str | None = None) -> ReplyKeyboardMarkup:
    """Нижнее меню: одна текстовая кнопка «Настройки».

    Важно: НЕ ставим web_app на ReplyKeyboard — у таких Mini App initData пустой
    (см. docs Telegram WebAppInitData). Открытие идёт через inline-кнопку в ответе бота.
    """
    del is_admin, webapp_url
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=Btn.SETUP)]],
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
                text="Настройки",
                web_app=WebAppInfo(url=settings_webapp_url(webapp_url)),
            )
        )
    else:
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
