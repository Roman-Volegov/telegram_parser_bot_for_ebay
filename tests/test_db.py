import tempfile
import unittest
from pathlib import Path

from bot.db import Database
from bot.models import Marketplace


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name) / "test.db"))
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    async def test_watch_lifecycle_and_seen(self):
        watch = await self.db.add_watch(
            user_id=42,
            marketplace=Marketplace.EBAY,
            query="nike dunk",
            max_price=100,
        )
        watches = await self.db.list_watches(42)
        self.assertEqual(len(watches), 1)
        self.assertEqual(watches[0].query, "nike dunk")

        new_ids = await self.db.filter_new_ids(watch.id, ["a", "b"])
        self.assertEqual(new_ids, ["a", "b"])
        await self.db.mark_seen(watch.id, ["a"])
        new_ids = await self.db.filter_new_ids(watch.id, ["a", "b"])
        self.assertEqual(new_ids, ["b"])

        removed = await self.db.deactivate_watch(42, watch.id)
        self.assertTrue(removed)
        self.assertEqual(await self.db.list_watches(42), [])


if __name__ == "__main__":
    unittest.main()
