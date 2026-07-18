import unittest

from bot.handlers import _parse_marketplace, _parse_query_and_prices
from bot.models import Marketplace
from bot.parsers.ebay import _extract_ebay_item_id, _parse_price_text as ebay_price
from bot.parsers.poshmark import _extract_poshmark_id, _parse_price_text as posh_price


class CommandParsingTests(unittest.TestCase):
    def test_marketplace_aliases(self):
        self.assertEqual(_parse_marketplace("ebay"), Marketplace.EBAY)
        self.assertEqual(_parse_marketplace("posh"), Marketplace.POSHMARK)
        self.assertIsNone(_parse_marketplace("amazon"))

    def test_query_and_prices(self):
        query, min_price, max_price = _parse_query_and_prices(
            ["nike", "dunk", "min=20", "max=120.5"]
        )
        self.assertEqual(query, "nike dunk")
        self.assertEqual(min_price, 20.0)
        self.assertEqual(max_price, 120.5)


class EbayHelpersTests(unittest.TestCase):
    def test_item_id(self):
        self.assertEqual(
            _extract_ebay_item_id("https://www.ebay.com/itm/Nike-Dunk/123456789012"),
            "123456789012",
        )

    def test_price(self):
        price, currency = ebay_price("US $49.99")
        self.assertEqual(price, 49.99)
        self.assertEqual(currency, "USD")


class PoshmarkHelpersTests(unittest.TestCase):
    def test_listing_id(self):
        self.assertEqual(
            _extract_poshmark_id("/listing/coach-bag-507f1f77bcf86cd799439011"),
            "507f1f77bcf86cd799439011",
        )

    def test_price(self):
        price, currency = posh_price("Coach Bag $85 Size M")
        self.assertEqual(price, 85.0)
        self.assertEqual(currency, "USD")


if __name__ == "__main__":
    unittest.main()
