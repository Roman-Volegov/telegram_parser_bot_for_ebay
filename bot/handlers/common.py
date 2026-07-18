from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.config import Settings
from bot.menu import Btn, main_menu_kb
from bot.models import User

router = Router(name="common")

HELP_TEXT = """
<b>Меню пользователя</b>
➕ Новый поиск — создать мониторинг
📋 Мои поиски — список и управление
⚙️ Настройки — источники и ключи eBay
🔑 Ключи API — статус / отзыв ключей
❓ Справка — это сообщение
🏠 Меню — показать кнопки снова

Также доступны команды в меню слева от поля ввода (/add, /list, /setup…).

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
        reply_markup=main_menu_kb(is_admin=is_admin),
    )
