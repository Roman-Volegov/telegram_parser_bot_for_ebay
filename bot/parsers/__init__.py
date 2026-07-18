from bot.parsers.base import BaseParser, ParserError
from bot.parsers.ebay import EbayParser
from bot.parsers.poshmark import PoshmarkParser
from bot.models import Marketplace

__all__ = [
    "BaseParser",
    "EbayParser",
    "PoshmarkParser",
    "ParserError",
    "get_parser",
]


def get_parser(marketplace: Marketplace, **kwargs) -> BaseParser:
    if marketplace is Marketplace.EBAY:
        return EbayParser(
            app_id=kwargs.get("app_id", ""),
            cert_id=kwargs.get("cert_id", ""),
            marketplace_id=kwargs.get("marketplace_id", "EBAY_US"),
        )
    if marketplace is Marketplace.POSHMARK:
        return PoshmarkParser()
    raise ValueError(f"Unsupported marketplace: {marketplace}")
