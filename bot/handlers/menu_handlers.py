from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.config import Settings
from bot.menu import Btn, open_miniapp_inline_kb, remove_menu_kb, webapp_url_from_base

router = Router(name="menu")


@router.message(F.text == Btn.MENU)
async def show_menu(
    message: Message,
    state: FSMContext,
    settings: Settings,
) -> None:
    await state.clear()
    await message.answer(
        "Откройте Mini App кнопкой ниже или через меню у поля ввода.",
        reply_markup=remove_menu_kb(),
    )
    inline = open_miniapp_inline_kb(webapp_url_from_base(settings.public_base_url))
    if inline is not None:
        await message.answer("Откройте Mini App:", reply_markup=inline)
