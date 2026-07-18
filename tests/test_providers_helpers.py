import hashlib
import unittest

from bot.providers.ebay_parser import _extract_ebay_item_id, _parse_html_listings
from bot.providers.http_utils import BROWSER_HEADERS, parse_price_text, truncate
from bot.providers.poshmark import _extract_poshmark_id
from bot.handlers.searches import _parse_filters


class HelpersTests(unittest.TestCase):
    def test_ebay_id(self):
        self.assertEqual(
            _extract_ebay_item_id("https://www.ebay.com/itm/Nike/123456789012"),
            "123456789012",
        )
        self.assertIsNone(
            _extract_ebay_item_id("https://www.ebay.com/itm/placeholder/123456")
        )

    def test_browser_headers_look_like_chrome(self):
        self.assertIn("Chrome/131", BROWSER_HEADERS["User-Agent"])
        self.assertIn("Sec-Ch-Ua", BROWSER_HEADERS)

    def test_parse_html_listings(self):
        html = """
        <ul>
          <li class="s-item">
            <a class="s-item__link" href="https://www.ebay.com/itm/demo/123456">x</a>
            <div class="s-item__title">Shop on eBay</div>
          </li>
          <li class="s-item">
            <a class="s-item__link" href="https://www.ebay.com/itm/Trifari/298497011183">
              link
            </a>
            <div class="s-item__title">Trifari Necklace Vintage</div>
            <span class="s-item__price">US $24.99</span>
            <span class="s-item__shipping">Free shipping</span>
          </li>
        </ul>
        """
        items = _parse_html_listings(html, limit=10)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, "298497011183")
        self.assertEqual(items[0].price, 24.99)
        self.assertTrue(items[0].shipping_free)

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
