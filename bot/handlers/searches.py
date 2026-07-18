from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db import Database
from bot.keyboards import (
    add_source_kb,
    confirm_delete_search_kb,
    confirm_search_kb,
    searches_manage_kb,
    skip_filters_kb,
)
from bot.menu import Btn
from bot.models import SOURCE_LABELS, Search, Source, User
from bot.services.poller import PollerService
from bot.states import AddSearchStates, EditSearchStates

router = Router(name="searches")


def _parse_filters(
    text: str,
) -> tuple[float | None, float | None, str | None, bool | None]:
    """Парсит строку вида: max=100 min=10 condition=3000 bin=1"""
    min_price = max_price = None
    condition = None
    buy_it_now: bool | None = None
    for part in text.split():
        m = re.fullmatch(r"min=(\d+(?:\.\d+)?)", part, flags=re.I)
        if m:
            min_price = float(m.group(1))
            continue
        m = re.fullmatch(r"max=(\d+(?:\.\d+)?)", part, flags=re.I)
        if m:
            max_price = float(m.group(1))
            continue
        m = re.fullmatch(r"condition=([\w,]+)", part, flags=re.I)
        if m:
            condition = m.group(1)
            continue
        m = re.fullmatch(r"bin=([01])", part, flags=re.I)
        if m:
            buy_it_now = m.group(1) == "1"
            continue
    return min_price, max_price, condition, buy_it_now


def _format_search(search: Search) -> str:
    status = "⏸" if search.paused else "▶️"
    parts = [
        f"{status} #{search.id} · {SOURCE_LABELS[search.source]}",
        f"<code>{search.keywords}</code>",
    ]
    filt = []
    if search.min_price is not None:
        filt.append(f"min={search.min_price:g}")
    if search.max_price is not None:
        filt.append(f"max={search.max_price:g}")
    if search.condition:
        filt.append(f"condition={search.condition}")
    if search.buy_it_now:
        filt.append("BIN")
    if filt:
        parts.append(" · ".join(filt))
    return "\n".join(parts)


@router.message(Command("add"))
@router.message(F.text == Btn.ADD)
async def cmd_add(message: Message, state: FSMContext, user: User) -> None:
    if not user.setup_completed or not user.enabled_sources:
        await message.answer("Сначала завершите настройку («⚙️ Настройки»).")
        return
    await state.clear()
    await state.set_state(AddSearchStates.choose_source)
    await message.answer(
        "Выберите источник для нового поиска:",
        reply_markup=add_source_kb(user.enabled_sources),
    )


@router.callback_query(StateFilter(AddSearchStates.choose_source), F.data == "add:cancel")
@router.callback_query(F.data == "add:cancel")
async def add_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Создание поиска отменено.")
    await callback.answer()


