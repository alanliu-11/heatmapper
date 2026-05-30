import os
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.getenv("DATABASE_URL", "")


class _Conn:
    """Thin wrapper that mimics the sqlite3.Connection API the app relies on:

    - `?` placeholders are translated to psycopg's `%s`
    - `conn.execute(sql, params).fetchone()/.fetchall()` works
    - rows behave like dicts (`row["col"]` and `dict(row)`)

    This keeps the ~40 existing call sites unchanged while running on Postgres.
    """

    def __init__(self, conn: psycopg.Connection):
        self._conn = conn

    def execute(self, sql: str, params=()):
        cur = self._conn.cursor()
        # `params or None` so parameterless statements skip %-interpolation.
        cur.execute(sql.replace("?", "%s"), params or None)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def get_db() -> _Conn:
    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    return _Conn(conn)


def seconds_since(value) -> float:
    """Age in seconds of a timestamp from Postgres (tz-aware datetime) or a
    legacy string. Returns +inf when the value is missing."""
    if value is None:
        return float("inf")
    if isinstance(value, str):
        value = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - value).total_seconds()


# Statements run individually (psycopg's extended protocol disallows multiple
# statements per execute). `citext` gives case-insensitive usernames/tickers,
# replacing SQLite's `COLLATE NOCASE`.
_SCHEMA = [
    "CREATE EXTENSION IF NOT EXISTS citext",
    """CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username CITEXT NOT NULL UNIQUE,
        email TEXT DEFAULT '',
        password_hash TEXT NOT NULL,
        created_at TIMESTAMPTZ DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS watchlist (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        ticker CITEXT NOT NULL,
        added_at TIMESTAMPTZ DEFAULT now(),
        UNIQUE(user_id, ticker)
    )""",
    """CREATE TABLE IF NOT EXISTS news (
        id SERIAL PRIMARY KEY,
        ticker CITEXT NOT NULL,
        title TEXT NOT NULL,
        source TEXT,
        url TEXT,
        snippet TEXT,
        published TEXT,
        sentiment_score REAL,
        sentiment_label TEXT,
        fetched_at TIMESTAMPTZ DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS company_data (
        id SERIAL PRIMARY KEY,
        ticker CITEXT NOT NULL UNIQUE,
        data TEXT NOT NULL,
        fetched_at TIMESTAMPTZ DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS push_subscriptions (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        subscription_json TEXT NOT NULL,
        created_at TIMESTAMPTZ DEFAULT now(),
        UNIQUE(user_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_news_ticker ON news(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_news_fetched ON news(fetched_at)",
    "CREATE INDEX IF NOT EXISTS idx_watchlist_user_id ON watchlist(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_watchlist_ticker ON watchlist(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_push_user_id ON push_subscriptions(user_id)",
]


def init_db():
    conn = get_db()
    try:
        for stmt in _SCHEMA:
            conn.execute(stmt)
        conn.commit()
    finally:
        conn.close()
