import pytest
from pydantic import ValidationError

from bot.models import Source
from bot.web.api import SearchCreateIn, SearchUpdateIn


def test_create_search_accepts_and_deduplicates_multiple_sources():
    payload = SearchCreateIn(
        sources=[Source.ETSY, Source.EBAY_PARSER, Source.ETSY],
        keywords="vintage brooch",
    )
    assert payload.sources == [Source.ETSY, Source.EBAY_PARSER]


def test_create_search_keeps_legacy_single_source_input():
    payload = SearchCreateIn(source=Source.POSHMARK, keywords="vintage bag")
    assert payload.sources == [Source.POSHMARK]


def test_create_search_accepts_categories_payload():
    payload = SearchCreateIn(
        sources=[Source.ETSY],
        keywords="vintage brooch",
        categories={"etsy": [{"taxonomy_id": 10, "category_path": "Jewelry"}]},
    )
    assert payload.categories is not None
    assert "etsy" in payload.categories


def test_create_and_update_require_at_least_one_selected_source():
    with pytest.raises(ValidationError):
        SearchCreateIn(sources=[], keywords="vintage brooch")
    with pytest.raises(ValidationError):
        SearchUpdateIn(sources=[])
