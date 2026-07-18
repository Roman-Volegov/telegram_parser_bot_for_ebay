from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from bot.config import Settings
from bot.menu import Btn, menu_kb, webapp_url_from_base
from bot.models import User

router = Router(name="setup")


def _open_settings_kb(settings: Settings) -> InlineKeyboardMarkup | None:
    url = webapp_url_from_base(settings.public_base_url)
    if not url.startswith("https://"):
        return None
    # Открываем сразу вкладку настроек через hash — фронт это подхватит
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚙️ Открыть настройки",
                    web_app=WebAppInfo(url=f"{url}#settings"),
                )
            ]
        ]
    )


async def _redirect_to_miniapp(
    message: Message,
    settings: Settings,
    *,
    text: str,
) -> None:
    is_admin = message.from_user is not None and message.from_user.id in settings.admin_ids
    kb = _open_settings_kb(settings)
    await message.answer(
        text,
        reply_markup=kb
        or menu_kb(is_admin=is_admin, public_base_url=settings.public_base_url),
    )
    if kb is None:
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
        text=(
            "Все настройки перенесены в Mini App:\n"
            "• источники eBay API / Parser / Poshmark\n"
            "• Client ID / Secret + проверка OAuth\n"
            "• marketplace и deletion URL\n\n"
            "Нажмите кнопку ниже."
        ),
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
        text="Статус ключей и отзыв — во вкладке «Настройки» Mini App.",
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
