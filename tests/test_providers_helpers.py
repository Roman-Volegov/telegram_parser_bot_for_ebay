import hashlib
import unittest
from unittest.mock import AsyncMock, patch

from bot.providers.ebay_parser import _extract_ebay_item_id, _parse_html_listings
from bot.providers.etsy import (
    _extract_etsy_listing_id,
    _extract_shipping_from_detail,
    _filter_title_matches,
    _looks_like_datadome,
    _parse_api_listings,
    _parse_search_html,
)
from bot.providers.etsy_browser import _is_browser_failure_error
from bot.providers.http_utils import (
    BROWSER_HEADERS,
    parse_price_text,
    request_with_retries,
    truncate,
)
from bot.providers.poshmark import _extract_poshmark_id, _extract_shipping_from_detail as _posh_ship
from bot.handlers.searches import _parse_filters
from bot.web.deletion import deletion_endpoint


class HelpersTests(unittest.TestCase):
    def test_ebay_id(self):
        self.assertEqual(
            _extract_ebay_item_id("https://www.ebay.com/itm/Nike/123456789012"),
            "123456789012",
        )
        self.assertIsNone(
            _extract_ebay_item_id("https://www.ebay.com/itm/placeholder/123456")
        )

    def test_etsy_listing_id(self):
        self.assertEqual(
            _extract_etsy_listing_id(
                "https://www.etsy.com/listing/1234567890/vintage-necklace?ref=x"
            ),
            "1234567890",
        )

    def test_etsy_datadome_marker(self):
        self.assertTrue(_looks_like_datadome("<!-- DATADOME_CHALLENGE -->"))

    def test_etsy_browser_crash_is_recoverable(self):
        self.assertTrue(_is_browser_failure_error(RuntimeError("Page crashed")))
        self.assertTrue(
            _is_browser_failure_error(
                RuntimeError("Target page, context or browser has been closed")
            )
        )
        self.assertFalse(_is_browser_failure_error(RuntimeError("HTTP 403")))

    def test_etsy_parse_search_html(self):
        html = """
        <ul>
          <li>
            <a href="/listing/1234567890/vintage-necklace">
              <img alt="Vintage Necklace Gold" />
              <h3>Vintage Necklace Gold</h3>
              <span class="currency-value">24.50</span>
            </a>
          </li>
        </ul>
        """
        items = _parse_search_html(html, limit=10)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, "1234567890")
        self.assertEqual(items[0].title, "Vintage Necklace Gold")
        self.assertEqual(items[0].price, 24.5)
        self.assertEqual(items[0].source.value, "etsy")

    def test_etsy_parse_api_listings(self):
        payload = {
            "count": 1,
            "results": [
                {
                    "listing_id": 9876543210,
                    "title": "Handmade Bag",
                    "description": "Nice bag",
                    "url": "https://www.etsy.com/listing/9876543210/handmade-bag",
                    "price": {"amount": 1999, "divisor": 100, "currency_code": "USD"},
                    "images": [{"url_570xN": "https://i.etsystatic.com/bag.jpg"}],
                }
            ],
        }
        items = _parse_api_listings(payload, limit=10)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, "9876543210")
        self.assertEqual(items[0].title, "Handmade Bag")
        self.assertEqual(items[0].price, 19.99)
        self.assertEqual(items[0].currency, "USD")
        self.assertEqual(items[0].image_url, "https://i.etsystatic.com/bag.jpg")

    def test_etsy_title_filter_requires_all_query_words_in_title(self):
        html = """
        <ul>
          <li>
            <a href="/listing/1000000001/monet-necklace">
              <img alt="Vintage Monet Gold Necklace" />
              <h3>Vintage Monet Gold Necklace</h3>
            </a>
          </li>
          <li>
            <a href="/listing/1000000002/generic-necklace">
              <img alt="Vintage Gold Necklace" />
              <h3>Vintage Gold Necklace</h3>
            </a>
          </li>
          <li>
            <a href="/listing/1000000003/monet-brooch">
              <img alt="Monet Gold Brooch" />
              <h3>Monet Gold Brooch</h3>
            </a>
          </li>
        </ul>
        """
        candidates = _parse_search_html(html, limit=10)
        items = _filter_title_matches(
            candidates,
            "Monet Necklace",
            limit=10,
        )
        self.assertEqual([item.id for item in items], ["1000000001"])

    def test_etsy_title_filter_does_not_match_description_only(self):
        payload = {
            "results": [
                {
                    "listing_id": 1000000004,
                    "title": "Vintage Gold Necklace",
                    "description": "Beautiful Monet jewelry",
                    "url": "https://www.etsy.com/listing/1000000004/item",
                }
            ]
        }
        candidates = _parse_api_listings(payload, limit=10)
        self.assertEqual(
            _filter_title_matches(candidates, "Monet", limit=10),
            [],
        )

    def test_etsy_shipping_from_detail(self):
        html = """
        <html><body>
          <script>free_shipping_everywhere=true</script>
          <div>Shipping: $4.99</div>
        </body></html>
        """
        cost, currency, is_free = _extract_shipping_from_detail(html)
        self.assertEqual(cost, 4.99)
        self.assertFalse(is_free)

    def test_poshmark_shipping_from_detail(self):
        html = """
        <html><body>
          <script>free_shipping_discount_enabled=true; shipping_fee=0</script>
          <div class="shipping"><span>$6.49 Shipping</span></div>
        </body></html>
        """
        cost, currency, is_free = _posh_ship(html)
        self.assertEqual(cost, 6.49)
        self.assertEqual(currency, "USD")
        self.assertFalse(is_free)


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
          <li class="s-card">
            <a class="s-card__link" href="https://www.ebay.com/itm/158095592633?hash=1">img</a>
            <div class="s-card__title">
              <span class="su-styled-text">Vintage Estate Trifari Opens in a new window or tab</span>
            </div>
            <div class="s-card__subtitle">Pre-Owned</div>
            <span class="s-card__price">$49.99</span>
            <div class="s-card__attribute-row">+$24.67 delivery</div>
          </li>
        </ul>
        """
        items = _parse_html_listings(html, limit=10)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].id, "298497011183")
        self.assertEqual(items[0].price, 24.99)
        self.assertTrue(items[0].shipping_free)
        self.assertEqual(items[1].id, "158095592633")
        self.assertEqual(items[1].title, "Vintage Estate Trifari")
        self.assertEqual(items[1].price, 49.99)
        self.assertEqual(items[1].shipping_cost, 24.67)

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
        endpoint = deletion_endpoint("https://example.com", 1, token)
        digest = hashlib.sha256((challenge + token + endpoint).encode()).hexdigest()
        self.assertEqual(len(digest), 64)
        self.assertNotIn(token, endpoint)


class HttpRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_first_request_has_no_delay(self):
        response = AsyncMock()
        response.status_code = 200
        client = AsyncMock()
        client.request.return_value = response

        with patch("bot.providers.http_utils.asyncio.sleep", new=AsyncMock()) as sleep:
            result = await request_with_retries(client, "GET", "https://example.com")

        self.assertIs(result, response)
        sleep.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
