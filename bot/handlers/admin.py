from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from bot.db import Database
from bot.models import UserStatus

router = Router(name="admin")


def _format_user_line(user) -> str:
    uname = f"@{user.username}" if user.username else "—"
    setup = "setup✓" if user.setup_completed else "setup✗"
    return (
        f"<code>{user.telegram_id}</code> · {user.status.value} · {setup}\n"
        f"{user.full_name or '—'} · {uname}"
    )


@router.message(Command("users"))
async def cmd_users(message: Message, db: Database, command: CommandObject) -> None:
    status = None
    if command.args:
        raw = command.args.strip().lower()
        try:
            status = UserStatus(raw)
        except ValueError:
            await message.answer("Статус: pending|approved|rejected|blocked")
            return
    users = await db.list_users(status=status)
    if not users:
        await message.answer("Пользователей нет.")
        return
    chunks: list[str] = []
    buf = "Пользователи:\n\n"
    for user in users[:100]:
        line = _format_user_line(user) + "\n\n"
        if len(buf) + len(line) > 3500:
            chunks.append(buf)
            buf = ""
        buf += line
    if buf:
        chunks.append(buf)
    for chunk in chunks:
        await message.answer(chunk, parse_mode="HTML")


async def _set_status_and_notify(
    bot: Bot,
    db: Database,
    telegram_id: int,
    status: UserStatus,
    *,
    actor_chat,
) -> None:
    user = await db.set_user_status(telegram_id, status)
    if user is None:
        await actor_chat.answer(f"Пользователь {telegram_id} не найден.")
        return
    await actor_chat.answer(
        f"Статус {telegram_id}: <b>{status.value}</b>",
        parse_mode="HTML",
    )
    texts = {
        UserStatus.APPROVED: (
            "✅ Заявка одобрена.\nПройдите мастер настройки: /setup"
        ),
        UserStatus.REJECTED: "❌ Заявка отклонена.",
        UserStatus.BLOCKED: "🚫 Доступ заблокирован.",
        UserStatus.PENDING: "Статус снова pending.",
    }
    try:
        await bot.send_message(telegram_id, texts[status])
    except Exception:
        pass


@router.message(Command("approve"))
async def cmd_approve(
    message: Message, bot: Bot, db: Database, command: CommandObject
) -> None:
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Формат: /approve <telegram_id>")
        return
    await _set_status_and_notify(
        bot, db, int(command.args.strip()), UserStatus.APPROVED, actor_chat=message
    )


@router.message(Command("reject"))
async def cmd_reject(
    message: Message, bot: Bot, db: Database, command: CommandObject
) -> None:
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Формат: /reject <telegram_id>")
        return
    await _set_status_and_notify(
        bot, db, int(command.args.strip()), UserStatus.REJECTED, actor_chat=message
    )


@router.message(Command("block"))
async def cmd_block(
    message: Message, bot: Bot, db: Database, command: CommandObject
) -> None:
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Формат: /block <telegram_id>")
        return
    await _set_status_and_notify(
        bot, db, int(command.args.strip()), UserStatus.BLOCKED, actor_chat=message
    )


@router.callback_query(F.data.startswith("admin:"))
async def admin_callbacks(callback: CallbackQuery, bot: Bot, db: Database) -> None:
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
        bot, db, telegram_id, status, actor_chat=callback.message
    )
    await callback.answer("Готово")
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
