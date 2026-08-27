from __future__ import annotations

from abc import ABC, abstractmethod

from bot.models import Listing, Search, Source


class ProviderError(Exception):
    """Ошибка провайдера маркетплейса."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class BaseProvider(ABC):
    source: Source

    @abstractmethod
    async def search(self, search: Search, *, limit: int = 20) -> list[Listing]:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None