@router.callback_query(StateFilter(AddSearchStates.choose_source), F.data.startswith("add:source:"))
async def add_choose_source(callback: CallbackQuery, state: FSMContext, user: User) -> None:
    source = Source(callback.data.split(":")[-1])
    if source not in user.enabled_sources:
        await callback.answer("Источник не включён в /settings", show_alert=True)
        return
    await state.update_data(source=source.value)
    await state.set_state(AddSearchStates.keywords)
    await callback.message.edit_text(
        f"Источник: <b>{SOURCE_LABELS[source]}</b>\n\n"
        "Отправьте ключевые слова поиска.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(StateFilter(AddSearchStates.keywords))
async def add_keywords(message: Message, state: FSMContext) -> None:
    keywords = (message.text or "").strip()
    if keywords.lower() == "отмена":
        await state.clear()
        await message.answer("Отменено.")
        return
    if len(keywords) < 2:
        await message.answer("Слишком короткий запрос.")
        return
    await state.update_data(keywords=keywords)
    await state.set_state(AddSearchStates.filters)
    await message.answer(
        "Фильтры (одной строкой) или нажмите кнопку:\n"
        "<code>max=120 min=20 condition=3000 bin=1</code>\n\n"
        "condition — ID состояния eBay (опционально)\n"
        "bin=1 — только Buy It Now",
        parse_mode="HTML",
        reply_markup=skip_filters_kb(),
    )


@router.callback_query(StateFilter(AddSearchStates.filters), F.data == "add:filters_skip")
async def add_filters_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(
        min_price=None, max_price=None, condition=None, buy_it_now=True
    )
    await _show_add_confirm(callback.message, state)
    await callback.answer()


@router.message(StateFilter(AddSearchStates.filters))
async def add_filters_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text.lower() == "отмена":
        await state.clear()
        await message.answer("Отменено.")
        return
    min_price, max_price, condition, buy_it_now = _parse_filters(text)
    await state.update_data(
        min_price=min_price,
        max_price=max_price,
        condition=condition,
        buy_it_now=True if buy_it_now is None else buy_it_now,
    )
    await _show_add_confirm(message, state)


async def _show_add_confirm(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    source = Source(data["source"])
    lines = [
        "Подтвердите поиск:",
        f"Источник: {SOURCE_LABELS[source]}",
        f"Запрос: <code>{data['keywords']}</code>",
    ]
    if data.get("min_price") is not None:
        lines.append(f"min: {data['min_price']}")
    if data.get("max_price") is not None:
        lines.append(f"max: {data['max_price']}")
    if data.get("condition"):
        lines.append(f"condition: {data['condition']}")
    lines.append(f"Buy It Now: {'да' if data.get('buy_it_now', True) else 'нет'}")
    await state.set_state(AddSearchStates.confirm)
    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=confirm_search_kb(),
    )


@router.callback_query(StateFilter(AddSearchStates.confirm), F.data == "add:confirm")
async def add_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
    user: User,
    poller: PollerService,
) -> None:
    data = await state.get_data()
    search = await db.add_search(
        user.telegram_id,
        Source(data["source"]),
        data["keywords"],
        max_price=data.get("max_price"),
        min_price=data.get("min_price"),
        condition=data.get("condition"),
        buy_it_now=bool(data.get("buy_it_now", True)),
    )
    await state.clear()
    await callback.message.edit_text(
        f"Поиск создан.\n{_format_search(search)}\n\n"
        "Делаю тихий первый прогон (без спама старыми лотами)…",
        parse_mode="HTML",
    )
    await poller.process_search(search, notify=False)
    await callback.message.answer(
        f"✅ Поиск #{search.id} готов. Новые лоты будут приходить в этот чат."
    )
    await callback.answer()


@router.message(Command("list"))
@router.message(F.text == Btn.LIST)
async def cmd_list(
    message: Message,
    db: Database,
    user: User,
    state: FSMContext,
) -> None:
    await state.clear()
    searches = await db.list_searches(user.telegram_id)
    if not searches:
        await message.answer("Поисков нет. Нажмите «➕ Новый поиск».")
        return
    text = "Ваши поиски:\n\n" + "\n\n".join(_format_search(s) for s in searches)
    text += "\n\nУправление кнопками ниже:"
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=searches_manage_kb(searches),
    )


@router.callback_query(F.data.startswith("search:toggle:"))
async def search_toggle(callback: CallbackQuery, db: Database, user: User) -> None:
    search_id = int((callback.data or "").split(":")[-1])
    search = await db.get_search(search_id)
    if search is None or search.telegram_id != user.telegram_id:
        await callback.answer("Не найден", show_alert=True)
        return
    await db.set_search_paused(search_id, user.telegram_id, not search.paused)
    searches = await db.list_searches(user.telegram_id)
    text = "Ваши поиски:\n\n" + "\n\n".join(_format_search(s) for s in searches)
    await callback.message.edit_text(
        text + "\n\nУправление кнопками ниже:",
        parse_mode="HTML",
        reply_markup=searches_manage_kb(searches),
    )
    await callback.answer("⏸ Пауза" if not search.paused else "▶️ Включено")


