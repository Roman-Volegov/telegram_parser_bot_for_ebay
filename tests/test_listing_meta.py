import unittest

from bot.cards import build_caption
from bot.models import Listing, Source
from bot.providers.listing_meta import (
    format_ebay_listing_type,
    parse_ebay_html_listing_type,
    parse_shipping_info,
    shipping_from_ebay_api,
)


class ListingMetaTests(unittest.TestCase):
    def test_shipping_free(self):
        cost, currency, is_free = parse_shipping_info("Free shipping")
        self.assertTrue(is_free)
        self.assertEqual(cost, 0.0)

    def test_shipping_paid(self):
        cost, currency, is_free = parse_shipping_info("+$12.50 shipping")
        self.assertFalse(is_free)
        self.assertEqual(cost, 12.5)
        self.assertEqual(currency, "USD")

    def test_listing_type_html(self):
        self.assertEqual(
            parse_ebay_html_listing_type("Buy It Now or Best Offer"),
            "Buy It Now / Best Offer",
        )
        self.assertEqual(parse_ebay_html_listing_type("3 bids · Auction"), "Аукцион")

    def test_api_buying_options(self):
        self.assertEqual(
            format_ebay_listing_type(["FIXED_PRICE", "BEST_OFFER"]),
            "Buy It Now / Best Offer",
        )

    def test_api_shipping(self):
        cost, currency, is_free = shipping_from_ebay_api(
            {
                "shippingOptions": [
                    {
                        "shippingCostType": "FIXED",
                        "shippingCost": {"value": "7.99", "currency": "USD"},
                    }
                ]
            }
        )
        self.assertEqual(cost, 7.99)
        self.assertEqual(currency, "USD")
        self.assertFalse(is_free)

    def test_caption_includes_shipping_and_type(self):
        listing = Listing(
            id="1",
            title="Nike Dunk",
            description="Nice shoes",
            price=99.0,
            currency="USD",
            image_url=None,
            item_url="https://ebay.com/itm/1",
            source=Source.EBAY_PARSER,
            shipping_cost=5.0,
            shipping_currency="USD",
            listing_type="Buy It Now",
        )
        caption = build_caption(listing)
        self.assertIn("Доставка: 5.00 USD", caption)
        self.assertIn("Тип: Buy It Now", caption)


if __name__ == "__main__":
    unittest.main()
