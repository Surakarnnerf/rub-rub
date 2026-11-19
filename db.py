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
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote

try:  # only needed when a Postgres URL is configured
    import psycopg
    psycopg_import_error = None
except ImportError as _error:  # SQLite-only installs (your PC, Docker) don't have it
    psycopg = None
    psycopg_import_error = str(_error)  # kept so the log can say why (e.g. a missing libpq)

# Hosting platforms name the variable differently; the first one holding a postgres:// URL wins.
POSTGRES_ENV_NAMES = ("RUBRUB_DATABASE_URL", "DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL")

_sqlite_path: Path | None = None
_postgres_url: str | None = None

integrity_errors: tuple = (sqlite3.IntegrityError,) + ((psycopg.IntegrityError,) if psycopg else ())


class DatabaseNotConfigured(RuntimeError):
    """Nothing to connect to, e.g. a Vercel project without a Postgres address. `code` is what the
    API answers (503), so the owner can tell the cases apart without reading logs. `details` may list
    related variable NAMES (never values) to help the owner fix the settings."""

    code = "database_not_configured"

    def __init__(self, message: str, details=None):
        super().__init__(message)
        self.details = list(details or [])


class DriverMissing(DatabaseNotConfigured):
    """There is an address, but the psycopg package could not be imported."""

    code = "database_driver_missing"


class DatabaseUnavailable(RuntimeError):
    """The address is set and the driver is there, but the database refused or did not answer."""

    code = "database_unreachable"


_URL_IN_TEXT = re.compile(r"postgres(?:ql)?://[^\s'\"<>]+")


def extract_postgres_url(value) -> str | None:
    """The postgres:// address inside a variable's value. It tolerates what people end up with:
    quotes around it, or a whole command such as  psql 'postgresql://user:pass@host/db'."""
    match = _URL_IN_TEXT.search(value) if isinstance(value, str) else None
    return match.group(0) if match else None


def postgres_url_from_parts(env) -> str | None:
    """Build the address from libpq's separate variables (PGHOST, PGUSER, PGPASSWORD, PGDATABASE),
    which Neon's Vercel integration also sets. Passwords are percent-encoded so any character is safe."""
    host, user, database = env.get("PGHOST"), env.get("PGUSER"), env.get("PGDATABASE")
    if not (host and user and database):
        return None
    password, port = env.get("PGPASSWORD"), env.get("PGPORT")
    auth = quote(user, safe="") + (":" + quote(password, safe="") if password else "")
    return (f"postgresql://{auth}@{host}{':' + port if port else ''}/{quote(database, safe='')}"
            f"?sslmode={quote(env.get('PGSSLMODE') or 'require', safe='')}")


def postgres_url_from_env(environ=None, any_name: bool = False) -> str | None:
    """The Postgres address the hosting platform gave us.

    The usual names are tried first. With any_name=True (used on Vercel) any variable that holds a
    postgres:// address counts, because Vercel lets you put a prefix in front of the names a database
    integration creates (STORAGE_URL, MYDB_DATABASE_URL...); as a last resort the separate PG*
    variables are assembled. Pooled connections win over direct ones, because serverless functions
    open a connection per request."""
    env = os.environ if environ is None else environ
    for name in POSTGRES_ENV_NAMES:
        url = extract_postgres_url(env.get(name))
        if url:
            return url
    if not any_name:
        return None
    found = []
    for name, value in env.items():
        url = extract_postgres_url(value)
        if url:
            upper = name.upper()
            penalty = (2 if ("UNPOOLED" in upper or "NON_POOLING" in upper) else 0) + (1 if "PRISMA" in upper else 0)
            found.append((penalty, name, url))
    return min(found)[2] if found else postgres_url_from_parts(env)


def describe_variables(environ=None) -> list:
    """For the owner when nothing was found: the names of variables that might hold a database
    address, each marked postgres-url (it holds one) or other. NEVER the values."""
    env = os.environ if environ is None else environ
    hints = ("URL", "POSTGRES", "DATABASE", "STORAGE", "NEON", "PG")
    return [f"{name}={'postgres-url' if extract_postgres_url(env[name]) else 'other'}"
            for name in sorted(env) if any(h in name.upper() for h in hints)]


def related_variable_names(environ=None) -> list:
    """Names only (no values, no verdicts): for log messages."""
    return [item.split("=")[0] for item in describe_variables(environ)]


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
            raise DriverMissing(
                "A Postgres address is set but the psycopg package could not be imported "
                f"({psycopg_import_error or 'not installed'}). Check that requirements.txt was installed in the build.")
        try:
            # prepare_threshold=None: poolers such as Neon's PgBouncer don't support prepared statements
            raw = psycopg.connect(_postgres_url, row_factory=_row_factory, prepare_threshold=None, connect_timeout=10)
        except psycopg.Error as e:
            raise DatabaseUnavailable(f"Could not connect to Postgres: {e}") from e
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
    is_expert    INTEGER NOT NULL DEFAULT 0,
    expert_title TEXT NOT NULL DEFAULT '',
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
    city            TEXT NOT NULL DEFAULT 'other',
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
CREATE TABLE IF NOT EXISTS saves (
    user_id    {big} NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    review_id  {big} NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    created_at {big} NOT NULL,
    PRIMARY KEY (user_id, review_id)
);
CREATE TABLE IF NOT EXISTS collections (
    id          {pk},
    user_id     {big} NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_public   INTEGER NOT NULL DEFAULT 1,
    created_at  {big} NOT NULL,
    updated_at  {big} NOT NULL
);
CREATE TABLE IF NOT EXISTS collection_items (
    collection_id {big} NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    review_id     {big} NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    added_at      {big} NOT NULL,
    PRIMARY KEY (collection_id, review_id)
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
CREATE UNIQUE INDEX IF NOT EXISTS collections_user_name ON collections (user_id, lower(name));
CREATE INDEX IF NOT EXISTS idx_collections_user ON collections(user_id);
CREATE INDEX IF NOT EXISTS idx_saves_review ON saves(review_id);
"""
# (the index on reviews(city) is created in rubrub.init_db, after an older table got its city column)


# Every table the schema creates, children before parents (the tests use this to start from nothing).
TABLE_NAMES = ("collection_items", "collections", "saves", "likes", "photos", "rate_hits", "sessions", "reviews", "users", "meta")


def schema_sql(engine_name: str) -> str:
    if engine_name == "postgres":
        return _SCHEMA.format(pk="BIGSERIAL PRIMARY KEY", big="BIGINT", blob="BYTEA")
    return _SCHEMA.format(pk="INTEGER PRIMARY KEY AUTOINCREMENT", big="BIGINT", blob="BLOB")


def column_exists(con: Connection, table: str, column: str) -> bool:
    """Does the table already have this column? (used to upgrade older databases)"""
    if con.engine == "sqlite":
        return any(r["name"] == column for r in con.execute(f"PRAGMA table_info({table})"))
    return con.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name = ? AND column_name = ? AND table_schema = current_schema()",
        (table, column),
    ).fetchone() is not None


def get_meta(con: Connection, key: str) -> str | None:
    row = con.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(con: Connection, key: str, value) -> None:
    con.execute("INSERT INTO meta(k, v) VALUES(?, ?) ON CONFLICT (k) DO UPDATE SET v = excluded.v", (key, str(value)))
