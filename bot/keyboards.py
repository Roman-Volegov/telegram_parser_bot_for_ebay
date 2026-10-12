from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.models import EBAY_MARKETPLACES, SOURCE_LABELS, Search, Source, UserStatus


def admin_review_kb(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Одобрить",
                    callback_data=f"admin:approve:{telegram_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"admin:reject:{telegram_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🚫 Заблокировать",
                    callback_data=f"admin:block:{telegram_id}",
                ),
            ],
        ]
    )


def sources_multiselect_kb(selected: set[Source]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for source in Source:
        mark = "✅" if source in selected else "⬜"
        builder.button(
            text=f"{mark} {SOURCE_LABELS[source]}",
            callback_data=f"setup:toggle:{source.value}",
        )
    builder.button(text="Далее →", callback_data="setup:sources_done")
    builder.button(text="Отмена", callback_data="setup:cancel")
    builder.adjust(1)
    return builder.as_markup()


def marketplace_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for market in EBAY_MARKETPLACES:
        builder.button(text=market, callback_data=f"setup:market:{market}")
    builder.adjust(2)
    return builder.as_markup()


def setup_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сохранить", callback_data="setup:save"),
                InlineKeyboardButton(text="Отмена", callback_data="setup:cancel"),
            ]
        ]
    )


def add_source_kb(enabled: list[Source]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for source in enabled:
        builder.button(
            text=SOURCE_LABELS[source],
            callback_data=f"add:source:{source.value}",
        )
    builder.button(text="Отмена", callback_data="add:cancel")
    builder.adjust(1)
    return builder.as_markup()


def skip_filters_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Без фильтров / далее", callback_data="add:filters_skip")],
            [InlineKeyboardButton(text="Отмена", callback_data="add:cancel")],
        ]
    )


def confirm_search_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Создать", callback_data="add:confirm"),
                InlineKeyboardButton(text="Отмена", callback_data="add:cancel"),
            ]
        ]
    )


def listing_url_kb(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Открыть лот", url=url)]]
    )


def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Отмена")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def searches_manage_kb(searches: list[Search]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for search in searches[:20]:
        toggle = "▶️ Вкл" if search.paused else "⏸ Пауза"
        builder.button(
            text=f"{toggle} #{search.id}",
            callback_data=f"search:toggle:{search.id}",
        )
        builder.button(
            text=f"✏️ #{search.id}",
            callback_data=f"search:edit:{search.id}",
        )
        builder.button(
            text=f"🗑 #{search.id}",
            callback_data=f"search:askdel:{search.id}",
        )
    builder.adjust(3)
    return builder.as_markup()


def confirm_delete_search_kb(search_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, удалить",
                    callback_data=f"search:delyes:{search_id}",
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data="search:delno",
                ),
            ]
        ]
    )


def keys_actions_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Отозвать ключи",
                    callback_data="keys:revoke_ask",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ Настройки",
                    callback_data="keys:goto_setup",
                )
            ],
        ]
    )


def confirm_revoke_keys_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, удалить ключи", callback_data="keys:revoke_yes"),
                InlineKeyboardButton(text="Отмена", callback_data="keys:revoke_no"),
            ]
        ]
    )


def admin_users_filter_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Все", callback_data="adminpanel:users:all"),
                InlineKeyboardButton(text="Pending", callback_data="adminpanel:users:pending"),
            ],
            [
                InlineKeyboardButton(text="Approved", callback_data="adminpanel:users:approved"),
                InlineKeyboardButton(text="Rejected", callback_data="adminpanel:users:rejected"),
            ],
            [
                InlineKeyboardButton(text="Blocked", callback_data="adminpanel:users:blocked"),
            ],
        ]
    )


def admin_user_actions_kb(telegram_id: int, status: UserStatus) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if status is not UserStatus.APPROVED:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Одобрить",
                    callback_data=f"admin:approve:{telegram_id}",
                )
            ]
        )
    if status is not UserStatus.REJECTED:
        rows.append(
            [
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"admin:reject:{telegram_id}",
                )
            ]
        )
    if status is not UserStatus.BLOCKED:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🚫 Блок",
                    callback_data=f"admin:block:{telegram_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pending_menu_note() -> str:
    return "Ожидайте одобрения администратора."
