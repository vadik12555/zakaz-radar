"""SQLite storage with versioned migrations (PRAGMA user_version). Same approach as Leadly."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)

MIGRATIONS: list[str] = [
    # 1 — initial schema
    """
    CREATE TABLE users (
        id              INTEGER PRIMARY KEY,           -- Telegram user id
        username        TEXT,
        first_name      TEXT,
        created_at      TEXT NOT NULL,
        last_seen_at    TEXT NOT NULL,
        onboarded       INTEGER NOT NULL DEFAULT 0,
        radar_on        INTEGER NOT NULL DEFAULT 1,
        categories      TEXT NOT NULL DEFAULT '[]',    -- JSON list of FL.ru category ids
        keywords        TEXT NOT NULL DEFAULT '[]',
        excludes        TEXT NOT NULL DEFAULT '[]',
        min_budget      INTEGER NOT NULL DEFAULT 0,
        show_no_budget  INTEGER NOT NULL DEFAULT 1,
        quiet           INTEGER NOT NULL DEFAULT 0,    -- no radar notifications 23:00–08:00 MSK
        template        TEXT,                          -- own response template (PRO)
        p_name          TEXT,
        p_spec          TEXT,
        p_portfolio     TEXT,
        p_experience    TEXT,
        blocked         INTEGER NOT NULL DEFAULT 0,    -- the user blocked the bot
        limit_note_day  TEXT                           -- day the "10 notifications" note was sent
    );

    CREATE TABLE orders (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        link          TEXT NOT NULL UNIQUE,            -- dedup key
        fl_id         INTEGER,
        title         TEXT NOT NULL,
        budget        INTEGER,
        currency      TEXT,
        description   TEXT NOT NULL,
        categories    TEXT NOT NULL,                   -- JSON list of raw "Раздел / Подраздел"
        cat_ids       TEXT NOT NULL,                   -- JSON list of ids
        published_at  TEXT NOT NULL,
        fetched_at    TEXT NOT NULL
    );
    CREATE INDEX idx_orders_published ON orders(published_at);
    CREATE INDEX idx_orders_fetched ON orders(fetched_at);

    CREATE TABLE deliveries (
        user_id    INTEGER NOT NULL,
        order_id   INTEGER NOT NULL,
        due_at     TEXT NOT NULL,                      -- FREE: fetched_at + 30 min
        status     TEXT NOT NULL,                      -- pending | sent | quiet | limited | failed
        sent_at    TEXT,
        sent_day   TEXT,                               -- MSK date, for the daily limit
        created_at TEXT NOT NULL,
        PRIMARY KEY (user_id, order_id)
    );
    CREATE INDEX idx_deliveries_status ON deliveries(status, due_at);

    CREATE TABLE favorites (
        user_id    INTEGER NOT NULL,
        order_id   INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (user_id, order_id)
    );

    CREATE TABLE opens (
        user_id    INTEGER NOT NULL,
        order_id   INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (user_id, order_id)
    );

    CREATE TABLE searches (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,
        query      TEXT,                               -- NULL = by the user's filters
        order_ids  TEXT NOT NULL,                      -- JSON list, newest first
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_searches_user ON searches(user_id);

    CREATE TABLE usage_events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL,
        kind        TEXT NOT NULL,
        ref         TEXT NOT NULL,                     -- idempotency key: the same search is not charged twice
        created_at  TEXT NOT NULL,
        UNIQUE (user_id, kind, ref)
    );

    CREATE TABLE payments (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id             INTEGER NOT NULL,
        plan                TEXT NOT NULL,
        amount              INTEGER NOT NULL,
        currency            TEXT NOT NULL,
        status              TEXT NOT NULL,             -- pending | paid | refunded | manual
        payload             TEXT NOT NULL UNIQUE,
        telegram_charge_id  TEXT UNIQUE,
        provider_charge_id  TEXT,
        error               TEXT,
        created_at          TEXT NOT NULL,
        paid_at             TEXT
    );
    CREATE INDEX idx_payments_user ON payments(user_id);

    CREATE TABLE subscriptions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL,
        plan        TEXT NOT NULL,
        starts_at   TEXT NOT NULL,
        ends_at     TEXT NOT NULL,
        status      TEXT NOT NULL,                     -- active | refunded
        source      TEXT NOT NULL,                     -- stars | manual
        payment_id  INTEGER REFERENCES payments(id),
        created_at  TEXT NOT NULL
    );
    CREATE INDEX idx_subscriptions_user ON subscriptions(user_id, ends_at);

    CREATE TABLE events (
        user_id    INTEGER NOT NULL,
        name       TEXT NOT NULL,                      -- funnel: start | filters | search | pay | paid
        created_at TEXT NOT NULL,
        PRIMARY KEY (user_id, name)
    );

    CREATE TABLE kv (
        key    TEXT PRIMARY KEY,
        value  TEXT
    );

    CREATE TABLE fsm (
        key    TEXT PRIMARY KEY,
        state  TEXT,
        data   TEXT NOT NULL DEFAULT '{}'
    );
    """,
]


@dataclass
class User:
    id: int
    username: str | None
    first_name: str | None
    created_at: str
    last_seen_at: str
    onboarded: int = 0
    radar_on: int = 1
    categories: str = "[]"
    keywords: str = "[]"
    excludes: str = "[]"
    min_budget: int = 0
    show_no_budget: int = 1
    quiet: int = 0
    template: str | None = None
    p_name: str | None = None
    p_spec: str | None = None
    p_portfolio: str | None = None
    p_experience: str | None = None
    blocked: int = 0
    limit_note_day: str | None = None

    @property
    def cats(self) -> list[int]:
        return json.loads(self.categories or "[]")

    @property
    def kws(self) -> list[str]:
        return json.loads(self.keywords or "[]")

    @property
    def excl(self) -> list[str]:
        return json.loads(self.excludes or "[]")


USER_FIELDS = {f.name for f in fields(User)}


def order_row(row) -> dict:
    d = dict(row)
    d["categories"] = json.loads(d["categories"])
    d["cat_ids"] = json.loads(d["cat_ids"])
    return d


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._conn: aiosqlite.Connection | None = None
        self.lock = asyncio.Lock()  # serialises read-check-write sequences (limits, payments)

    async def connect(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path, timeout=30)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            await self._conn.execute("PRAGMA journal_mode = WAL")
        await self.migrate()

    async def migrate(self) -> None:
        version = (await self.fetchone("PRAGMA user_version"))[0]
        for number, script in enumerate(MIGRATIONS, start=1):
            if number <= version:
                continue
            log.info("applying database migration %s", number)
            await self.conn.executescript("BEGIN;\n" + script + f"\nPRAGMA user_version = {number};\nCOMMIT;")
        await self.conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if not self._conn:
            raise RuntimeError("Database is not connected")
        return self._conn

    async def fetchone(self, sql: str, *args: Any) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, args) as cur:
            return await cur.fetchone()

    async def fetchall(self, sql: str, *args: Any) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, args) as cur:
            return list(await cur.fetchall())

    async def scalar(self, sql: str, *args: Any) -> Any:
        row = await self.fetchone(sql, *args)
        return row[0] if row else None

    async def execute(self, sql: str, *args: Any, commit: bool = True) -> int:
        """Run a statement; returns lastrowid (INSERT) or rowcount."""
        cur = await self.conn.execute(sql, args)
        if commit:
            await self.conn.commit()
        return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            try:
                yield self
                await self.conn.commit()
            except Exception:
                await self.conn.rollback()
                raise

    # --- key/value ----------------------------------------------------------------

    async def kv_get(self, key: str) -> str | None:
        return await self.scalar("SELECT value FROM kv WHERE key = ?", key)

    async def kv_set(self, key: str, value: str | None) -> None:
        await self.execute("INSERT INTO kv (key, value) VALUES (?, ?) "
                           "ON CONFLICT(key) DO UPDATE SET value = excluded.value", key, value)

    # --- users ---------------------------------------------------------------------

    async def upsert_user(self, user_id: int, username: str | None, first_name: str | None,
                          now: str) -> tuple[User, bool]:
        row = await self.fetchone("SELECT * FROM users WHERE id = ?", user_id)
        if row is None:
            await self.execute("INSERT OR IGNORE INTO users (id, username, first_name, created_at, last_seen_at) "
                               "VALUES (?, ?, ?, ?, ?)", user_id, username, first_name, now, now)
            return await self.get_user(user_id), True
        if (row["username"], row["first_name"]) != (username, first_name) or row["last_seen_at"][:15] != now[:15] \
                or row["blocked"]:
            await self.execute("UPDATE users SET username = ?, first_name = ?, last_seen_at = ?, blocked = 0 "
                               "WHERE id = ?", username, first_name, now, user_id)
            row = await self.fetchone("SELECT * FROM users WHERE id = ?", user_id)
        return User(**dict(row)), False

    async def get_user(self, user_id: int) -> User | None:
        row = await self.fetchone("SELECT * FROM users WHERE id = ?", user_id)
        return User(**dict(row)) if row else None

    async def update_user(self, user_id: int, **values: Any) -> None:
        bad = set(values) - USER_FIELDS
        if bad:
            raise ValueError(f"unknown user fields: {bad}")
        values = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v) for k, v in values.items()}
        cols = ", ".join(f"{k} = ?" for k in values)
        await self.execute(f"UPDATE users SET {cols} WHERE id = ?", *values.values(), user_id)
