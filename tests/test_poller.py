import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from bot.models import Listing, Search, Source, User, UserStatus
from bot.providers.base import BaseProvider, ProviderError
from bot.services.poller import PollerService


class FakeDatabase:
    def __init__(self) -> None:
        self.seen = {"existing"}
        self.logs = []

    async def get_user(self, telegram_id):
        return User(
            telegram_id=telegram_id,
            username="user",
            full_name="User",
            status=UserStatus.APPROVED,
            setup_completed=True,
        )

    async def has_seen(self, search_id):
        return bool(self.seen)

    async def filter_new_ids(self, search_id, item_ids):
        return [item_id for item_id in item_ids if item_id not in self.seen]

    async def mark_seen(self, search_id, item_ids):
        self.seen.update(item_ids)

    async def add_poll_log(self, *args, **kwargs):
        self.logs.append(kwargs)


class FakeProvider(BaseProvider):
    source = Source.EBAY_API

    def __init__(self, listings=None, error=None) -> None:
        self.listings = listings or []
        self.error = error
        self.closed = False

    async def search(self, search, *, limit=20):
        if self.error:
            raise self.error
        return self.listings[:limit]

    async def aclose(self):
        self.closed = True


class FakeAccess:
    def create_ticket_url(self):
        return "https://example.com/ticket"


def listing(item_id: str) -> Listing:
    return Listing(
        id=item_id,
        title=item_id,
        description=item_id,
        price=1,
        currency="USD",
        image_url=None,
        item_url=f"https://example.com/{item_id}",
        source=Source.EBAY_API,
    )


class PollerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = FakeDatabase()
        self.bot = AsyncMock()
        self.poller = PollerService(
            self.bot,
            self.db,
            credentials=None,
            interval_sec=300,
        )
        self.search = Search(
            id=1,
            telegram_id=10,
            source=Source.EBAY_API,
            keywords="watch",
        )

    async def test_only_successfully_sent_items_become_seen(self):
        provider = FakeProvider([listing("one"), listing("two")])
        self.poller._build_provider = AsyncMock(return_value=provider)

        with patch(
            "bot.services.poller.send_listing_card",
            new=AsyncMock(side_effect=[RuntimeError("telegram down"), None]),
        ):
            sent = await self.poller.process_search(self.search, notify=True)

        self.assertEqual(sent, 1)
        self.assertNotIn("one", self.db.seen)
        self.assertIn("two", self.db.seen)
        self.assertTrue(provider.closed)

    async def test_parallel_processing_is_serialized_per_search(self):
        providers = [
            FakeProvider([listing("one")]),
            FakeProvider([listing("one")]),
        ]
        self.poller._build_provider = AsyncMock(side_effect=providers)

        async def slow_send(*args, **kwargs):
            await asyncio.sleep(0.02)

        send = AsyncMock(side_effect=slow_send)
        with patch("bot.services.poller.send_listing_card", new=send):
            await asyncio.gather(
                self.poller.process_search(self.search, notify=True),
                self.poller.process_search(self.search, notify=True),
            )

        self.assertEqual(send.await_count, 1)

    async def test_captcha_notifies_admin_using_error_code(self):
        poller = PollerService(
            self.bot,
            self.db,
            credentials=None,
            interval_sec=300,
            etsy_vnc_access=FakeAccess(),
            etsy_captcha_notify_ids={99},
        )
        search = Search(
            id=2,
            telegram_id=10,
            source=Source.ETSY,
            keywords="bag",
        )
        poller._build_provider = AsyncMock(
            return_value=FakeProvider(
                error=ProviderError("captcha", code="ETSY_CAPTCHA")
            )
        )

        await poller.process_search(search, notify=True)

        self.bot.send_message.assert_awaited_once()
        self.assertEqual(self.bot.send_message.await_args.args[0], 99)
