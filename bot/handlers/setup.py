from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import Settings
from bot.db import Database
from bot.keyboards import marketplace_kb, setup_confirm_kb, sources_multiselect_kb
from bot.models import SOURCE_LABELS, Source, User
from bot.providers.ebay_api import EbayApiProvider
from bot.services.credentials import CredentialsService
from bot.states import SetupStates

router = Router(name="setup")

EBAY_CHECKLIST = """
<b>Как получить eBay App ID / Cert ID</b>

1. Зайдите на https://developer.ebay.com и войдите в аккаунт.
2. Application Keys → создайте приложение (или откройте существующее).
3. Скопируйте <b>App ID (Client ID)</b> и <b>Cert ID (Client Secret)</b>.
4. Для Production keyset нужен Marketplace Account Deletion endpoint —
   URL мы покажем после сохранения ключей.
5. Scope для application token:
   <code>https://api.ebay.com/oauth/api_scope</code>

Отправьте сейчас <b>Client ID (App ID)</b> одним сообщением.
Напишите «Отмена» для выхода.
""".strip()


def _selected_from_data(data: dict) -> set[Source]:
    raw = data.get("sources") or []
    return {Source(s) for s in raw}


@router.message(Command("setup"))
@router.message(Command("settings"))
async def cmd_setup(
    message: Message,
    state: FSMContext,
    user: User,
) -> None:
    await state.clear()
    selected = set(user.enabled_sources)
    await state.set_state(SetupStates.choose_sources)
    await state.update_data(sources=[s.value for s in selected])
    await message.answer(
        "Мастер настройки.\nВыберите источники (можно несколько):",
        reply_markup=sources_multiselect_kb(selected),
    )


@router.callback_query(StateFilter(SetupStates.choose_sources), F.data.startswith("setup:toggle:"))
async def setup_toggle_source(callback: CallbackQuery, state: FSMContext) -> None:
    source = Source(callback.data.split(":")[-1])
    data = await state.get_data()
    selected = _selected_from_data(data)
    if source in selected:
        selected.remove(source)
    else:
        selected.add(source)
    await state.update_data(sources=[s.value for s in selected])
    await callback.message.edit_reply_markup(
        reply_markup=sources_multiselect_kb(selected)
    )
    await callback.answer()


@router.callback_query(StateFilter(SetupStates.choose_sources), F.data == "setup:cancel")
@router.callback_query(F.data == "setup:cancel")
async def setup_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Настройка отменена.")
    await callback.answer()


@router.callback_query(StateFilter(SetupStates.choose_sources), F.data == "setup:sources_done")
async def setup_sources_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected = _selected_from_data(data)
    if not selected:
        await callback.answer("Выберите хотя бы один источник", show_alert=True)
        return
    if Source.EBAY_API in selected:
        await state.set_state(SetupStates.ebay_checklist)
        await callback.message.edit_text(EBAY_CHECKLIST, parse_mode="HTML")
        await state.set_state(SetupStates.ebay_client_id)
    else:
        await state.update_data(marketplace="EBAY_US")
        await _show_confirm(callback.message, state)
    await callback.answer()


@router.message(StateFilter(SetupStates.ebay_client_id))
async def setup_client_id(message: Message, state: FSMContext, bot: Bot) -> None:
    if (message.text or "").strip().lower() == "отмена":
        await state.clear()
        await message.answer("Настройка отменена.")
        return
    client_id = (message.text or "").strip()
    if len(client_id) < 8:
        await message.answer("Client ID слишком короткий. Попробуйте ещё раз.")
        return
    await state.update_data(client_id=client_id)
    try:
        await message.delete()
    except Exception:
        pass
    await state.set_state(SetupStates.ebay_client_secret)
    await message.answer(
        "Теперь отправьте <b>Client Secret (Cert ID)</b>.\n"
        "Сообщение с секретом будет удалено из чата.",
        parse_mode="HTML",
    )


