from __future__ import annotations

from abc import ABC, abstractmethod

from bot.models import Listing, Search, Source


class ProviderError(Exception):
    """Ошибка провайдера маркетплейса."""


class BaseProvider(ABC):
    source: Source

    @abstractmethod
    async def search(self, search: Search, *, limit: int = 20) -> list[Listing]:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None
