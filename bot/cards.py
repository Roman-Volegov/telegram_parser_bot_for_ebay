from __future__ import annotations

import logging
from html import escape

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import URLInputFile

from bot.keyboards import listing_url_kb
from bot.models import SOURCE_LABELS, Listing

logger = logging.getLogger(__name__)


def build_caption(listing: Listing) -> str:
    source = SOURCE_LABELS.get(listing.source, listing.source.value)
    title = escape(listing.title)
    desc = escape(listing.description or listing.title)
    price = escape(listing.price_label)
    # Telegram caption limit ~1024
    caption = (
        f"<b>{title}</b>\n"
        f"💰 {price} · {escape(source)}\n\n"
        f"{desc}"
    )
    if len(caption) > 1000:
        caption = caption[:997] + "…"
    return caption


async def send_listing_card(bot: Bot, chat_id: int, listing: Listing) -> None:
    caption = build_caption(listing)
    reply_markup = listing_url_kb(listing.item_url)
    if listing.image_url:
        try:
            await bot.send_photo(
                chat_id,
                photo=URLInputFile(listing.image_url),
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
            return
        except Exception:
            logger.warning(
                "Failed to send photo for item %s, fallback to text",
                listing.id,
                exc_info=True,
            )
    text = caption + f'\n\n<a href="{escape(listing.item_url, quote=True)}">Ссылка</a>'
    await bot.send_message(
        chat_id,
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
        disable_web_page_preview=False,
    )
