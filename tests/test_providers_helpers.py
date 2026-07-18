import hashlib
import unittest

from bot.providers.ebay_parser import _extract_ebay_item_id
from bot.providers.http_utils import parse_price_text, truncate
from bot.providers.poshmark import _extract_poshmark_id
from bot.handlers.searches import _parse_filters


class HelpersTests(unittest.TestCase):
    def test_ebay_id(self):
        self.assertEqual(
            _extract_ebay_item_id("https://www.ebay.com/itm/Nike/123456789012"),
            "123456789012",
        )

    def test_posh_id(self):
        self.assertEqual(
            _extract_poshmark_id("/listing/coach-507f1f77bcf86cd799439011"),
            "507f1f77bcf86cd799439011",
        )

    def test_price(self):
        price, cur = parse_price_text("US $49.99")
        self.assertEqual(price, 49.99)
        self.assertEqual(cur, "USD")

    def test_truncate(self):
        self.assertTrue(truncate("a" * 500, 50).endswith("…"))

    def test_filters(self):
        min_p, max_p, cond, bin_v = _parse_filters("nike max=120 min=10 bin=0")
        self.assertEqual(min_p, 10.0)
        self.assertEqual(max_p, 120.0)
        self.assertEqual(bin_v, False)
        self.assertIsNone(cond)

    def test_deletion_challenge_hash(self):
        challenge = "abc"
        token = "tok"
        endpoint = "https://example.com/ebay/deletion/1"
        digest = hashlib.sha256((challenge + token + endpoint).encode()).hexdigest()
        self.assertEqual(len(digest), 64)


if __name__ == "__main__":
    unittest.main()
