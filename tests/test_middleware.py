import unittest
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import Chat, Message, Update, User

from bot.middlewares import AccessMiddleware


class AccessMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_on_update_passes(self):
        mw = AccessMiddleware(admin_ids={1})
        handler = AsyncMock(return_value="ok")
        tg_user = User(id=386263279, is_bot=False, first_name="Roman")
        message = Message(
            message_id=1,
            date=0,
            chat=Chat(id=386263279, type="private"),
            from_user=tg_user,
            text="/start",
        )
        update = Update(update_id=1, message=message)
        data = {"event_from_user": tg_user, "db": MagicMock()}

        result = await mw(handler, update, data)
        self.assertEqual(result, "ok")
        handler.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
