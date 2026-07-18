from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.models import User

router = Router(name="common")

HELP_TEXT = """
<b>Команды пользователя</b>
/setup или /settings — мастер источников и ключей
/add — новый поиск
/list — список поисков
/edit &lt;id&gt; — изменить поиск
/pause &lt;id&gt; / /resume &lt;id&gt;
/delete &lt;id&gt;
/keys_status — статус ключей eBay API
/revoke_keys — удалить ключи
/help — справка

<b>Команды админа</b>
/users [status]
/approve &lt;telegram_id&gt;
/reject &lt;telegram_id&gt;
/block &lt;telegram_id&gt;
""".strip()


@router.message(Command("help"))
async def cmd_help(message: Message, user: User | None = None) -> None:
    await message.answer(HELP_TEXT, parse_mode="HTML")