@router.message(StateFilter(SetupStates.ebay_client_secret))
async def setup_client_secret(
    message: Message,
    state: FSMContext,
    bot: Bot,
) -> None:
    if (message.text or "").strip().lower() == "отмена":
        await state.clear()
        await message.answer("Настройка отменена.")
        return
    secret = (message.text or "").strip()
    try:
        await message.delete()
    except Exception:
        pass
    if len(secret) < 8:
        await message.answer("Client Secret слишком короткий. Попробуйте ещё раз.")
        return
    await state.update_data(client_secret=secret)
    await state.set_state(SetupStates.ebay_marketplace)
    await message.answer(
        "Выберите marketplace по умолчанию:",
        reply_markup=marketplace_kb(),
    )


@router.callback_query(StateFilter(SetupStates.ebay_marketplace), F.data.startswith("setup:market:"))
async def setup_marketplace(callback: CallbackQuery, state: FSMContext) -> None:
    market = callback.data.split(":")[-1]
    await state.update_data(marketplace=market)
    await _show_confirm(callback.message, state)
    await callback.answer()


async def _show_confirm(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    selected = _selected_from_data(data)
    lines = ["Проверьте настройки:", ""]
    lines.append("Источники:")
    for source in selected:
        lines.append(f"• {SOURCE_LABELS[source]}")
    if Source.EBAY_API in selected:
        lines.append(f"Marketplace: <code>{data.get('marketplace', 'EBAY_US')}</code>")
        lines.append("Ключи: будут проверены через OAuth и сохранены зашифрованно.")
    await state.set_state(SetupStates.confirm)
    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=setup_confirm_kb(),
    )


@router.callback_query(StateFilter(SetupStates.confirm), F.data == "setup:save")
async def setup_save(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
    credentials: CredentialsService,
    settings: Settings,
    user: User,
) -> None:
    data = await state.get_data()
    selected = sorted(_selected_from_data(data), key=lambda s: s.value)
    marketplace = data.get("marketplace") or "EBAY_US"

    if Source.EBAY_API in selected:
        client_id = data.get("client_id")
        client_secret = data.get("client_secret")
        if not client_id or not client_secret:
            await callback.answer("Ключи не введены", show_alert=True)
            return
        await callback.message.edit_text("Проверяю OAuth Client Credentials…")
        provider = EbayApiProvider(
            client_id=client_id,
            client_secret=client_secret,
            marketplace_id=marketplace,
            telegram_id=user.telegram_id,
            proxy=settings.http_proxy or None,
        )
        try:
            await provider.verify_credentials()
        except Exception as exc:
            await callback.message.answer(
                f"OAuth не прошёл: {exc}\n"
                "Исправьте ключи через /setup ещё раз."
            )
            await state.clear()
            await callback.answer()
            return
        finally:
            await provider.aclose()

        await credentials.save_ebay_keys(user.telegram_id, client_id, client_secret)
        # Не держим plaintext в FSM
        await state.update_data(client_id=None, client_secret=None)

    await db.save_setup(
        user.telegram_id,
        enabled_sources=selected,
        ebay_marketplace=marketplace,
        setup_completed=True,
    )

    text_parts = [
        "✅ Настройка сохранена.",
        f"Источники: {', '.join(SOURCE_LABELS[s] for s in selected)}",
    ]
    if Source.EBAY_API in selected:
        token = await db.ensure_deletion_token(user.telegram_id)
        deletion_url = f"{settings.public_base_url}/ebay/deletion/{user.telegram_id}"
        text_parts.extend(
            [
                "",
                "<b>eBay Marketplace Account Deletion</b>",
                f"URL: <code>{deletion_url}</code>",
                f"Verification token: <code>{token}</code>",
                "Укажите их в developer.ebay.com для Production keyset.",
            ]
        )
    text_parts.extend(["", "Дальше создайте поиск: /add"])
    await state.clear()
    await callback.message.answer("\n".join(text_parts), parse_mode="HTML")
    await callback.answer("Сохранено")


@router.message(Command("keys_status"))
async def cmd_keys_status(
    message: Message, credentials: CredentialsService, user: User
) -> None:
    text = await credentials.keys_status_text(user.telegram_id)
    await message.answer(text)


@router.message(Command("revoke_keys"))
async def cmd_revoke_keys(
    message: Message, credentials: CredentialsService, user: User
) -> None:
    removed = await credentials.revoke(user.telegram_id)
    if removed:
        await message.answer("Ключи eBay API удалены. При необходимости пройдите /setup.")
    else:
        await message.answer("Сохранённых ключей не было.")