@router.callback_query(F.data.startswith("search:edit:"))
async def search_edit_cb(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
    user: User,
) -> None:
    search_id = int((callback.data or "").split(":")[-1])
    search = await db.get_search(search_id)
    if search is None or search.telegram_id != user.telegram_id:
        await callback.answer("Не найден", show_alert=True)
        return
    await state.set_state(EditSearchStates.keywords)
    await state.update_data(edit_id=search_id)
    await callback.message.answer(
        f"Редактирование #{search_id}.\n"
        f"Текущий запрос: <code>{search.keywords}</code>\n\n"
        "Отправьте новые keywords (или «-» чтобы оставить):",
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("search:askdel:"))
async def search_del_ask(callback: CallbackQuery, db: Database, user: User) -> None:
    search_id = int((callback.data or "").split(":")[-1])
    search = await db.get_search(search_id)
    if search is None or search.telegram_id != user.telegram_id:
        await callback.answer("Не найден", show_alert=True)
        return
    await callback.message.answer(
        f"Удалить поиск #{search_id}?\n{_format_search(search)}",
        parse_mode="HTML",
        reply_markup=confirm_delete_search_kb(search_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("search:delyes:"))
async def search_del_yes(callback: CallbackQuery, db: Database, user: User) -> None:
    search_id = int((callback.data or "").split(":")[-1])
    ok = await db.delete_search(search_id, user.telegram_id)
    await callback.message.answer("🗑 Удалено." if ok else "Поиск не найден.")
    await callback.answer()


@router.callback_query(F.data == "search:delno")
async def search_del_no(callback: CallbackQuery) -> None:
    await callback.message.answer("Удаление отменено.")
    await callback.answer()


@router.message(Command("pause"))
async def cmd_pause(
    message: Message, command: CommandObject, db: Database, user: User
) -> None:
    search_id = _require_id(command)
    if search_id is None:
        await message.answer("Формат: /pause <id>")
        return
    ok = await db.set_search_paused(search_id, user.telegram_id, True)
    await message.answer("⏸ Пауза." if ok else "Поиск не найден.")


@router.message(Command("resume"))
async def cmd_resume(
    message: Message, command: CommandObject, db: Database, user: User
) -> None:
    search_id = _require_id(command)
    if search_id is None:
        await message.answer("Формат: /resume <id>")
        return
    ok = await db.set_search_paused(search_id, user.telegram_id, False)
    await message.answer("▶️ Возобновлён." if ok else "Поиск не найден.")


@router.message(Command("delete"))
async def cmd_delete(
    message: Message, command: CommandObject, db: Database, user: User
) -> None:
    search_id = _require_id(command)
    if search_id is None:
        await message.answer("Формат: /delete <id>")
        return
    ok = await db.delete_search(search_id, user.telegram_id)
    await message.answer("🗑 Удалено." if ok else "Поиск не найден.")


@router.message(Command("edit"))
async def cmd_edit(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    db: Database,
    user: User,
) -> None:
    search_id = _require_id(command)
    if search_id is None:
        await message.answer("Формат: /edit <id>")
        return
    search = await db.get_search(search_id)
    if search is None or search.telegram_id != user.telegram_id:
        await message.answer("Поиск не найден.")
        return
    await state.set_state(EditSearchStates.keywords)
    await state.update_data(edit_id=search_id)
    await message.answer(
        f"Редактирование #{search_id}.\n"
        f"Текущий запрос: <code>{search.keywords}</code>\n\n"
        "Отправьте новые keywords (или «-» чтобы оставить):",
        parse_mode="HTML",
    )


@router.message(StateFilter(EditSearchStates.keywords))
async def edit_keywords(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text.lower() == "отмена":
        await state.clear()
        await message.answer("Отменено.")
        return
    keywords = None if text == "-" else text
    await state.update_data(keywords=keywords)
    await state.set_state(EditSearchStates.filters)
    await message.answer(
        "Новые фильтры одной строкой, «-» чтобы не менять,\n"
        "или <code>clear</code> чтобы сбросить цены:\n"
        "<code>max=80 min=10 condition=3000 bin=1</code>",
        parse_mode="HTML",
    )


@router.message(StateFilter(EditSearchStates.filters))
async def edit_filters(
    message: Message, state: FSMContext, db: Database, user: User
) -> None:
    text = (message.text or "").strip()
    if text.lower() == "отмена":
        await state.clear()
        await message.answer("Отменено.")
        return
    data = await state.get_data()
    search_id = int(data["edit_id"])
    keywords = data.get("keywords")
    if text == "-":
        updated = await db.update_search(
            search_id, user.telegram_id, keywords=keywords
        )
    elif text.lower() == "clear":
        updated = await db.update_search(
            search_id,
            user.telegram_id,
            keywords=keywords,
            clear_max_price=True,
            clear_min_price=True,
            condition="",
        )
    else:
        min_price, max_price, condition, buy_it_now = _parse_filters(text)
        updated = await db.update_search(
            search_id,
            user.telegram_id,
            keywords=keywords,
            min_price=min_price,
            max_price=max_price,
            condition=condition,
            buy_it_now=buy_it_now,
        )
    await state.clear()
    if updated is None:
        await message.answer("Не удалось обновить.")
        return
    await message.answer("Обновлено:\n" + _format_search(updated), parse_mode="HTML")


def _require_id(command: CommandObject) -> int | None:
    if not command.args or not command.args.strip().isdigit():
        return None
    return int(command.args.strip())
