from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from bot.models import Search, Source, User, UserStatus

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    setup_completed INTEGER NOT NULL DEFAULT 0,
    enabled_sources TEXT NOT NULL DEFAULT '[]',
    ebay_marketplace TEXT NOT NULL DEFAULT 'EBAY_US',
    ebay_deletion_token TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS credentials (
    telegram_id INTEGER PRIMARY KEY,
    ebay_client_id_enc TEXT NOT NULL,
    ebay_client_secret_enc TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    keywords TEXT NOT NULL,
    max_price REAL,
    min_price REAL,
    condition TEXT,
    buy_it_now INTEGER NOT NULL DEFAULT 1,
    paused INTEGER NOT NULL DEFAULT 0,
    filters_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS seen_items (
    search_id INTEGER NOT NULL,
    item_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY (search_id, item_id),
    FOREIGN KEY (search_id) REFERENCES searches(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
CREATE INDEX IF NOT EXISTS idx_searches_user ON searches(telegram_id);
CREATE INDEX IF NOT EXISTS idx_searches_active ON searches(paused, source);
CREATE INDEX IF NOT EXISTS idx_seen_first_seen_at ON seen_items(first_seen_at);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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

    # --- users ---

    async def get_user(self, telegram_id: int) -> User | None:
        cursor = await self.conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,),
        )
        row = await cursor.fetchone()
        return self._row_to_user(row) if row else None

    async def upsert_pending_user(
        self,
        telegram_id: int,
        username: str | None,
        full_name: str | None,
    ) -> tuple[User, bool]:
        """Создаёт pending-пользователя. Возвращает (user, created)."""
        existing = await self.get_user(telegram_id)
        now = _utcnow()
        if existing is not None:
            await self.conn.execute(
                """
                UPDATE users
                SET username = ?, full_name = ?, updated_at = ?
                WHERE telegram_id = ?
                """,
                (username, full_name, now, telegram_id),
            )
            await self.conn.commit()
            user = await self.get_user(telegram_id)
            assert user is not None
            return user, False

        token = secrets.token_urlsafe(24)
        await self.conn.execute(
            """
            INSERT INTO users (
                telegram_id, username, full_name, status, setup_completed,
                enabled_sources, ebay_marketplace, ebay_deletion_token,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, 0, '[]', 'EBAY_US', ?, ?, ?)
            """,
            (telegram_id, username, full_name, UserStatus.PENDING.value, token, now, now),
        )
        await self.conn.commit()
        user = await self.get_user(telegram_id)
        assert user is not None
        return user, True

    async def set_user_status(self, telegram_id: int, status: UserStatus) -> User | None:
        await self.conn.execute(
            "UPDATE users SET status = ?, updated_at = ? WHERE telegram_id = ?",
            (status.value, _utcnow(), telegram_id),
        )
        await self.conn.commit()
        return await self.get_user(telegram_id)

    async def list_users(self, *, status: UserStatus | None = None) -> list[User]:
        if status is None:
            cursor = await self.conn.execute(
                "SELECT * FROM users ORDER BY created_at DESC"
            )
        else:
            cursor = await self.conn.execute(
                "SELECT * FROM users WHERE status = ? ORDER BY created_at DESC",
                (status.value,),
            )
        rows = await cursor.fetchall()
        return [self._row_to_user(row) for row in rows]

    async def save_setup(
        self,
        telegram_id: int,
        *,
        enabled_sources: list[Source],
        ebay_marketplace: str = "EBAY_US",
        setup_completed: bool = True,
    ) -> User | None:
        await self.conn.execute(
            """
            UPDATE users
            SET enabled_sources = ?,
                ebay_marketplace = ?,
                setup_completed = ?,
                updated_at = ?
            WHERE telegram_id = ?
            """,
            (
                json.dumps([s.value for s in enabled_sources]),
                ebay_marketplace,
                1 if setup_completed else 0,
                _utcnow(),
                telegram_id,
            ),
        )
        await self.conn.commit()
        return await self.get_user(telegram_id)

    async def ensure_deletion_token(self, telegram_id: int) -> str:
        user = await self.get_user(telegram_id)
        if user is None:
            raise ValueError("User not found")
        if user.ebay_deletion_token:
            return user.ebay_deletion_token
        token = secrets.token_urlsafe(24)
        await self.conn.execute(
            "UPDATE users SET ebay_deletion_token = ?, updated_at = ? WHERE telegram_id = ?",
            (token, _utcnow(), telegram_id),
        )
        await self.conn.commit()
        return token

    # --- credentials ---

    async def save_credentials(
        self,
        telegram_id: int,
        client_id_enc: str,
        client_secret_enc: str,
    ) -> None:
        now = _utcnow()
        await self.conn.execute(
            """
            INSERT INTO credentials (telegram_id, ebay_client_id_enc, ebay_client_secret_enc, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                ebay_client_id_enc = excluded.ebay_client_id_enc,
                ebay_client_secret_enc = excluded.ebay_client_secret_enc,
                updated_at = excluded.updated_at
            """,
            (telegram_id, client_id_enc, client_secret_enc, now),
        )
        await self.conn.commit()

    async def get_credentials_enc(self, telegram_id: int) -> tuple[str, str] | None:
        cursor = await self.conn.execute(
            """
            SELECT ebay_client_id_enc, ebay_client_secret_enc
            FROM credentials WHERE telegram_id = ?
            """,
            (telegram_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return row["ebay_client_id_enc"], row["ebay_client_secret_enc"]

    async def has_credentials(self, telegram_id: int) -> bool:
        return await self.get_credentials_enc(telegram_id) is not None

    async def revoke_credentials(self, telegram_id: int) -> bool:
        cursor = await self.conn.execute(
            "DELETE FROM credentials WHERE telegram_id = ?",
            (telegram_id,),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    # --- searches ---

    async def add_search(
        self,
        telegram_id: int,
        source: Source,
        keywords: str,
        *,
        max_price: float | None = None,
        min_price: float | None = None,
        condition: str | None = None,
        buy_it_now: bool = True,
        filters_json: dict[str, Any] | None = None,
    ) -> Search:
        now = _utcnow()
        cursor = await self.conn.execute(
            """
            INSERT INTO searches (
                telegram_id, source, keywords, max_price, min_price,
                condition, buy_it_now, paused, filters_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                telegram_id,
                source.value,
                keywords.strip(),
                max_price,
                min_price,
                condition,
                1 if buy_it_now else 0,
                json.dumps(filters_json or {}),
                now,
                now,
            ),
        )
        await self.conn.commit()
        search_id = cursor.lastrowid
        assert search_id is not None
        search = await self.get_search(search_id)
        assert search is not None
        return search

    async def get_search(self, search_id: int) -> Search | None:
        cursor = await self.conn.execute(
            "SELECT * FROM searches WHERE id = ?",
            (search_id,),
        )
        row = await cursor.fetchone()
        return self._row_to_search(row) if row else None

    async def list_searches(self, telegram_id: int) -> list[Search]:
        cursor = await self.conn.execute(
            """
            SELECT * FROM searches
            WHERE telegram_id = ?
            ORDER BY id DESC
            """,
            (telegram_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_search(row) for row in rows]

    async def list_active_searches_for_polling(self) -> list[Search]:
        cursor = await self.conn.execute(
            """
            SELECT s.*
            FROM searches s
            JOIN users u ON u.telegram_id = s.telegram_id
            WHERE s.paused = 0
              AND u.status = ?
              AND u.setup_completed = 1
            ORDER BY s.id ASC
            """,
            (UserStatus.APPROVED.value,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_search(row) for row in rows]

    async def update_search(
        self,
        search_id: int,
        telegram_id: int,
        *,
        keywords: str | None = None,
        max_price: float | None = None,
        min_price: float | None = None,
        condition: str | None = None,
        buy_it_now: bool | None = None,
        clear_max_price: bool = False,
        clear_min_price: bool = False,
    ) -> Search | None:
        search = await self.get_search(search_id)
        if search is None or search.telegram_id != telegram_id:
            return None
        new_keywords = keywords if keywords is not None else search.keywords
        new_max = None if clear_max_price else (max_price if max_price is not None else search.max_price)
        new_min = None if clear_min_price else (min_price if min_price is not None else search.min_price)
        new_condition = condition if condition is not None else search.condition
        new_bin = search.buy_it_now if buy_it_now is None else buy_it_now
        await self.conn.execute(
            """
            UPDATE searches
            SET keywords = ?, max_price = ?, min_price = ?, condition = ?,
                buy_it_now = ?, updated_at = ?
            WHERE id = ? AND telegram_id = ?
            """,
            (
                new_keywords,
                new_max,
                new_min,
                new_condition,
                1 if new_bin else 0,
                _utcnow(),
                search_id,
                telegram_id,
            ),
        )
        await self.conn.commit()
        return await self.get_search(search_id)

    async def set_search_paused(
        self, search_id: int, telegram_id: int, paused: bool
    ) -> bool:
        cursor = await self.conn.execute(
            """
            UPDATE searches
            SET paused = ?, updated_at = ?
            WHERE id = ? AND telegram_id = ?
            """,
            (1 if paused else 0, _utcnow(), search_id, telegram_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def delete_search(self, search_id: int, telegram_id: int) -> bool:
        cursor = await self.conn.execute(
            "DELETE FROM searches WHERE id = ? AND telegram_id = ?",
            (search_id, telegram_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    # --- seen items ---

    async def mark_seen(self, search_id: int, item_ids: list[str]) -> None:
        if not item_ids:
            return
        now = _utcnow()
        await self.conn.executemany(
            """
            INSERT OR IGNORE INTO seen_items (search_id, item_id, first_seen_at)
            VALUES (?, ?, ?)
            """,
            [(search_id, item_id, now) for item_id in item_ids],
        )
        await self.conn.commit()

    async def filter_new_ids(self, search_id: int, item_ids: list[str]) -> list[str]:
        if not item_ids:
            return []
        placeholders = ",".join("?" for _ in item_ids)
        cursor = await self.conn.execute(
            f"""
            SELECT item_id FROM seen_items
            WHERE search_id = ? AND item_id IN ({placeholders})
            """,
            (search_id, *item_ids),
        )
        rows = await cursor.fetchall()
        known = {row["item_id"] for row in rows}
        return [item_id for item_id in item_ids if item_id not in known]

    async def count_seen(self, search_id: int) -> int:
        cursor = await self.conn.execute(
            "SELECT COUNT(*) AS c FROM seen_items WHERE search_id = ?",
            (search_id,),
        )
        row = await cursor.fetchone()
        return int(row["c"]) if row else 0

    async def cleanup_seen_items(self, ttl_days: int) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=ttl_days)
        ).replace(microsecond=0).isoformat()
        cursor = await self.conn.execute(
            "DELETE FROM seen_items WHERE first_seen_at < ?",
            (cutoff,),
        )
        await self.conn.commit()
        return cursor.rowcount

    # --- mappers ---

    @staticmethod
    def _row_to_user(row: aiosqlite.Row) -> User:
        sources_raw = json.loads(row["enabled_sources"] or "[]")
        sources = [Source(s) for s in sources_raw]
        return User(
            telegram_id=row["telegram_id"],
            username=row["username"],
            full_name=row["full_name"],
            status=UserStatus(row["status"]),
            setup_completed=bool(row["setup_completed"]),
            enabled_sources=sources,
            ebay_marketplace=row["ebay_marketplace"] or "EBAY_US",
            ebay_deletion_token=row["ebay_deletion_token"],
        )

    @staticmethod
    def _row_to_search(row: aiosqlite.Row) -> Search:
        return Search(
            id=row["id"],
            telegram_id=row["telegram_id"],
            source=Source(row["source"]),
            keywords=row["keywords"],
            max_price=row["max_price"],
            min_price=row["min_price"],
            condition=row["condition"],
            buy_it_now=bool(row["buy_it_now"]),
            paused=bool(row["paused"]),
            filters_json=json.loads(row["filters_json"] or "{}"),
        )
