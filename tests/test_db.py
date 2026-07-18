import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot.db import Database
from bot.models import Source, UserStatus


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name) / "test.db"))
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    async def test_user_and_search_flow(self):
        user, created = await self.db.upsert_pending_user(1, "u", "User")
        self.assertTrue(created)
        self.assertEqual(user.status, UserStatus.PENDING)

        await self.db.set_user_status(1, UserStatus.APPROVED)
        await self.db.save_setup(
            1,
            enabled_sources=[Source.EBAY_PARSER, Source.POSHMARK],
            setup_completed=True,
        )
        user = await self.db.get_user(1)
        self.assertTrue(user.setup_completed)
        self.assertIn(Source.POSHMARK, user.enabled_sources)

        search = await self.db.add_search(
            1, Source.EBAY_PARSER, "nike dunk", max_price=100
        )
        new_ids = await self.db.filter_new_ids(search.id, ["a", "b"])
        self.assertEqual(new_ids, ["a", "b"])
        await self.db.mark_seen(search.id, ["a"])
        self.assertEqual(await self.db.filter_new_ids(search.id, ["a", "b"]), ["b"])

        active = await self.db.list_active_searches_for_polling()
        self.assertEqual(len(active), 1)

        await self.db.set_search_paused(search.id, 1, True)
        self.assertEqual(await self.db.list_active_searches_for_polling(), [])

    async def test_cleanup_seen_items(self):
        await self.db.upsert_pending_user(2, None, "A")
        await self.db.set_user_status(2, UserStatus.APPROVED)
        await self.db.save_setup(2, enabled_sources=[Source.POSHMARK], setup_completed=True)
        search = await self.db.add_search(2, Source.POSHMARK, "bag")
        old = (datetime.now(timezone.utc) - timedelta(days=120)).replace(
            microsecond=0
        ).isoformat()
        await self.db.conn.execute(
            "INSERT INTO seen_items (search_id, item_id, first_seen_at) VALUES (?, ?, ?)",
            (search.id, "old", old),
        )
        await self.db.mark_seen(search.id, ["new"])
        await self.db.conn.commit()
        deleted = await self.db.cleanup_seen_items(90)
        self.assertEqual(deleted, 1)
        self.assertEqual(await self.db.filter_new_ids(search.id, ["old", "new"]), ["old"])

    async def test_credentials_and_deletion_token(self):
        await self.db.upsert_pending_user(3, None, "B")
        await self.db.save_credentials(3, "enc1", "enc2")
        self.assertTrue(await self.db.has_credentials(3))
        token = await self.db.ensure_deletion_token(3)
        self.assertTrue(token)
        token2 = await self.db.ensure_deletion_token(3)
        self.assertEqual(token, token2)
        await self.db.revoke_credentials(3)
        self.assertFalse(await self.db.has_credentials(3))


if __name__ == "__main__":
    unittest.main()
