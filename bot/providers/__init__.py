from bot.models import Source
from bot.providers.base import BaseProvider, ProviderError
from bot.providers.ebay_api import EbayApiProvider
from bot.providers.ebay_parser import EbayParserProvider
from bot.providers.poshmark import PoshmarkProvider

__all__ = [
    "BaseProvider",
    "EbayApiProvider",
    "EbayParserProvider",
    "PoshmarkProvider",
    "ProviderError",
    "get_provider",
]


def get_provider(source: Source, **kwargs) -> BaseProvider:
    if source is Source.EBAY_API:
        return EbayApiProvider(**kwargs)
    if source is Source.EBAY_PARSER:
        return EbayParserProvider(**kwargs)
    if source is Source.POSHMARK:
        return PoshmarkProvider(**kwargs)
    raise ValueError(f"Unsupported source: {source}")
