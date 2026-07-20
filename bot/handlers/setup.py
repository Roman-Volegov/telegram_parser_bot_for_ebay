from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.config import Settings
from bot.menu import (
    Btn,
    open_miniapp_inline_kb,
    remove_menu_kb,
    webapp_url_from_base,
)
from bot.models import User

router = Router(name="setup")


async def _redirect_to_miniapp(
    message: Message,
    settings: Settings,
    *,
    text: str,
) -> None:
    url = webapp_url_from_base(settings.public_base_url)
    inline = open_miniapp_inline_kb(url)
    await message.answer(text, reply_markup=remove_menu_kb())
    if inline is not None:
        await message.answer("Откройте Mini App:", reply_markup=inline)
    else:
        await message.answer(
            "Mini App требует HTTPS. Сейчас настройки недоступны через WebApp.\n"
            f"PUBLIC_BASE_URL={settings.public_base_url}"
        )


@router.message(Command("setup"))
@router.message(Command("settings"))
@router.message(F.text == Btn.SETUP)
async def cmd_setup(
    message: Message,
    state: FSMContext,
    settings: Settings,
    user: User,
) -> None:
    await state.clear()
    await _redirect_to_miniapp(
        message,
        settings,
        text="Нажмите кнопку ниже, чтобы открыть Mini App.",
    )


@router.message(Command("keys_status"))
@router.message(F.text == Btn.KEYS)
async def cmd_keys_status(
    message: Message,
    state: FSMContext,
    settings: Settings,
    user: User,
) -> None:
    await state.clear()
    await _redirect_to_miniapp(
        message,
        settings,
        text="Статус ключей и отзыв — в Mini App → Настройки.",
    )


@router.message(Command("revoke_keys"))
@router.message(F.text == Btn.REVOKE_KEYS)
async def cmd_revoke_keys(
    message: Message,
    state: FSMContext,
    settings: Settings,
    user: User,
) -> None:
    await state.clear()
    await _redirect_to_miniapp(
        message,
        settings,
        text="Отозвать ключи можно в Mini App → Настройки → «Удалить ключи».",
    )
