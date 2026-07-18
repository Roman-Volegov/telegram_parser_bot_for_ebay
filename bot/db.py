from __future__ import annotations

import aiosqlite
from pathlib import Path

from bot.models import Marketplace, WatchFilter


SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    marketplace TEXT NOT NULL,
    query TEXT NOT NULL,
    min_price REAL,
    max_price REAL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS seen_listings (
    watch_id INTEGER NOT NULL,
    external_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (watch_id, external_id),
    FOREIGN KEY (watch_id) REFERENCES watches(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_watches_user ON watches(user_id);
CREATE INDEX IF NOT EXISTS idx_watches_active ON watches(active);
"""


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected")
        return self._conn

    async def add_watch(
        self,
        user_id: int,
        marketplace: Marketplace,
        query: str,
        min_price: float | None = None,
        max_price: float | None = None,
    ) -> WatchFilter:
        cursor = await self.conn.execute(
            """
            INSERT INTO watches (user_id, marketplace, query, min_price, max_price)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, marketplace.value, query.strip(), min_price, max_price),
        )
        await self.conn.commit()
        watch_id = cursor.lastrowid
        assert watch_id is not None
        return WatchFilter(
            id=watch_id,
            user_id=user_id,
            marketplace=marketplace,
            query=query.strip(),
            min_price=min_price,
            max_price=max_price,
            active=True,
        )

    async def list_watches(self, user_id: int) -> list[WatchFilter]:
        cursor = await self.conn.execute(
            """
            SELECT id, user_id, marketplace, query, min_price, max_price, active
            FROM watches
            WHERE user_id = ? AND active = 1
            ORDER BY id DESC
            """,
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_watch(row) for row in rows]

    async def list_active_watches(self) -> list[WatchFilter]:
        cursor = await self.conn.execute(
            """
            SELECT id, user_id, marketplace, query, min_price, max_price, active
            FROM watches
            WHERE active = 1
            ORDER BY id ASC
            """
        )
        rows = await cursor.fetchall()
        return [self._row_to_watch(row) for row in rows]

    async def deactivate_watch(self, user_id: int, watch_id: int) -> bool:
        cursor = await self.conn.execute(
            """
            UPDATE watches
            SET active = 0
            WHERE id = ? AND user_id = ? AND active = 1
            """,
            (watch_id, user_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def mark_seen(self, watch_id: int, external_ids: list[str]) -> None:
        if not external_ids:
            return
        await self.conn.executemany(
            """
            INSERT OR IGNORE INTO seen_listings (watch_id, external_id)
            VALUES (?, ?)
            """,
            [(watch_id, external_id) for external_id in external_ids],
        )
        await self.conn.commit()

    async def filter_new_ids(self, watch_id: int, external_ids: list[str]) -> list[str]:
        if not external_ids:
            return []
        placeholders = ",".join("?" for _ in external_ids)
        cursor = await self.conn.execute(
            f"""
            SELECT external_id
            FROM seen_listings
            WHERE watch_id = ? AND external_id IN ({placeholders})
            """,
            (watch_id, *external_ids),
        )
        rows = await cursor.fetchall()
        known = {row["external_id"] for row in rows}
        return [external_id for external_id in external_ids if external_id not in known]

    @staticmethod
    def _row_to_watch(row: aiosqlite.Row) -> WatchFilter:
        return WatchFilter(
            id=row["id"],
            user_id=row["user_id"],
            marketplace=Marketplace(row["marketplace"]),
            query=row["query"],
            min_price=row["min_price"],
            max_price=row["max_price"],
            active=bool(row["active"]),
        )
