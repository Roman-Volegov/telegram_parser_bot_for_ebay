from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from bot.config import Settings
from bot.menu import Btn, menu_kb, webapp_url_from_base
from bot.models import User

router = Router(name="common")

HELP_TEXT = """
<b>Меню пользователя</b>
📱 Mini App — поиски и <b>все настройки</b> (источники, ключи eBay, marketplace)
➕ Новый поиск / 📋 Мои поиски — также в чате
⚙️ / 🔑 — открывают Mini App → Настройки
❓ Справка / 🏠 Меню

Команды: /app /add /list /setup …

<b>Админ</b>
👥 Пользователи — заявки и статусы
""".strip()


@router.message(Command("help"))
@router.message(F.text == Btn.HELP)
async def cmd_help(
    message: Message,
    state: FSMContext,
    settings: Settings,
    user: User | None = None,
) -> None:
    await state.clear()
    is_admin = message.from_user is not None and message.from_user.id in settings.admin_ids
    await message.answer(
        HELP_TEXT,
        parse_mode="HTML",
        reply_markup=menu_kb(is_admin=is_admin, public_base_url=settings.public_base_url),
    )


@router.message(Command("app"))
async def cmd_app(message: Message, settings: Settings) -> None:
    url = webapp_url_from_base(settings.public_base_url)
    if not url.startswith("https://"):
        await message.answer(
            "Mini App требует HTTPS.\n"
            f"Сейчас PUBLIC_BASE_URL={settings.public_base_url}"
        )
        return
    await message.answer(
        "Откройте Mini App:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📱 Открыть Mini App", web_app=WebAppInfo(url=url))]
            ]
        ),
    )
