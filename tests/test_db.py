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
        self.assertTrue(await self.db.has_seen(search.id))
        self.assertEqual(await self.db.cleanup_seen_items(90), 0)
        self.assertEqual(await self.db.filter_new_ids(search.id, ["old", "new"]), [])
        await self.db.set_search_paused(search.id, 2, True)
        deleted = await self.db.cleanup_seen_items(90)
        self.assertEqual(deleted, 1)
        self.assertEqual(await self.db.filter_new_ids(search.id, ["old", "new"]), ["old"])

    async def test_database_uses_wal(self):
        cursor = await self.db.conn.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
        self.assertEqual(row[0], "wal")

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

    async def test_etsy_credentials_encrypted_storage(self):
        await self.db.upsert_pending_user(6, None, "E")
        await self.db.save_etsy_credentials(6, "enc-etsy")
        self.assertTrue(await self.db.has_etsy_credentials(6))
        self.assertEqual(await self.db.get_etsy_credentials_enc(6), "enc-etsy")
        # eBay ключи независимы
        self.assertFalse(await self.db.has_credentials(6))
        await self.db.save_credentials(6, "enc-ebay-id", "enc-ebay-secret")
        self.assertTrue(await self.db.has_credentials(6))
        self.assertTrue(await self.db.has_etsy_credentials(6))
        await self.db.revoke_etsy_credentials(6)
        self.assertFalse(await self.db.has_etsy_credentials(6))
        self.assertTrue(await self.db.has_credentials(6))
        await self.db.revoke_credentials(6)
        self.assertFalse(await self.db.has_credentials(6))
        # пустая строка credentials удаляется
        cursor = await self.db.conn.execute(
            "SELECT 1 FROM credentials WHERE telegram_id = ?", (6,)
        )
        self.assertIsNone(await cursor.fetchone())

    async def test_poll_logs_clear_and_list(self):
        await self.db.upsert_pending_user(4, None, "C")
        await self.db.set_user_status(4, UserStatus.APPROVED)
        search = await self.db.add_search(4, Source.POSHMARK, "coat")
        await self.db.add_poll_log(
            4,
            search_id=search.id,
            source=Source.POSHMARK,
            keywords="coat",
            status="ok",
            found=1,
            message="old",
        )
        await self.db.clear_poll_logs(4)
        await self.db.add_poll_log(
            4,
            search_id=search.id,
            source=Source.POSHMARK,
            keywords="coat",
            status="seed",
            found=5,
            message="current",
        )
        logs = await self.db.list_poll_logs(4)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].status, "seed")
        self.assertEqual(logs[0].found, 5)
        self.assertEqual(logs[0].message, "current")

    async def test_find_identical_search(self):
        await self.db.upsert_pending_user(5, None, "D")
        await self.db.set_user_status(5, UserStatus.APPROVED)
        await self.db.save_setup(
            5,
            enabled_sources=[Source.EBAY_PARSER, Source.POSHMARK],
            setup_completed=True,
        )
        created = await self.db.add_search(
            5,
            Source.EBAY_PARSER,
            "Vintage Lamp",
            min_price=10,
            max_price=50,
            buy_it_now=True,
            marketplace="EBAY_US",
        )
        found = await self.db.find_identical_search(
            5,
            Source.EBAY_PARSER,
            "  vintage   lamp ",
            min_price=10,
            max_price=50,
            buy_it_now=True,
            marketplace="EBAY_US",
        )
        self.assertIsNotNone(found)
        self.assertEqual(found.id, created.id)

        different_price = await self.db.find_identical_search(
            5,
            Source.EBAY_PARSER,
            "vintage lamp",
            min_price=10,
            max_price=60,
            buy_it_now=True,
            marketplace="EBAY_US",
        )
        self.assertIsNone(different_price)

        different_source = await self.db.find_identical_search(
            5,
            Source.POSHMARK,
            "vintage lamp",
            min_price=10,
            max_price=50,
            buy_it_now=False,
        )
        self.assertIsNone(different_source)

        await self.db.add_search(
            5, Source.POSHMARK, "bag", min_price=5, max_price=20, buy_it_now=False
        )
        posh_dup = await self.db.find_identical_search(
            5,
            Source.POSHMARK,
            "Bag",
            min_price=5,
            max_price=20,
            buy_it_now=False,
            marketplace="EBAY_US",  # игнорируется для Poshmark
        )
        self.assertIsNotNone(posh_dup)


if __name__ == "__main__":
    unittest.main()
