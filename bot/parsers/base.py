from __future__ import annotations

from abc import ABC, abstractmethod

from bot.models import Listing, Marketplace, WatchFilter


class ParserError(Exception):
    """Ошибка получения или разбора выдачи маркетплейса."""


class BaseParser(ABC):
    marketplace: Marketplace

    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        min_price: float | None = None,
        max_price: float | None = None,
        limit: int = 20,
    ) -> list[Listing]:
        raise NotImplementedError

    async def search_watch(self, watch: WatchFilter, *, limit: int = 20) -> list[Listing]:
        return await self.search(
            watch.query,
            min_price=watch.min_price,
            max_price=watch.max_price,
            limit=limit,
        )

    async def aclose(self) -> None:
        return None
