import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock

from bot.services.taxonomies import TaxonomyService


def _parser_tree(source: str) -> dict:
    return {
        "source": source,
        "marketplace": None,
        "updated_at": "2099-01-01T00:00:00Z",
        "method": "parser",
        "nodes": [{"id": "1", "name": "Category"}],
    }


def test_background_refresh_skips_fresh_etsy_and_poshmark():
    with TemporaryDirectory() as directory:
        service = TaxonomyService(Path(directory))
        service._save("etsy", _parser_tree("etsy"))
        service._save("poshmark", _parser_tree("poshmark"))
        service._refresh_ebay_parser = AsyncMock(
            return_value={"ok": True, "markets": 1}
        )
        service._refresh_etsy_parser = AsyncMock(return_value={"ok": True})
        service._refresh_poshmark_parser = AsyncMock(return_value={"ok": True})

        result = asyncio.run(service.refresh(force=False))

    assert result["ok"] is True
    service._refresh_ebay_parser.assert_awaited_once_with(force=False)
    service._refresh_etsy_parser.assert_not_awaited()
    service._refresh_poshmark_parser.assert_not_awaited()
    assert result["results"]["etsy"]["skipped"] is True


def test_forced_refresh_updates_all_sources():
    with TemporaryDirectory() as directory:
        service = TaxonomyService(Path(directory))
        service._refresh_ebay_parser = AsyncMock(return_value={"ok": True})
        service._refresh_etsy_parser = AsyncMock(return_value={"ok": True})
        service._refresh_poshmark_parser = AsyncMock(return_value={"ok": True})

        asyncio.run(service.refresh(force=True))

    service._refresh_ebay_parser.assert_awaited_once_with(force=True)
    service._refresh_etsy_parser.assert_awaited_once()
    service._refresh_poshmark_parser.assert_awaited_once()
