from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.config import Settings
from bot.menu import Btn, open_miniapp_inline_kb, remove_menu_kb, webapp_url_from_base
from bot.models import User
from bot.services.poller import PollerService

router = Router(name="common")

HELP_TEXT = """
<b>DecoParser</b>
Откройте Mini App кнопкой в сообщении бота или через кнопку меню
у поля ввода («Настройки»).

Команды: /app /add /list /setup /etsy_captcha /help
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
    await message.answer(
        HELP_TEXT,
        parse_mode="HTML",
        reply_markup=remove_menu_kb(),
    )
    inline = open_miniapp_inline_kb(webapp_url_from_base(settings.public_base_url))
    if inline is not None:
        await message.answer("Откройте Mini App:", reply_markup=inline)


@router.message(Command("app"))
async def cmd_app(message: Message, settings: Settings) -> None:
    url = webapp_url_from_base(settings.public_base_url)
    inline = open_miniapp_inline_kb(url, label="📱 Открыть Mini App")
    if inline is None:
        await message.answer(
            "Mini App требует HTTPS.\n"
            f"Сейчас PUBLIC_BASE_URL={settings.public_base_url}"
        )
        return
    await message.answer("Откройте Mini App:", reply_markup=inline)


@router.message(Command("etsy_captcha"))
async def cmd_etsy_captcha(
    message: Message,
    poller: PollerService,
    state: FSMContext,
) -> None:
    await state.clear()
    if not poller.etsy_vnc_access:
        await message.answer(
            "Ссылка CAPTCHA недоступна: не задан ETSY_NOVNC_TOKEN на сервере."
        )
        return
    sent = await poller.send_etsy_captcha_link(message.chat.id, force=True)
    if not sent:
        await message.answer(
            "Не удалось отправить ссылку. Проверьте, что бот может писать вам в личку."
        )
