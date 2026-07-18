from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from bot.config import Settings
from bot.db import Database
from bot.formatting import format_listings, format_watch, format_watch_list, marketplace_label
from bot.models import Marketplace
from bot.parsers import ParserError, get_parser

logger = logging.getLogger(__name__)
router = Router(name="commands")

HELP_TEXT = """
Команды:

/search ebay &lt;запрос&gt; [max=цена]
/search poshmark &lt;запрос&gt; [max=цена]

/watch ebay &lt;запрос&gt; [min=цена] [max=цена]
/watch poshmark &lt;запрос&gt; [min=цена] [max=цена]

/watches — список подписок
/unwatch &lt;id&gt; — удалить подписку
/help — справка

Примеры:
/search ebay nike dunk low max=120
/watch poshmark coach bag min=20 max=80
""".strip()


def _parse_marketplace(raw: str) -> Marketplace | None:
    value = raw.strip().lower()
    aliases = {
        "ebay": Marketplace.EBAY,
        "e": Marketplace.EBAY,
        "poshmark": Marketplace.POSHMARK,
        "posh": Marketplace.POSHMARK,
        "pm": Marketplace.POSHMARK,
    }
    return aliases.get(value)


def _parse_query_and_prices(parts: list[str]) -> tuple[str, float | None, float | None]:
    min_price: float | None = None
    max_price: float | None = None
    query_parts: list[str] = []
    for part in parts:
        min_match = re.fullmatch(r"min=(\d+(?:\.\d+)?)", part, flags=re.IGNORECASE)
        max_match = re.fullmatch(r"max=(\d+(?:\.\d+)?)", part, flags=re.IGNORECASE)
        if min_match:
            min_price = float(min_match.group(1))
            continue
        if max_match:
            max_price = float(max_match.group(1))
            continue
        query_parts.append(part)
    return " ".join(query_parts).strip(), min_price, max_price


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Бот мониторинга лотов eBay и Poshmark.\n\n" + HELP_TEXT,
        parse_mode="HTML",
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, parse_mode="HTML")


@router.message(Command("search"))
async def cmd_search(
    message: Message,
    command: CommandObject,
    settings: Settings,
) -> None:
    if not command.args:
        await message.answer("Формат: /search ebay|poshmark &lt;запрос&gt; [max=цена]", parse_mode="HTML")
        return

    parts = command.args.split()
    marketplace = _parse_marketplace(parts[0])
    if marketplace is None:
        await message.answer("Укажите маркетплейс: ebay или poshmark")
        return

    query, min_price, max_price = _parse_query_and_prices(parts[1:])
    if not query:
        await message.answer("Нужен поисковый запрос.")
        return

    await message.answer(f"Ищу на {marketplace_label(marketplace)}: <code>{query}</code>…", parse_mode="HTML")

    parser = get_parser(
        marketplace,
        app_id=settings.ebay_app_id,
        cert_id=settings.ebay_cert_id,
        marketplace_id=settings.ebay_marketplace_id,
    ) if marketplace is Marketplace.EBAY else get_parser(marketplace)

    try:
        listings = await parser.search(
            query,
            min_price=min_price,
            max_price=max_price,
            limit=8,
        )
    except ParserError as exc:
        logger.warning("Search failed: %s", exc)
        await message.answer(f"Не удалось получить выдачу: {exc}")
        return
    finally:
        await parser.aclose()

    text = format_listings(
        listings,
        header=f"Результаты · {marketplace_label(marketplace)} · <code>{query}</code>",
    )
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)


@router.message(Command("watch"))
async def cmd_watch(
    message: Message,
    command: CommandObject,
    db: Database,
) -> None:
    if not command.args:
        await message.answer(
            "Формат: /watch ebay|poshmark &lt;запрос&gt; [min=цена] [max=цена]",
            parse_mode="HTML",
        )
        return

    parts = command.args.split()
    marketplace = _parse_marketplace(parts[0])
    if marketplace is None:
        await message.answer("Укажите маркетплейс: ebay или poshmark")
        return

    query, min_price, max_price = _parse_query_and_prices(parts[1:])
    if not query:
        await message.answer("Нужен поисковый запрос.")
        return

    watch = await db.add_watch(
        user_id=message.from_user.id,
        marketplace=marketplace,
        query=query,
        min_price=min_price,
        max_price=max_price,
    )
    await message.answer(
        "Подписка создана. Новые лоты придут в этот чат.\n" + format_watch(watch),
        parse_mode="HTML",
    )


@router.message(Command("watches"))
async def cmd_watches(message: Message, db: Database) -> None:
    watches = await db.list_watches(message.from_user.id)
    await message.answer(format_watch_list(watches), parse_mode="HTML")


@router.message(Command("unwatch"))
async def cmd_unwatch(
    message: Message,
    command: CommandObject,
    db: Database,
) -> None:
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Формат: /unwatch &lt;id&gt;", parse_mode="HTML")
        return
    watch_id = int(command.args.strip())
    removed = await db.deactivate_watch(message.from_user.id, watch_id)
    if removed:
        await message.answer(f"Подписка #{watch_id} удалена.")
    else:
        await message.answer("Подписка не найдена или уже удалена.")


@router.message(F.text & ~F.text.startswith("/"))
async def fallback_text(message: Message) -> None:
    await message.answer("Используйте /help для списка команд.")
