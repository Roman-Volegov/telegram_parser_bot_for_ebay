from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import Settings
from bot.db import Database
from bot.keyboards import admin_user_actions_kb, admin_users_filter_kb
from bot.menu import Btn, main_menu_kb, remove_menu_kb
from bot.models import UserStatus

router = Router(name="admin")


def _format_user_line(user) -> str:
    uname = f"@{user.username}" if user.username else "—"
    setup = "setup✓" if user.setup_completed else "setup✗"
    return (
        f"<code>{user.telegram_id}</code> · {user.status.value} · {setup}\n"
        f"{user.full_name or '—'} · {uname}"
    )


async def _send_users_list(
    message: Message,
    db: Database,
    *,
    status: UserStatus | None,
) -> None:
    users = await db.list_users(status=status)
    if not users:
        await message.answer(
            "Пользователей нет.",
            reply_markup=admin_users_filter_kb(),
        )
        return
    title = f"Пользователи ({status.value if status else 'все'}):"
    await message.answer(title, reply_markup=admin_users_filter_kb())
    for user in users[:40]:
        await message.answer(
            _format_user_line(user),
            parse_mode="HTML",
            reply_markup=admin_user_actions_kb(user.telegram_id, user.status),
        )


@router.message(Command("users"))
async def cmd_users(
    message: Message,
    db: Database,
    command: CommandObject,
    state: FSMContext,
) -> None:
    await state.clear()
    status = None
    if command.args:
        raw = command.args.strip().lower()
        try:
            status = UserStatus(raw)
        except ValueError:
            await message.answer("Статус: pending|approved|rejected|blocked")
            return
    await _send_users_list(message, db, status=status)


@router.message(F.text == Btn.ADMIN_USERS)
async def cmd_users_button(
    message: Message,
    db: Database,
    state: FSMContext,
) -> None:
    await state.clear()
    await _send_users_list(message, db, status=None)


@router.callback_query(F.data.startswith("adminpanel:users:"))
async def admin_users_filter(callback: CallbackQuery, db: Database) -> None:
    raw = (callback.data or "").split(":")[-1]
    status = None if raw == "all" else UserStatus(raw)
    if callback.message:
        await _send_users_list(callback.message, db, status=status)
    await callback.answer()


async def _set_status_and_notify(
    bot: Bot,
    db: Database,
    telegram_id: int,
    status: UserStatus,
    *,
    actor_chat,
    settings: Settings,
) -> None:
    user = await db.set_user_status(telegram_id, status)
    if user is None:
        await actor_chat.answer(f"Пользователь {telegram_id} не найден.")
        return
    await actor_chat.answer(
        f"Статус {telegram_id}: <b>{status.value}</b>",
        parse_mode="HTML",
    )
    is_admin = telegram_id in settings.admin_ids
    if status is UserStatus.APPROVED:
        text = "✅ Заявка одобрена.\nНажмите «⚙️ Настройки» для мастера настройки."
        markup = main_menu_kb(is_admin=is_admin)
    elif status is UserStatus.REJECTED:
        text = "❌ Заявка отклонена."
        markup = remove_menu_kb()
    elif status is UserStatus.BLOCKED:
        text = "🚫 Доступ заблокирован."
        markup = remove_menu_kb()
    else:
        text = "Статус снова pending."
        markup = remove_menu_kb()
    try:
        await bot.send_message(telegram_id, text, reply_markup=markup)
    except Exception:
        pass


@router.message(Command("approve"))
async def cmd_approve(
    message: Message,
    bot: Bot,
    db: Database,
    command: CommandObject,
    settings: Settings,
) -> None:
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Формат: /approve <telegram_id>")
        return
    await _set_status_and_notify(
        bot,
        db,
        int(command.args.strip()),
        UserStatus.APPROVED,
        actor_chat=message,
        settings=settings,
    )


@router.message(Command("reject"))
async def cmd_reject(
    message: Message,
    bot: Bot,
    db: Database,
    command: CommandObject,
    settings: Settings,
) -> None:
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Формат: /reject <telegram_id>")
        return
    await _set_status_and_notify(
        bot,
        db,
        int(command.args.strip()),
        UserStatus.REJECTED,
        actor_chat=message,
        settings=settings,
    )


@router.message(Command("block"))
async def cmd_block(
    message: Message,
    bot: Bot,
    db: Database,
    command: CommandObject,
    settings: Settings,
) -> None:
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Формат: /block <telegram_id>")
        return
    await _set_status_and_notify(
        bot,
        db,
        int(command.args.strip()),
        UserStatus.BLOCKED,
        actor_chat=message,
        settings=settings,
    )


@router.callback_query(F.data.startswith("admin:"))
async def admin_callbacks(
    callback: CallbackQuery,
    bot: Bot,
    db: Database,
    settings: Settings,
) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer("Некорректные данные")
        return
    _, action, tid_raw = parts
    if not tid_raw.isdigit():
        await callback.answer("Некорректный id")
        return
    telegram_id = int(tid_raw)
    mapping = {
        "approve": UserStatus.APPROVED,
        "reject": UserStatus.REJECTED,
        "block": UserStatus.BLOCKED,
    }
    status = mapping.get(action)
    if status is None:
        await callback.answer("Неизвестное действие")
        return
    await _set_status_and_notify(
        bot,
        db,
        telegram_id,
        status,
        actor_chat=callback.message,
        settings=settings,
    )
    await callback.answer("Готово")
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
