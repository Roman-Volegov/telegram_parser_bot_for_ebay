from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.config import Settings
from bot.menu import Btn, main_menu_kb
from bot.models import User

router = Router(name="menu")


@router.message(F.text == Btn.MENU)
async def show_menu(
    message: Message,
    state: FSMContext,
    user: User,
    settings: Settings,
) -> None:
    await state.clear()
    is_admin = message.from_user is not None and message.from_user.id in settings.admin_ids
    await message.answer(
        "Главное меню. Выберите действие кнопкой ниже\n"
        "или через меню команд слева от поля ввода.",
        reply_markup=main_menu_kb(is_admin=is_admin),
    )
