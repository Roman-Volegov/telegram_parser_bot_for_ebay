import tempfile
import unittest
from pathlib import Path

from bot.models import Source
from bot.providers.ebay_parser import EbayParserProvider
from bot.services.categories import (
    MAX_CATEGORIES_PER_SOURCE,
    apply_categories_to_filters,
    categories_for_search,
    normalize_categories_payload,
)
from bot.services.taxonomies import TaxonomyService


class CategoriesHelperTests(unittest.TestCase):
    def test_normalize_and_apply_per_source(self):
        payload = normalize_categories_payload(
            {
                "ebay_api": [
                    {"category_id": "11450", "category_path": "Clothing"},
                    {"category_id": "11450", "category_path": "dup"},
                ],
                "etsy": [{"taxonomy_id": "12", "category_path": "Jewelry"}],
                "poshmark": [
                    {
                        "department": "Women",
                        "category": "Bags",
                        "category_path": "Women > Bags",
                    }
                ],
            }
        )
        self.assertEqual(len(payload["ebay_api"]), 1)
        self.assertEqual(payload["etsy"][0]["taxonomy_id"], 12)
        filters = apply_categories_to_filters(
            {"marketplace": "EBAY_US"},
            source=Source.EBAY_API,
            categories_by_source=payload,
        )
        cats = categories_for_search(filters, Source.EBAY_API)
        self.assertEqual(cats[0]["category_id"], "11450")

    def test_empty_categories_means_all(self):
        self.assertEqual(categories_for_search({}, Source.ETSY), [])
        self.assertEqual(
            categories_for_search({"categories": []}, Source.POSHMARK),
            [],
        )

    def test_max_categories_cap(self):
        raw = {
            "ebay_parser": [
                {"category_id": str(i), "category_path": f"C{i}"}
                for i in range(MAX_CATEGORIES_PER_SOURCE + 5)
            ]
        }
        payload = normalize_categories_payload(raw)
        self.assertEqual(len(payload["ebay_parser"]), MAX_CATEGORIES_PER_SOURCE)


class TaxonomyServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = TaxonomyService(Path(self.tmp.name) / "tax")

    async def asyncTearDown(self):
        await self.service.aclose()
        self.tmp.cleanup()

    async def test_seed_search_and_children(self):
        items = await self.service.search(source="ebay_api", q="cloth", marketplace="EBAY_US")
        self.assertTrue(any("Clothing" in (item.get("path") or "") for item in items))
        roots = await self.service.children(source="ebay", marketplace="EBAY_US")
        self.assertGreater(len(roots), 5)
        clothing = next(item for item in roots if item["id"] == "11450")
        children = await self.service.children(
            source="ebay_parser",
            parent_id=clothing["id"],
            marketplace="EBAY_US",
        )
        self.assertTrue(children)
        etsy = await self.service.search(source="etsy", q="jewel")
        self.assertTrue(etsy)
        posh = await self.service.children(source="poshmark")
        self.assertTrue(any(item["name"] == "Women" for item in posh))

    async def test_status_reports_trees(self):
        status = self.service.status()
        self.assertEqual(status["ttl_days"], 30)
        self.assertGreaterEqual(len(status["trees"]), 3)


class EbayParserCategoryParamsTests(unittest.TestCase):
    def test_build_params_includes_sacat(self):
        from bot.models import Search

        provider = EbayParserProvider()
        search = Search(
            id=1,
            telegram_id=1,
            source=Source.EBAY_PARSER,
            keywords="nike",
        )
        params = provider._build_params(search, category_id="11450")
        self.assertEqual(params["_sacat"], "11450")
