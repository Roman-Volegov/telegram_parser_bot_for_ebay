from __future__ import annotations

from aiogram import Bot, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot.config import Settings
from bot.db import Database
from bot.keyboards import admin_review_kb
from bot.menu import menu_kb, remove_menu_kb
from bot.models import UserStatus

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot, db: Database, settings: Settings) -> None:
    if message.from_user is None:
        return
    user, created = await db.upsert_pending_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )

    # Админы из .env сразу получают approved
    if (
        message.from_user.id in settings.admin_ids
        and user.status is UserStatus.PENDING
    ):
        user = await db.set_user_status(message.from_user.id, UserStatus.APPROVED)
        assert user is not None
        created = False

    is_admin = message.from_user.id in settings.admin_ids

    if user.status is UserStatus.BLOCKED:
        await message.answer("Доступ заблокирован.", reply_markup=remove_menu_kb())
        return
    if user.status is UserStatus.REJECTED:
        await message.answer(
            "Заявка отклонена. Обратитесь к администратору.",
            reply_markup=remove_menu_kb(),
        )
        return
    if user.status is UserStatus.APPROVED:
        kb = menu_kb(is_admin=is_admin, public_base_url=settings.public_base_url)
        if user.setup_completed:
            await message.answer(
                "С возвращением! Пользуйтесь кнопками меню или Mini App.",
                reply_markup=kb,
            )
        else:
            await message.answer(
                "Вы одобрены. Откройте Mini App или «⚙️ Настройки».",
                reply_markup=kb,
            )
        return

    # pending
    await message.answer(
        "Заявка на доступ отправлена администратору.\n"
        "Статус: <b>pending</b>\n"
        "Как только вас одобрят — придёт уведомление.",
        parse_mode="HTML",
        reply_markup=remove_menu_kb(),
    )

    if created:
        uname = f"@{message.from_user.username}" if message.from_user.username else "—"
        text = (
            "🆕 Новая заявка на доступ\n"
            f"ID: <code>{message.from_user.id}</code>\n"
            f"Имя: {message.from_user.full_name}\n"
            f"Username: {uname}"
        )
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(
                    admin_id,
                    text,
                    parse_mode="HTML",
                    reply_markup=admin_review_kb(message.from_user.id),
                )
            except Exception:
                pass
