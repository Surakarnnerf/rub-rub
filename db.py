"""Database access for Rub rub: one small interface, two engines.

  * SQLite   - a file on disk. Your own PC, Docker, and hosts that keep a disk (Render...).
  * Postgres - a hosted database. Serverless hosts such as Vercel have no disk, so there the
               accounts, reviews and photos live in Postgres (Neon, Supabase, ...).

The application writes its SQL once, in the subset both engines understand: "?" placeholders,
RETURNING id, ON CONFLICT, lower(...) for case-insensitive text. Postgres needs the `psycopg`
package (see requirements.txt); SQLite needs nothing.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

try:  # only needed when a Postgres URL is configured
    import psycopg
except ImportError:  # SQLite-only installs (your PC, Docker) don't have it
    psycopg = None

# Hosting platforms name the variable differently; the first one holding a postgres:// URL wins.
POSTGRES_ENV_NAMES = ("RUBRUB_DATABASE_URL", "DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL")

_sqlite_path: Path | None = None
_postgres_url: str | None = None

integrity_errors: tuple = (sqlite3.IntegrityError,) + ((psycopg.IntegrityError,) if psycopg else ())


class DatabaseNotConfigured(RuntimeError):
    """Raised when there is nothing to connect to, e.g. a Vercel project without a Postgres database."""


def postgres_url_from_env(environ=None) -> str | None:
    env = os.environ if environ is None else environ
    for name in POSTGRES_ENV_NAMES:
        value = (env.get(name) or "").strip()
        if value.startswith(("postgres://", "postgresql://")):
            return value
    return None


def configure(sqlite_path=None, postgres_url: str | None = None) -> None:
    """Choose the engine. A Postgres URL wins; otherwise the SQLite file is used."""
    global _sqlite_path, _postgres_url
    _sqlite_path = Path(sqlite_path) if sqlite_path else None
    _postgres_url = postgres_url or None


def settings() -> tuple:
    """(sqlite_path, postgres_url): lets a caller put the configuration back after changing it."""
    return _sqlite_path, _postgres_url


def engine() -> str:
    return "postgres" if _postgres_url else "sqlite"


class Row:
    """A Postgres result row. Like sqlite3.Row, both row["name"] and row[0] work."""

    __slots__ = ("_index", "_values")

    def __init__(self, index: dict, values: tuple):
        self._index = index
        self._values = values

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return self._values[key]
        return self._values[self._index[key]]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def keys(self) -> list:
        return list(self._index)


def _row_factory(cursor):
    index = {c.name: i for i, c in enumerate(cursor.description or [])}
    return lambda values: Row(index, tuple(values))


_pg_sql_cache: dict[str, str] = {}


def _pg_sql(sql: str) -> str:
    """The application's "?" placeholders become psycopg's "%s" (a literal % must be doubled)."""
    out = _pg_sql_cache.get(sql)
    if out is None:
        out = _pg_sql_cache[sql] = sql.replace("%", "%%").replace("?", "%s")
    return out


class Connection:
    """The same few methods on both engines. Rows support row["name"] and row[0]."""

    def __init__(self, raw, engine_name: str):
        self._raw = raw
        self.engine = engine_name

    def execute(self, sql: str, params=()):
        if self.engine == "postgres":
            return self._raw.execute(_pg_sql(sql), tuple(params))
        return self._raw.execute(sql, tuple(params))

    def executescript(self, sql: str) -> None:
        if self.engine == "postgres":
            self._raw.execute(sql)  # no parameters, so Postgres accepts several statements at once
        else:
            self._raw.executescript(sql)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()


def connect() -> Connection:
    if _postgres_url:
        if psycopg is None:
            raise DatabaseNotConfigured("DATABASE_URL is set but the 'psycopg' package is not installed")
        # prepare_threshold=None: poolers such as Neon's PgBouncer don't support prepared statements
        raw = psycopg.connect(_postgres_url, row_factory=_row_factory, prepare_threshold=None, connect_timeout=10)
        return Connection(raw, "postgres")
    if _sqlite_path is None:
        raise DatabaseNotConfigured("no database is configured")
    raw = sqlite3.connect(_sqlite_path, timeout=10)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    return Connection(raw, "sqlite")


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id           {pk},
    username     TEXT NOT NULL,
    display_name TEXT NOT NULL,
    pw_hash      TEXT,
    bio          TEXT NOT NULL DEFAULT '',
    is_featured  INTEGER NOT NULL DEFAULT 0,
    created_at   {big} NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS users_username_lower ON users (lower(username));
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    {big} NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at {big} NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
    id              {pk},
    user_id         {big} NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    restaurant_name TEXT NOT NULL,
    area            TEXT NOT NULL DEFAULT '',
    menu_item       TEXT NOT NULL,
    category        TEXT NOT NULL DEFAULT 'other',
    rating          INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    price_thb       INTEGER NOT NULL CHECK (price_thb BETWEEN 1 AND 10000),
    cash_only       INTEGER NOT NULL DEFAULT 0,
    pay_qr          INTEGER NOT NULL DEFAULT 0,
    pay_card        INTEGER NOT NULL DEFAULT 0,
    open_days       TEXT NOT NULL,
    open_time       TEXT NOT NULL,
    close_time      TEXT NOT NULL,
    spice           INTEGER NOT NULL DEFAULT 0 CHECK (spice BETWEEN 0 AND 3),
    body            TEXT NOT NULL DEFAULT '',
    photo           TEXT,
    is_sample       INTEGER NOT NULL DEFAULT 0,
    created_at      {big} NOT NULL,
    updated_at      {big} NOT NULL
);
CREATE TABLE IF NOT EXISTS likes (
    user_id   {big} NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    review_id {big} NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, review_id)
);
CREATE TABLE IF NOT EXISTS photos (
    name         TEXT PRIMARY KEY,
    content_type TEXT NOT NULL,
    data         {blob} NOT NULL,
    created_at   {big} NOT NULL
);
CREATE TABLE IF NOT EXISTS rate_hits (
    rkey TEXT NOT NULL,
    ts   {big} NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reviews_user ON reviews(user_id);
CREATE INDEX IF NOT EXISTS idx_reviews_created ON reviews(created_at);
CREATE INDEX IF NOT EXISTS idx_rate_hits ON rate_hits(rkey, ts);
"""


def schema_sql(engine_name: str) -> str:
    if engine_name == "postgres":
        return _SCHEMA.format(pk="BIGSERIAL PRIMARY KEY", big="BIGINT", blob="BYTEA")
    return _SCHEMA.format(pk="INTEGER PRIMARY KEY AUTOINCREMENT", big="BIGINT", blob="BLOB")


def get_meta(con: Connection, key: str) -> str | None:
    row = con.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(con: Connection, key: str, value) -> None:
    con.execute("INSERT INTO meta(k, v) VALUES(?, ?) ON CONFLICT (k) DO UPDATE SET v = excluded.v", (key, str(value)))
