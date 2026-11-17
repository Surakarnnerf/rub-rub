#!/usr/bin/env python3
"""Rub rub - Thai street food reviews.

Python 3.9+. On your PC and in Docker it needs nothing but the standard library (SQLite file).
On serverless hosts such as Vercel it runs as a WSGI app (`wsgi_app`, entry point api/index.py)
on a Postgres database, which needs the `psycopg` package (requirements.txt).

    python rubrub.py                       # http://127.0.0.1:8000
    python rubrub.py --host 0.0.0.0        # share on your local network
    python rubrub.py --reset-password NAME # set/reset a user's password

Hosting is configured with environment variables, see README.md: PORT, DATABASE_URL (Postgres),
RUBRUB_DATA_DIR, RUBRUB_TRUST_PROXY, RUBRUB_PROXY_HOPS, RUBRUB_SECURE_COOKIES, RUBRUB_SEED,
RUBRUB_HOST, RUBRUB_RUN_AS_UID.  GET /healthz is the health-check endpoint.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import os
import re
import random
import secrets
import signal
import sys
import threading
import time
import traceback
from http import HTTPStatus
from http.client import HTTPMessage
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

import db

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "public"
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "rubrub.db"
db.configure(sqlite_path=DB_PATH)  # the default: a SQLite file next to the app


def configure(data_dir) -> None:
    """Use a SQLite database in another data folder (used by the tests)."""
    global DATA_DIR, DB_PATH
    DATA_DIR = Path(data_dir).resolve()
    DB_PATH = DATA_DIR / "rubrub.db"
    db.configure(sqlite_path=DB_PATH)


def use_postgres(url: str) -> None:
    """Keep everything in a hosted Postgres database instead of a SQLite file."""
    db.configure(sqlite_path=DB_PATH, postgres_url=url)


# Reverse-proxy settings. Hosting platforms put a proxy in front of the app, and that is where
# HTTPS ends and where the visitor's real IP is only visible in X-Forwarded-* headers. These are
# OFF by default because any client can forge such headers when it talks to the server directly.
TRUST_PROXY = False
PROXY_HOPS = 1  # how many trusted proxies sit in front of the app
FORCE_SECURE = False  # always mark the session cookie Secure


def configure_proxy(trust: bool = False, hops: int = 1, secure: bool = False) -> None:
    global TRUST_PROXY, PROXY_HOPS, FORCE_SECURE
    TRUST_PROXY, PROXY_HOPS, FORCE_SECURE = bool(trust), max(1, int(hops)), bool(secure)


def env_flag(name: str, default: bool = False, environ=None) -> bool:
    value = (os.environ if environ is None else environ).get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def drop_privileges(uid: int) -> None:
    """Containers start as root and a freshly mounted volume is usually owned by root. Fix the
    ownership of the data folder, then carry on as an unprivileged user. No-op unless root."""
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if DATA_DIR.stat().st_uid != uid:
            os.chown(DATA_DIR, uid, uid)
            for p in DATA_DIR.rglob("*"):
                os.chown(p, uid, uid, follow_symlinks=False)
        os.setgroups([])
        os.setgid(uid)
        os.setuid(uid)
    except OSError as e:
        # Some hosts restrict what a container may do. Keeping the site up matters more than the extra layer.
        print(f"WARNING: could not switch to user {uid} ({e}); continuing as the current user.", flush=True)


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
SESSION_COOKIE = "rr_session"
SESSION_TTL = 30 * 24 * 3600
MAX_JSON = 64 * 1024
MAX_UPLOAD = 3 * 1024 * 1024
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
PAYMENTS = ("cash_only", "qr", "card")
CATEGORIES = ("khao_man_gai", "kra_pao", "noodles", "other")  # ข้าวมันไก่, ข้าวกระเพรา, ก๋วยเตี๋ยว, อื่นๆ
FAMOUS_LIKES = 25  # likes received (on all their reviews) before a reviewer earns the "famous" badge
SEED_VERSION = 2
CATEGORY_KEYWORDS = (  # checked in order; keep in sync with guessCategory() in public/js/app.js
    ("khao_man_gai", ("khao man gai", "kao man gai", "man gai", "hainan", "ข้าวมันไก่")),
    ("kra_pao", ("kra pao", "kaprao", "krapow", "ka prao", "กะเพรา", "กระเพรา")),
    ("noodles", ("noodle", "kuay teow", "guay teow", "kway teow", "ba mee", "bamee", "pad thai", "khao soi", "pad see ew",
                 "rad na", "wonton", "ก๋วยเตี๋ยว", "บะหมี่", "เส้น", "ผัดไทย", "ข้าวซอย", "ราดหน้า")),
)
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
PHOTO_RE = re.compile(r"^[0-9a-f]{24}\.(jpg|png|webp)$")
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data: blob:; "
        "style-src 'self' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; script-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
        "form-action 'self'"
    ),
}

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
}

PHOTO_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}

# The table definitions live in db.py (they differ a little between SQLite and Postgres).
REVIEW_SELECT = """
SELECT r.*, u.username, u.display_name, u.is_featured,
       (SELECT COUNT(*) FROM likes l2 JOIN reviews r2 ON r2.id = l2.review_id WHERE r2.user_id = u.id) AS author_likes,
       (SELECT COUNT(*) FROM likes l WHERE l.review_id = r.id) AS like_count,
       EXISTS(SELECT 1 FROM likes l WHERE l.review_id = r.id AND l.user_id = ?) AS liked
FROM reviews r JOIN users u ON u.id = r.user_id
"""

SORTS = {
    "new": "r.created_at DESC, r.id DESC",
    "top": "r.rating DESC, like_count DESC, r.id DESC",
    "cheap": "r.price_thb ASC, r.id DESC",
    "liked": "like_count DESC, r.id DESC",
}


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
class ApiError(Exception):
    def __init__(self, status: int, code: str, fields: dict | None = None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.fields = fields or {}


def connect() -> db.Connection:
    return db.connect()


def guess_category(text: str) -> str:
    s = (text or "").lower()
    for cat, words in CATEGORY_KEYWORDS:
        if any(w in s for w in words):
            return cat
    return "other"


def init_db() -> None:
    """Create the tables (and upgrade an older SQLite file). Safe to run on every start."""
    if db.engine() == "sqlite":
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = connect()
    try:
        if con.engine == "sqlite":
            con.execute("PRAGMA journal_mode = WAL")
        con.executescript(db.schema_sql(con.engine))
        if con.engine == "sqlite":
            # upgrade a file created before categories existed
            cols = {r["name"] for r in con.execute("PRAGMA table_info(reviews)")}
            if "category" not in cols:
                con.execute("ALTER TABLE reviews ADD COLUMN category TEXT NOT NULL DEFAULT 'other'")
                for r in con.execute("SELECT id, menu_item FROM reviews").fetchall():
                    con.execute("UPDATE reviews SET category = ? WHERE id = ?", (guess_category(r["menu_item"]), r["id"]))
        con.commit()
    finally:
        con.close()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, dk_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=2 ** 14, r=8, p=1, dklen=32
        )
        return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, TypeError):
        return False


DUMMY_HASH = hash_password(secrets.token_hex(8))  # equalises timing for unknown users

def rate_ok(con, key: str, limit: int, window: int) -> bool:
    """Record one attempt for `key`; False once `limit` attempts happened within `window` seconds.
    The attempts live in the database, so every server instance (serverless ones too) shares them."""
    now = int(time.time())
    seen = con.execute("SELECT COUNT(*) FROM rate_hits WHERE rkey = ? AND ts > ?", (key, now - window)).fetchone()[0]
    if seen >= limit:
        return False
    con.execute("INSERT INTO rate_hits(rkey, ts) VALUES(?, ?)", (key, now))
    if random.random() < 0.02:  # now and then, forget attempts older than a day
        con.execute("DELETE FROM rate_hits WHERE ts < ?", (now - 86400,))
    con.commit()  # keep the attempt even when the request then fails: a wrong password is what we count
    return True


def rate_keys() -> set:
    """Which keys have recorded attempts (used by the tests)."""
    con = connect()
    try:
        return {r[0] for r in con.execute("SELECT DISTINCT rkey FROM rate_hits")}
    finally:
        con.close()


def reset_rate_limits() -> None:
    con = connect()
    try:
        con.execute("DELETE FROM rate_hits")
        con.commit()
    finally:
        con.close()


def clean_line(value) -> str:
    if value is None:
        return ""
    return " ".join(CTRL_RE.sub("", str(value)).split())


def clean_text(value) -> str:
    s = CTRL_RE.sub("", str(value or "")).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def as_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"\d{1,6}", value.strip()):
        return int(value.strip())
    return None


def sniff_image(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def payment_columns(payments) -> dict:
    return {
        "cash_only": int("cash_only" in payments),
        "pay_qr": int("qr" in payments),
        "pay_card": int("card" in payments),
    }


def delete_photo_if_unused(con, name: str | None) -> None:
    """Photos are rows in the database. Drop one once no review uses it any more."""
    if name and PHOTO_RE.match(name):
        con.execute("DELETE FROM photos WHERE name = ? AND NOT EXISTS (SELECT 1 FROM reviews WHERE photo = ?)", (name, name))


def cleanup_orphan_photos(con) -> None:
    """Uploaded but never attached to a review (the poster gave up): forget them after a day."""
    con.execute(
        "DELETE FROM photos WHERE created_at < ? AND name NOT IN (SELECT photo FROM reviews WHERE photo IS NOT NULL)",
        (int(time.time()) - 24 * 3600,),
    )
    con.commit()


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def validate_review(d: dict, con) -> dict:
    errors: dict[str, str] = {}
    out: dict = {}

    def text(field: str, lo: int, hi: int, required: bool = True):
        v = clean_line(d.get(field))
        if not v:
            if required:
                errors[field] = "required"
        elif len(v) < lo:
            errors[field] = "too_short"
        elif len(v) > hi:
            errors[field] = "too_long"
        out[field] = v

    text("restaurant_name", 2, 80)
    text("area", 0, 80, required=False)
    text("menu_item", 2, 80)

    category = d.get("category")
    if category in (None, ""):
        category = guess_category(out["menu_item"])  # API clients may omit it
    elif not isinstance(category, str) or category not in CATEGORIES:
        errors["category"] = "invalid"
        category = "other"
    out["category"] = category

    rating = as_int(d.get("rating"))
    if rating is None and d.get("rating") in (None, ""):
        errors["rating"] = "required"
    elif rating is None or not 1 <= rating <= 5:
        errors["rating"] = "invalid"
    out["rating"] = rating

    price = as_int(d.get("price_thb"))
    if price is None and d.get("price_thb") in (None, ""):
        errors["price_thb"] = "required"
    elif price is None or not 1 <= price <= 10000:
        errors["price_thb"] = "invalid"
    out["price_thb"] = price

    spice = as_int(d.get("spice", 0) if d.get("spice") is not None else 0)
    if spice is None or not 0 <= spice <= 3:
        errors["spice"] = "invalid"
        spice = 0
    out["spice"] = spice

    payments = d.get("payments")
    if not isinstance(payments, list) or not payments:
        errors["payments"] = "pick_one"
        payments = []
    elif not all(isinstance(p, str) and p in PAYMENTS for p in payments):
        errors["payments"] = "invalid"
        payments = []
    elif "cash_only" in payments and len(set(payments)) > 1:
        errors["payments"] = "cash_only_exclusive"
        payments = []
    out.update(payment_columns(payments))

    days = d.get("open_days")
    if not isinstance(days, list) or not days:
        errors["open_days"] = "pick_one"
        days = []
    elif not all(isinstance(x, str) and x in DAYS for x in days):
        errors["open_days"] = "invalid"
        days = []
    out["open_days"] = ",".join(x for x in DAYS if x in days)

    for field in ("open_time", "close_time"):
        v = d.get(field)
        if not isinstance(v, str) or not TIME_RE.match(v):
            errors[field] = "required" if v in (None, "") else "invalid"
        out[field] = v
    if "open_time" not in errors and "close_time" not in errors and out["open_time"] == out["close_time"]:
        errors["close_time"] = "same_time"

    body = clean_text(d.get("body"))
    if len(body) > 1000:
        errors["body"] = "too_long"
    out["body"] = body

    photo = d.get("photo")
    if photo in (None, ""):
        out["photo"] = None
    elif (isinstance(photo, str) and PHOTO_RE.match(photo)
          and con.execute("SELECT 1 FROM photos WHERE name = ?", (photo,)).fetchone()):
        out["photo"] = photo
    else:
        errors["photo"] = "invalid"
        out["photo"] = None

    if errors:
        raise ApiError(400, "validation", errors)
    return out


# --------------------------------------------------------------------------
# JSON shaping
# --------------------------------------------------------------------------
def me_json(u) -> dict | None:
    if not u:
        return None
    return {"username": u["username"], "display_name": u["display_name"], "is_featured": bool(u["is_featured"])}


def review_json(r) -> dict:
    payments = [name for name, col in (("cash_only", "cash_only"), ("qr", "pay_qr"), ("card", "pay_card")) if r[col]]
    return {
        "id": r["id"],
        "restaurant_name": r["restaurant_name"],
        "area": r["area"],
        "menu_item": r["menu_item"],
        "category": r["category"],
        "rating": r["rating"],
        "price_thb": r["price_thb"],
        "payments": payments,
        "open_days": r["open_days"].split(","),
        "open_time": r["open_time"],
        "close_time": r["close_time"],
        "spice": r["spice"],
        "body": r["body"],
        "photo": f"/uploads/{r['photo']}" if r["photo"] else None,
        "is_sample": bool(r["is_sample"]),
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
        "likes": r["like_count"],
        "liked": bool(r["liked"]),
        "author": {
            "username": r["username"],
            "display_name": r["display_name"],
            "is_featured": bool(r["is_featured"]),
            "is_famous": r["author_likes"] >= FAMOUS_LIKES,
        },
    }


def user_json(u, review_count: int, avg, likes_received: int) -> dict:
    return {
        "username": u["username"],
        "display_name": u["display_name"],
        "bio": u["bio"],
        "is_featured": bool(u["is_featured"]),
        "is_famous": likes_received >= FAMOUS_LIKES,
        "famous_at": FAMOUS_LIKES,
        "created_at": u["created_at"],
        "review_count": review_count,
        "avg_rating": round(float(avg), 1) if avg is not None else None,  # Postgres AVG() is a Decimal
        "likes_received": likes_received,
    }


def like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def qstr(q: dict, key: str, default: str = "") -> str:
    vals = q.get(key)
    return vals[0] if vals else default


def qint(q: dict, key: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(qstr(q, key))
    except ValueError:
        return default
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
ROUTES: list = []


def route(method: str, pattern: str):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn

    return deco


def start_session(h: "Handler", user_id: int) -> None:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    h.con.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    h.con.execute(
        "INSERT INTO sessions(token_hash, user_id, expires_at) VALUES(?,?,?)",
        (hashlib.sha256(token.encode()).hexdigest(), user_id, now + SESSION_TTL),
    )
    h.cookies.append(f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}{h.secure_flag()}")


@route("GET", "/api/me")
def api_me(h, q):
    return {"user": me_json(h.current_user())}


@route("POST", "/api/signup")
def api_signup(h, q):
    if not rate_ok(h.con, f"signup:{h.client_ip()}", 20, 3600):
        raise ApiError(429, "rate_limited")
    d = h.read_json()
    errors = {}
    username = str(d.get("username") or "").strip()
    if not USERNAME_RE.match(username):
        errors["username"] = "invalid_username"
    display = clean_line(d.get("display_name")) or username
    if len(display) > 40:
        errors["display_name"] = "too_long"
    password = d.get("password")
    if not isinstance(password, str) or len(password) < 8:
        errors["password"] = "password_short"
    elif len(password) > 128:
        errors["password"] = "too_long"
    if errors:
        raise ApiError(400, "validation", errors)
    if h.con.execute("SELECT 1 FROM users WHERE lower(username) = lower(?)", (username,)).fetchone():
        raise ApiError(409, "validation", {"username": "username_taken"})
    try:
        uid = h.con.execute(
            "INSERT INTO users(username, display_name, pw_hash, created_at) VALUES(?,?,?,?) RETURNING id",
            (username, display, hash_password(password), int(time.time())),
        ).fetchone()[0]
    except db.integrity_errors:
        raise ApiError(409, "validation", {"username": "username_taken"})
    start_session(h, uid)
    user = h.con.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return 201, {"user": me_json(user)}


@route("POST", "/api/login")
def api_login(h, q):
    d = h.read_json()
    username = str(d.get("username") or "").strip()
    password = d.get("password")
    key = f"login:{h.client_ip()}:{username.lower()}"
    if not rate_ok(h.con, key, 8, 600):
        raise ApiError(429, "rate_limited")
    row = h.con.execute("SELECT * FROM users WHERE lower(username) = lower(?)", (username,)).fetchone()
    stored = row["pw_hash"] if row and row["pw_hash"] else DUMMY_HASH
    good = isinstance(password, str) and verify_password(password, stored)
    if not (good and row and row["pw_hash"]):
        raise ApiError(401, "bad_credentials")
    h.con.execute("DELETE FROM rate_hits WHERE rkey = ?", (key,))  # a correct login clears the failed attempts
    start_session(h, row["id"])
    return {"user": me_json(row)}


@route("POST", "/api/logout")
def api_logout(h, q):
    token = h.session_token()
    if token:
        h.con.execute("DELETE FROM sessions WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))
    h.cookies.append(f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0{h.secure_flag()}")
    return {"ok": True}


@route("GET", "/api/reviews")
def api_reviews(h, q):
    me = h.current_user()
    me_id = me["id"] if me else -1
    where, args = [], []

    text = qstr(q, "q").strip()[:80]
    if text:
        like = "%" + like_escape(text) + "%"
        where.append(
            "(lower(r.restaurant_name) LIKE lower(?) ESCAPE '\\' OR lower(r.menu_item) LIKE lower(?) ESCAPE '\\' "
            "OR lower(r.area) LIKE lower(?) ESCAPE '\\' OR lower(r.body) LIKE lower(?) ESCAPE '\\')"
        )
        args += [like] * 4
    pay = qstr(q, "payment")
    if pay == "cash_only":
        where.append("r.cash_only = 1")
    elif pay == "qr":
        where.append("r.pay_qr = 1")
    elif pay == "card":
        where.append("r.pay_card = 1")
    category = qstr(q, "category")
    if category in CATEGORIES:
        where.append("r.category = ?")
        args.append(category)
    min_rating = qint(q, "min_rating", 0, 0, 5)
    if min_rating:
        where.append("r.rating >= ?")
        args.append(min_rating)
    user = qstr(q, "user").strip()
    if user:
        where.append("lower(u.username) = lower(?)")
        args.append(user)
    if qstr(q, "featured") == "1":
        where.append("u.is_featured = 1")

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    order = SORTS.get(qstr(q, "sort"), SORTS["new"])
    limit = qint(q, "limit", 12, 1, 50)
    offset = qint(q, "offset", 0, 0, 100000)

    total = h.con.execute(
        "SELECT COUNT(*) FROM reviews r JOIN users u ON u.id = r.user_id" + clause, args
    ).fetchone()[0]
    rows = h.con.execute(
        REVIEW_SELECT + clause + f" ORDER BY {order} LIMIT ? OFFSET ?", [me_id, *args, limit, offset]
    ).fetchall()
    return {"reviews": [review_json(r) for r in rows], "total": total}


def fetch_review(h, review_id: int):
    me = h.current_user()
    return h.con.execute(REVIEW_SELECT + " WHERE r.id = ?", (me["id"] if me else -1, review_id)).fetchone()


@route("GET", r"/api/reviews/(\d+)")
def api_review_get(h, q, rid):
    row = fetch_review(h, int(rid))
    if not row:
        raise ApiError(404, "not_found")
    return {"review": review_json(row)}


REVIEW_COLUMNS = (
    "restaurant_name", "area", "menu_item", "category", "rating", "price_thb", "cash_only", "pay_qr", "pay_card",
    "open_days", "open_time", "close_time", "spice", "body", "photo",
)


@route("POST", "/api/reviews")
def api_review_create(h, q):
    me = h.require_user()
    if not rate_ok(h.con, f"review:{me['id']}", 30, 3600):
        raise ApiError(429, "rate_limited")
    data = validate_review(h.read_json(), h.con)
    now = int(time.time())
    cols = ("user_id", *REVIEW_COLUMNS, "created_at", "updated_at")
    rid = h.con.execute(
        f"INSERT INTO reviews({', '.join(cols)}) VALUES({', '.join('?' * len(cols))}) RETURNING id",
        [me["id"], *(data[c] for c in REVIEW_COLUMNS), now, now],
    ).fetchone()[0]
    return 201, {"review": review_json(fetch_review(h, rid))}


def owned_review(h, rid: int):
    me = h.require_user()
    row = h.con.execute("SELECT id, user_id, photo FROM reviews WHERE id = ?", (rid,)).fetchone()
    if not row:
        raise ApiError(404, "not_found")
    if row["user_id"] != me["id"]:
        raise ApiError(403, "forbidden")
    return row


@route("PUT", r"/api/reviews/(\d+)")
def api_review_update(h, q, rid):
    row = owned_review(h, int(rid))
    data = validate_review(h.read_json(), h.con)
    sets = ", ".join(f"{c} = ?" for c in REVIEW_COLUMNS)
    h.con.execute(
        f"UPDATE reviews SET {sets}, updated_at = ? WHERE id = ?",
        [*(data[c] for c in REVIEW_COLUMNS), int(time.time()), row["id"]],
    )
    if row["photo"] and row["photo"] != data["photo"]:
        delete_photo_if_unused(h.con, row["photo"])
    return {"review": review_json(fetch_review(h, row["id"]))}


@route("DELETE", r"/api/reviews/(\d+)")
def api_review_delete(h, q, rid):
    row = owned_review(h, int(rid))
    h.con.execute("DELETE FROM reviews WHERE id = ?", (row["id"],))
    delete_photo_if_unused(h.con, row["photo"])
    return {"ok": True}


@route("POST", r"/api/reviews/(\d+)/like")
def api_like(h, q, rid):
    me = h.require_user()
    row = h.con.execute("SELECT user_id FROM reviews WHERE id = ?", (int(rid),)).fetchone()
    if not row:
        raise ApiError(404, "not_found")
    if row["user_id"] == me["id"]:
        raise ApiError(403, "own_review")  # fame has to come from other people
    cur = h.con.execute("DELETE FROM likes WHERE user_id = ? AND review_id = ?", (me["id"], int(rid)))
    liked = cur.rowcount == 0
    if liked:
        h.con.execute(
            "INSERT INTO likes(user_id, review_id) VALUES(?,?) ON CONFLICT DO NOTHING", (me["id"], int(rid)))
    count = h.con.execute("SELECT COUNT(*) FROM likes WHERE review_id = ?", (int(rid),)).fetchone()[0]
    return {"liked": liked, "likes": count}


@route("GET", r"/api/users/([A-Za-z0-9_]{3,20})")
def api_user(h, q, username):
    u = h.con.execute("SELECT * FROM users WHERE lower(username) = lower(?)", (username,)).fetchone()
    if not u:
        raise ApiError(404, "not_found")
    stats = h.con.execute("SELECT COUNT(*) AS n, AVG(rating) AS avg FROM reviews WHERE user_id = ?", (u["id"],)).fetchone()
    got = h.con.execute(
        "SELECT COUNT(*) FROM likes l JOIN reviews r ON r.id = l.review_id WHERE r.user_id = ?", (u["id"],)
    ).fetchone()[0]
    return {"user": user_json(u, stats["n"], stats["avg"], got)}


@route("GET", "/api/users")
def api_famous_users(h, q):
    """Reviewers who earned the 'famous' badge from other people's likes (the featured blogger has her own spotlight)."""
    rows = h.con.execute(
        """SELECT * FROM (
               SELECT u.*, COUNT(r.id) AS n, AVG(r.rating) AS avg,
                      (SELECT COUNT(*) FROM likes l JOIN reviews r2 ON r2.id = l.review_id WHERE r2.user_id = u.id) AS got
               FROM users u JOIN reviews r ON r.user_id = u.id
               WHERE u.is_featured = 0
               GROUP BY u.id
           ) t
           WHERE got >= ?
           ORDER BY got DESC, n DESC
           LIMIT ?""",
        (FAMOUS_LIKES, qint(q, "limit", 6, 1, 20)),
    ).fetchall()
    return {"users": [user_json(u, u["n"], u["avg"], u["got"]) for u in rows]}


@route("GET", "/api/restaurants")
def api_restaurants(h, q):
    """Latest known details per restaurant, used to auto-complete the review form."""
    rows = h.con.execute(
        """SELECT r.* FROM reviews r
           WHERE r.id = (SELECT r2.id FROM reviews r2
                         WHERE lower(r2.restaurant_name) = lower(r.restaurant_name)
                         ORDER BY r2.created_at DESC, r2.id DESC LIMIT 1)
           ORDER BY r.created_at DESC LIMIT 200"""
    ).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "name": r["restaurant_name"],
                "area": r["area"],
                "payments": [n for n, c in (("cash_only", "cash_only"), ("qr", "pay_qr"), ("card", "pay_card")) if r[c]],
                "open_days": r["open_days"].split(","),
                "open_time": r["open_time"],
                "close_time": r["close_time"],
            }
        )
    return {"restaurants": out}


@route("POST", "/api/upload")
def api_upload(h, q):
    me = h.require_user()
    if not rate_ok(h.con, f"upload:{me['id']}", 60, 3600):
        raise ApiError(429, "rate_limited")
    data = h.read_body(MAX_UPLOAD)
    ext = sniff_image(data)
    if not ext:
        raise ApiError(415, "bad_image")
    name = f"{secrets.token_hex(12)}.{ext}"
    h.con.execute(
        "INSERT INTO photos(name, content_type, data, created_at) VALUES(?,?,?,?)",
        (name, PHOTO_TYPES[ext], data, int(time.time())),
    )
    return 201, {"photo": name, "url": f"/uploads/{name}"}


# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "RubRub/1.0"
    sys_version = ""
    timeout = 20

    # -- request plumbing --------------------------------------------------
    def do_GET(self):
        self.handle_request()

    do_HEAD = do_GET
    do_POST = do_GET
    do_PUT = do_GET
    do_DELETE = do_GET

    def handle_request(self):
        """Built-in server: work out the answer, then write it to the socket."""
        self.process()
        try:
            self.flush_response()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass  # the visitor went away

    def process(self):
        """Work out the response and keep it in self.response. Nothing here touches a socket, so the
        same code serves the built-in server and WSGI hosts (Vercel)."""
        self.con = None
        self.cookies: list[str] = []
        self.response = None
        self._user = False
        self._body_read = False
        try:
            url = urlsplit(self.path)
            path = unquote(url.path)
            if path == "/healthz":
                self.health()
            elif path.startswith("/api/"):
                self.con = connect()
                try:
                    result = self.dispatch_api(path, parse_qs(url.query))
                    self.con.commit()
                finally:
                    self.con.close()
                status, payload = result if isinstance(result, tuple) else (200, result)
                self.send_json(payload, status)
            elif path.startswith("/uploads/"):
                self.serve_photo(path[len("/uploads/"):])
            else:
                self.serve_static(path)
        except ApiError as e:
            self.send_json({"error": e.code, "fields": e.fields}, e.status)
        except (db.DatabaseNotConfigured, db.DatabaseUnavailable) as e:
            print(f"ERROR: {e}", flush=True)  # the full reason goes to the log, only a code goes to the visitor
            self.send_json({"error": e.code}, 503)
        except Exception:
            traceback.print_exc()
            self.send_json({"error": "server_error"}, 500)
        finally:
            self.drain_body()

    def flush_response(self):
        if self.response is None:
            return
        status, headers, body = self.response
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def dispatch_api(self, path: str, query: dict):
        self.check_csrf()
        path_matched = False
        for method, rx, fn in ROUTES:
            m = rx.match(path)
            if not m:
                continue
            path_matched = True
            if method == self.command or (method == "GET" and self.command == "HEAD"):
                return fn(self, query, *m.groups())
        raise ApiError(405, "method_not_allowed") if path_matched else ApiError(404, "not_found")

    def health(self):
        """Health check for hosting platforms: proves the process is up and the database opens."""
        if self.command not in ("GET", "HEAD"):
            raise ApiError(405, "method_not_allowed")
        con = connect()
        try:
            con.execute("SELECT 1").fetchone()
        finally:
            con.close()
        self.send_json({"ok": True})

    # -- proxy awareness ------------------------------------------------------
    def client_ip(self) -> str:
        """The visitor's address. Behind a trusted proxy that is read from X-Forwarded-For, counting
        from the right (the proxy's own entries are the ones that can be trusted, the left side can
        be forged by the visitor)."""
        if TRUST_PROXY:
            parts = [p.strip() for p in (self.headers.get("X-Forwarded-For") or "").split(",") if p.strip()]
            if parts:
                return parts[max(0, len(parts) - PROXY_HOPS)][:64]
        return self.client_address[0]

    def is_https(self) -> bool:
        if FORCE_SECURE:
            return True
        if TRUST_PROXY:
            proto = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
            return proto == "https"
        return False

    def secure_flag(self) -> str:
        return "; Secure" if self.is_https() else ""

    def check_csrf(self):
        if self.command in ("GET", "HEAD"):
            return
        if self.headers.get("X-Requested-With") != "rubrub":
            raise ApiError(403, "csrf")
        origin = self.headers.get("Origin")
        if origin:
            allowed = {self.headers.get("Host")}
            if TRUST_PROXY:  # some proxies rewrite Host and pass the public name on instead
                allowed.add((self.headers.get("X-Forwarded-Host") or "").split(",")[0].strip() or None)
            if urlsplit(origin).netloc not in allowed:
                raise ApiError(403, "csrf")

    def read_body(self, limit: int) -> bytes:
        self._body_read = True
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "bad_request")
        if length < 0:
            raise ApiError(400, "bad_request")
        if length > limit:
            raise ApiError(413, "too_large")
        return self.rfile.read(length) if length else b""

    def read_json(self) -> dict:
        raw = self.read_body(MAX_JSON)
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise ApiError(415, "bad_request")
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "bad_request")
        if not isinstance(data, dict):
            raise ApiError(400, "bad_request")
        return data

    def drain_body(self):
        """Read an unread request body so closing the socket never resets the response."""
        if self._body_read:
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if 0 < n <= 1_000_000:
                self.rfile.read(n)
        except Exception:
            pass

    # -- auth ----------------------------------------------------------------
    def session_token(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:
            return None
        morsel = jar.get(SESSION_COOKIE)
        return morsel.value if morsel and morsel.value else None

    def current_user(self):
        if self._user is not False:
            return self._user
        user = None
        token = self.session_token()
        if token and self.con is not None:
            user = self.con.execute(
                "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = ? AND s.expires_at > ?",
                (hashlib.sha256(token.encode()).hexdigest(), int(time.time())),
            ).fetchone()
        self._user = user
        return user

    def require_user(self):
        user = self.current_user()
        if not user:
            raise ApiError(401, "unauthorized")
        return user

    # -- responses -----------------------------------------------------------
    def send_bytes(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
        headers = [("Content-Type", ctype), ("Content-Length", str(len(body)))]
        headers += list(SECURITY_HEADERS.items())
        if self.is_https():
            headers.append(("Strict-Transport-Security", "max-age=31536000"))
        headers += list((extra or {}).items())
        headers += [("Set-Cookie", c) for c in self.cookies]
        self.response = (status, headers, b"" if self.command == "HEAD" else body)

    def send_json(self, payload, status: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_bytes(status, body, "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def serve_static(self, path: str):
        if self.command not in ("GET", "HEAD"):
            raise ApiError(405, "method_not_allowed")
        if "\\" in path or ":" in path or "\x00" in path:
            raise ApiError(404, "not_found")
        file = (STATIC_DIR / (path.lstrip("/") or "index.html")).resolve()
        if not file.is_relative_to(STATIC_DIR):
            raise ApiError(404, "not_found")
        if file.is_dir():
            file = file / "index.html"
        if not file.is_file():
            raise ApiError(404, "not_found")
        ctype = CONTENT_TYPES.get(file.suffix.lower(), "application/octet-stream")
        self.send_bytes(200, file.read_bytes(), ctype, {"Cache-Control": "no-cache"})

    def serve_photo(self, name: str):
        """Review photos are rows in the database (serverless hosts have no disk to keep files on)."""
        if self.command not in ("GET", "HEAD"):
            raise ApiError(405, "method_not_allowed")
        if not PHOTO_RE.match(name):
            raise ApiError(404, "not_found")
        con = connect()
        try:
            row = con.execute("SELECT content_type, data FROM photos WHERE name = ?", (name,)).fetchone()
        finally:
            con.close()
        if not row:
            raise ApiError(404, "not_found")
        # the name is random and never reused, so the browser and the host's CDN may keep it forever
        self.send_bytes(200, bytes(row["data"]), row["content_type"], {"Cache-Control": "public, max-age=31536000, immutable"})


class RubServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64
    # On Windows SO_REUSEADDR lets a second process steal the port; keep it off there.
    allow_reuse_address = sys.platform != "win32"


def make_server(host: str, port: int) -> RubServer:
    return RubServer((host, port), Handler)


# --------------------------------------------------------------------------
# WSGI entry point: Vercel and other serverless hosts call this once per request
# --------------------------------------------------------------------------
API_PREFIXES = ("/api/", "/uploads/", "/healthz")


def request_target(environ) -> tuple[str, str]:
    """(path, query string) of the original request.

    vercel.json rewrites /api/*, /uploads/* and /healthz to the one function and passes the real path
    along as ?rr_path=..., so routing works whether or not the platform keeps the original path."""
    path = environ.get("PATH_INFO") or "/"
    query = environ.get("QUERY_STRING") or ""
    params = parse_qs(query, keep_blank_values=True)
    real = (params.pop("rr_path", [""])[0] or "")
    if real.startswith(API_PREFIXES):
        path, query = real, urlencode(params, doseq=True)
    return quote(path, safe="/"), query  # Handler.process() unquotes it again, as it does for a socket request


class WsgiRequest(Handler):
    """The request logic of Handler, fed from a WSGI environ instead of a socket."""

    def __init__(self, environ):  # on purpose not BaseHTTPRequestHandler.__init__: there is no socket
        self.command = (environ.get("REQUEST_METHOD") or "GET").upper()
        headers = HTTPMessage()
        for key, value in environ.items():
            if key.startswith("HTTP_"):
                headers[key[5:].replace("_", "-").title()] = str(value)
        for key, name in (("CONTENT_TYPE", "Content-Type"), ("CONTENT_LENGTH", "Content-Length")):
            if environ.get(key):
                headers[name] = str(environ[key])
        self.headers = headers
        path, query = request_target(environ)
        self.path = path + ("?" + query if query else "")
        self.client_address = (environ.get("REMOTE_ADDR") or "0.0.0.0", 0)
        self.rfile = environ.get("wsgi.input") or BytesIO()
        self.cookies = []
        self.response = None


_ready = False
_ready_lock = threading.Lock()


def apply_environment(environ=None) -> None:
    """Settings that hosting platforms provide as environment variables."""
    env = os.environ if environ is None else environ
    on_vercel = bool(env.get("VERCEL"))
    if env.get("RUBRUB_DATA_DIR"):
        configure(env["RUBRUB_DATA_DIR"])
    url = db.postgres_url_from_env(env, any_name=on_vercel)
    if url:
        use_postgres(url)
    elif on_vercel:
        # Vercel has no disk to keep a SQLite file on: without a Postgres database nothing can be saved
        raise db.DatabaseNotConfigured(
            "No Postgres address found in the environment. In the Vercel dashboard open Storage, create a "
            "Neon Postgres database, connect it to this project and REDEPLOY (variables only reach new "
            "deployments). Variables that look related (names only): " + (", ".join(db.related_variable_names(env)) or "none"))
    if on_vercel or "RUBRUB_TRUST_PROXY" in env:  # Vercel always sits in front of the app and sets X-Forwarded-*
        configure_proxy(env_flag("RUBRUB_TRUST_PROXY", on_vercel, env), int(env.get("RUBRUB_PROXY_HOPS") or 1),
                        env_flag("RUBRUB_SECURE_COOKIES", False, env))


def ensure_ready(force: bool = False) -> None:
    """Once per server process (a "cold start" on serverless hosts): choose the database, create the
    tables, add the sample profiles. Concurrent cold starts are safe: seeding skips what already exists."""
    global _ready
    if _ready and not force:
        return
    with _ready_lock:
        if _ready and not force:
            return
        apply_environment()
        init_db()
        con = connect()
        try:
            if env_flag("RUBRUB_SEED", True):
                seed(con)
            cleanup_orphan_photos(con)
        finally:
            con.close()
        _ready = True


def wsgi_app(environ, start_response):
    req = WsgiRequest(environ)
    if req.path.startswith(API_PREFIXES):
        try:
            ensure_ready()
        except (db.DatabaseNotConfigured, db.DatabaseUnavailable) as e:
            print(f"ERROR: {e}", flush=True)
            req.send_json({"error": e.code}, 503)
        except Exception:
            traceback.print_exc()
            req.send_json({"error": "server_error"}, 503)
        else:
            req.process()
    else:
        req.process()  # static files (on Vercel the CDN serves these before the function is reached)
    status, headers, body = req.response or (500, [("Content-Type", "text/plain")], b"server error")
    start_response(f"{status} {HTTPStatus(status).phrase}", headers)
    return [body]


# --------------------------------------------------------------------------
# Seed data - clearly fictional vendors, labelled "sample" in the UI
# --------------------------------------------------------------------------
CLAUDE_BIO = (
    "Street food obsessive. I chase wok smoke, sizzle and the longest queue on the soi, "
    "and I always tell you if it's cash only. The reviews below are demo data: the "
    "vendors in them are made up. ชอบกินเผ็ด!"
)

SAMPLE_REVIEWS = [
    dict(
        restaurant_name="Auntie Noi's Wok Cart", area="Ari, Bangkok", menu_item="Pad Kra Pao Moo Saap + fried egg",
        rating=5, price_thb=60, payments=["cash_only"], open_days=DAYS[:6], open_time="10:30", close_time="15:00",
        spice=3,
        body="Smoky wok hei, a lacy-edged fried egg and holy basil by the fistful. She asks 'phet mai?' and "
             "you say yes. Sweating through your shirt is part of the experience. Get there before 12:00 or queue.",
    ),
    dict(
        restaurant_name="Som Tam Sisters", area="Old City, Chiang Mai", menu_item="Som Tam Thai with salted egg",
        rating=4, price_thb=50, payments=["qr"], open_days=DAYS, open_time="09:00", close_time="20:00", spice=2,
        body="Pounded to order in a clay mortar, sweet-sour-sharp with a proper crunch. Ask for 'phet nit noi' "
             "if you are new here. The salted egg makes it creamy. QR code works, so no cash fuss.",
    ),
    dict(
        restaurant_name="Moo Ping Uncle Lek", area="Phuket Old Town", menu_item="Moo Ping (3 skewers) + sticky rice",
        rating=5, price_thb=40, payments=["cash_only"], open_days=DAYS, open_time="06:00", close_time="10:30",
        spice=0,
        body="Charcoal-grilled pork, honey-glazed and sticky at the edges, with warm sticky rice in a banana "
             "leaf. The best forty baht breakfast I know. He sells out by 10, so don't be late.",
    ),
    dict(
        restaurant_name="Hainan Hero Khao Man Gai", area="Silom, Bangkok", menu_item="Khao Man Gai (steamed chicken rice)",
        rating=4, price_thb=55, payments=["qr"], open_days=DAYS[:6], open_time="07:00", close_time="14:00", spice=1,
        body="Silky chicken, rice cooked in the broth, and a fiery ginger-chili dipping sauce that does all the "
             "talking. Don't skip the little cup of soup. Fast service for a lunch rush.",
    ),
    dict(
        restaurant_name="Boat Noodle Bridge", area="Ayutthaya", menu_item="Kuay Teow Reua (boat noodles) x3 bowls",
        rating=4, price_thb=75, payments=["cash_only"], open_days=DAYS[1:], open_time="09:00", close_time="17:00",
        spice=2,
        body="Dark, rich, slightly sweet broth, tiny bowls and a tower of empties by the end. Add crispy pork "
             "skin on top. Cash only and no English menu: just point and smile.",
    ),
    dict(
        restaurant_name="Smoky Khao Soi Corner", area="Nimman, Chiang Mai", menu_item="Khao Soi Gai",
        rating=5, price_thb=70, payments=["qr", "card"], open_days=DAYS[:6], open_time="10:00", close_time="16:00",
        spice=2,
        body="Creamy coconut curry broth, tender chicken, a nest of crispy noodles on top and pickled mustard "
             "greens on the side. Squeeze the lime, add the chili oil, and thank me later.",
    ),
    dict(
        restaurant_name="Midnight Pad Thai Flame", area="Yaowarat, Bangkok", menu_item="Pad Thai Goong Sod",
        rating=4, price_thb=90, payments=["qr"], open_days=DAYS, open_time="18:00", close_time="02:00", spice=1,
        body="Big flames, big prawns, wrapped in a thin egg net. The stall opens at 18:00 and runs until two "
             "in the morning, which makes it my favourite post-night-market snack. Bring friends.",
    ),
    dict(
        restaurant_name="Mango Mama Sticky Rice", area="Hat Yai", menu_item="Khao Niao Mamuang",
        rating=5, price_thb=80, payments=["qr", "card"], open_days=DAYS, open_time="11:00", close_time="21:00",
        spice=0,
        body="Warm, coconutty sticky rice with perfectly ripe mango. The crispy mung bean sprinkle on top is "
             "the secret. I came for dessert and stayed for seconds. A cooling finish after all that chili.",
    ),
]


TESTER_BIO = (
    "Eats three street meals a day and reviews every one. Rub rub readers keep liking these, "
    "which is how I became a famous reviewer. (Sample profile: the vendors are made up.)"
)

TESTER_REVIEWS = [
    dict(
        restaurant_name="Soi 38 Crispy Kra Pao", area="Thonglor, Bangkok", menu_item="Pad Kra Pao Gai + crispy fried egg",
        category="kra_pao", rating=5, price_thb=55, payments=["cash_only"], open_days=DAYS[:6],
        open_time="07:00", close_time="14:00", spice=3,
        body="Chicken minced by hand, a fistful of holy basil and chilies you can feel in your ears. The egg is "
             "fried until the edges go lacy-crisp. I ate it standing up on the pavement and would again tomorrow.",
    ),
    dict(
        restaurant_name="Uncle Somchai's Chicken Rice", area="Sathorn, Bangkok", menu_item="Khao Man Gai Pak + liver soup",
        category="khao_man_gai", rating=4, price_thb=50, payments=["qr"], open_days=DAYS,
        open_time="06:00", close_time="13:00", spice=1,
        body="Tender chicken, rice that tastes of the broth, and a ginger-chili sauce with real kick. Order the "
             "'pak' (mixed) and ask for the liver soup on the side. Fast queue, QR code accepted.",
    ),
    dict(
        restaurant_name="Wonton Alley Egg Noodles", area="Yaowarat, Bangkok", menu_item="Ba Mee Moo Daeng + crispy wonton",
        category="noodles", rating=4, price_thb=45, payments=["cash_only"], open_days=DAYS[:6],
        open_time="08:00", close_time="16:00", spice=0,
        body="Springy egg noodles, sweet red pork and wontons that shatter when you bite them. Add the pickled "
             "chilies in vinegar if you want some heat. Cash only, and the lady counts change faster than a calculator.",
    ),
    dict(
        restaurant_name="Tom Yum Noodle Lady", area="Khon Kaen", menu_item="Tom Yum Noodles Moo Sap (dry or soup)",
        category="noodles", rating=5, price_thb=70, payments=["qr", "card"], open_days=DAYS[:6],
        open_time="10:00", close_time="20:00", spice=2,
        body="Hot, sour, a little sweet, with crushed peanuts and a squeeze of lime. I got mine as a soup and the "
             "dry version for a friend to compare. Both are winners. This is the one I send visitors to.",
    ),
    dict(
        restaurant_name="Rot Fai Night Wok", area="Night market, Bangkok", menu_item="Pad Kra Pao Talay (seafood)",
        category="kra_pao", rating=4, price_thb=90, payments=["qr"], open_days=DAYS[3:],
        open_time="17:00", close_time="23:30", spice=3,
        body="Squid, prawns and mussels in a roaring-hot basil stir fry, plated on rice with a runny egg. "
             "Only open Thursday to Sunday, so plan ahead. Worth the wait, and the wait is long after 20:00.",
    ),
    dict(
        restaurant_name="Roti Rose Cart", area="Old City, Chiang Mai", menu_item="Roti Gluay (banana roti) with condensed milk",
        category="other", rating=5, price_thb=35, payments=["cash_only"], open_days=DAYS,
        open_time="14:00", close_time="22:00", spice=0,
        body="She stretches the dough paper-thin, fries it in butter with banana and egg, and drizzles condensed "
             "milk on top. The perfect cooling dessert after a spicy night. Thirty-five baht of happiness.",
    ),
]

# Fictional "sample foodies" whose only job is to like the tester's reviews (they can't log in).
DEMO_FANS = [("demo_nok", "Nok"), ("demo_pim", "Pim"), ("demo_ton", "Ton"), ("demo_mali", "Mali"),
             ("demo_beam", "Beam"), ("demo_ploy", "Ploy"), ("demo_arm", "Arm"), ("demo_fah", "Fah"),
             ("demo_nan", "Nan"), ("demo_pete", "Pete"), ("demo_gift", "Gift"), ("demo_mint", "Mint")]
TESTER_LIKES = [12, 10, 9, 8, 7, 6]  # per review, 52 in total, comfortably over FAMOUS_LIKES


def _add_sample_review(con, user_id: int, s: dict, created: int) -> int:
    row = {k: v for k, v in s.items() if k not in ("payments", "open_days")}
    row.setdefault("category", guess_category(s["menu_item"]))
    row.update(payment_columns(s["payments"]))
    row["open_days"] = ",".join(s["open_days"])
    row.update(user_id=user_id, photo=None, is_sample=1, created_at=created, updated_at=created)
    cols = list(row)
    return con.execute(
        f"INSERT INTO reviews({', '.join(cols)}) VALUES({', '.join('?' * len(cols))}) RETURNING id", [row[c] for c in cols]
    ).fetchone()[0]


def seed_version(con) -> int:
    value = db.get_meta(con, "seed_version")
    if value is not None:
        return int(value)
    if con.engine == "sqlite":  # a file from before the meta table existed kept the number in its header
        return con.execute("PRAGMA user_version").fetchone()[0]
    return 0


def seed(con) -> bool:
    """Add the demo profiles. Each demo user is created only if missing, and only until the
    seed version is recorded, so deleting a demo review never brings it back."""
    if seed_version(con) >= SEED_VERSION:
        return False

    def exists(name: str) -> bool:
        return con.execute("SELECT 1 FROM users WHERE lower(username) = lower(?)", (name,)).fetchone() is not None

    now = int(time.time())
    added = False
    try:
        if not exists("claude"):
            uid = con.execute(
                "INSERT INTO users(username, display_name, pw_hash, bio, is_featured, created_at) VALUES(?,?,NULL,?,1,?) RETURNING id",
                ("claude", "Claude", CLAUDE_BIO, now),
            ).fetchone()[0]
            for i, s in enumerate(SAMPLE_REVIEWS):
                _add_sample_review(con, uid, s, now - i * 7200)
            con.commit()
            added = True
        if not exists("test_blogger"):
            tester = con.execute(
                "INSERT INTO users(username, display_name, pw_hash, bio, is_featured, created_at) VALUES(?,?,NULL,?,0,?) RETURNING id",
                ("test_blogger", "Test Eater", TESTER_BIO, now - 30 * 86400),
            ).fetchone()[0]
            names = [u for u, _ in DEMO_FANS]
            marks = ",".join("?" * len(names))
            taken = {r[0] for r in con.execute(f"SELECT lower(username) FROM users WHERE lower(username) IN ({marks})", names)}
            new_fans = [(u, n) for u, n in DEMO_FANS if u not in taken]
            if new_fans:
                con.execute(
                    "INSERT INTO users(username, display_name, pw_hash, bio, created_at) VALUES "
                    + ",".join(["(?,?,NULL,?,?)"] * len(new_fans)),
                    [x for u, n in new_fans for x in (u, n, "Sample foodie account used for demo likes.", now - 20 * 86400)],
                )
            fans = [r[0] for r in con.execute(f"SELECT id FROM users WHERE lower(username) IN ({marks}) ORDER BY id", names)]
            likes = []
            for i, s in enumerate(TESTER_REVIEWS):
                rid = _add_sample_review(con, tester, s, now - i * 5400 - 1800)
                likes += [(fans[(i * 3 + k) % len(fans)], rid) for k in range(min(TESTER_LIKES[i], len(fans)))]
            if likes:  # one statement for all the likes: fewer round trips to a database across the internet
                con.execute(
                    "INSERT INTO likes(user_id, review_id) VALUES " + ",".join(["(?,?)"] * len(likes)) + " ON CONFLICT DO NOTHING",
                    [x for pair in likes for x in pair],
                )
            con.commit()
            added = True
    except db.integrity_errors:
        con.rollback()  # another server process is seeding at this very moment, which is fine
    db.set_meta(con, "seed_version", SEED_VERSION)
    con.commit()
    return added


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def reset_password(username: str) -> int:
    con = connect()
    try:
        row = con.execute("SELECT id, username FROM users WHERE lower(username) = lower(?)", (username,)).fetchone()
        if not row:
            print(f"No such user: {username}", file=sys.stderr)
            return 1
        pw = getpass.getpass(f"New password for {row['username']} (min 8 chars): ")
        if len(pw) < 8 or len(pw) > 128:
            print("Password must be 8-128 characters.", file=sys.stderr)
            return 1
        if getpass.getpass("Repeat password: ") != pw:
            print("Passwords do not match.", file=sys.stderr)
            return 1
        con.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (hash_password(pw), row["id"]))
        con.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
        con.commit()
        print(f"Password updated for {row['username']}.")
        return 0
    finally:
        con.close()


def parse_args(argv=None, environ=None):
    """Command-line options. Each one can also come from an environment variable (what hosting
    platforms use); an explicit flag always wins."""
    env = os.environ if environ is None else environ
    on_platform = bool(env.get("PORT"))  # Render, Railway, Fly.io, Heroku... all hand us a PORT
    ap = argparse.ArgumentParser(description="Rub rub - Thai street food reviews")
    ap.add_argument("--host", default=env.get("RUBRUB_HOST") or ("0.0.0.0" if on_platform else "127.0.0.1"),
                    help="interface to bind (default 127.0.0.1, or 0.0.0.0 when PORT is set) [RUBRUB_HOST]")
    ap.add_argument("--port", type=int, default=env.get("PORT") or "8000", help="port to listen on [PORT]")
    ap.add_argument("--data-dir", default=env.get("RUBRUB_DATA_DIR") or None,
                    help="where to keep the SQLite database file (default ./data) [RUBRUB_DATA_DIR]")
    ap.add_argument("--database-url", default=db.postgres_url_from_env(env),
                    help="use a Postgres database instead of the SQLite file [DATABASE_URL]")
    ap.add_argument("--no-seed", action="store_true", default=not env_flag("RUBRUB_SEED", True, env),
                    help="don't create the sample Claude blogger + reviews [RUBRUB_SEED=0]")
    ap.add_argument("--trust-proxy", action="store_true", default=env_flag("RUBRUB_TRUST_PROXY", False, env),
                    help="behind a reverse proxy: read the visitor IP and HTTPS from X-Forwarded-* [RUBRUB_TRUST_PROXY=1]")
    ap.add_argument("--proxy-hops", type=int, default=env.get("RUBRUB_PROXY_HOPS") or "1",
                    help="number of trusted proxies in front of the app (default 1) [RUBRUB_PROXY_HOPS]")
    ap.add_argument("--secure-cookies", action="store_true", default=env_flag("RUBRUB_SECURE_COOKIES", False, env),
                    help="always mark the login cookie Secure (HTTPS only) [RUBRUB_SECURE_COOKIES=1]")
    ap.add_argument("--run-as-uid", type=int, default=env.get("RUBRUB_RUN_AS_UID") or None,
                    help="when started as root, fix data-folder ownership then switch to this user id [RUBRUB_RUN_AS_UID]")
    ap.add_argument("--reset-password", metavar="USERNAME", help="set a new password for a user, then exit")
    args = ap.parse_args(argv)
    if not 1 <= args.port <= 65535:
        ap.error("--port must be between 1 and 65535")
    if args.proxy_hops < 1:
        ap.error("--proxy-hops must be at least 1")
    args.on_platform = on_platform
    return args


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.data_dir:
        configure(args.data_dir)
    if args.database_url:
        use_postgres(args.database_url)
    configure_proxy(args.trust_proxy, args.proxy_hops, args.secure_cookies)
    if args.run_as_uid is not None:
        drop_privileges(args.run_as_uid)  # before touching the database, so its files get the right owner
    init_db()
    if args.reset_password:
        return reset_password(args.reset_password)

    con = connect()
    try:
        if not args.no_seed and seed(con):
            print("Added the sample profiles: 'claude' (featured blogger) and 'test_blogger' (famous reviewer).")
        cleanup_orphan_photos(con)
    finally:
        con.close()

    server = make_server(args.host, args.port)

    def stop(*_):  # SIGTERM is how containers and hosts ask us to shut down
        raise KeyboardInterrupt

    for name in ("SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), stop)
            except (ValueError, OSError):
                pass

    shown = "localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host
    print(f"Rub rub is sizzling at http://{shown}:{server.server_address[1]}  (Ctrl+C to stop)", flush=True)
    print("Database: Postgres" if db.engine() == "postgres" else f"Data folder: {DATA_DIR}", flush=True)
    if args.trust_proxy:
        print(f"Trusting {args.proxy_hops} reverse proxy hop(s) for visitor IP and HTTPS.", flush=True)
    elif args.on_platform:
        print("WARNING: PORT is set, so this looks like a hosting platform, but --trust-proxy is off. "
              "Every visitor will share one IP for rate limits and cookies won't be Secure. "
              "Set RUBRUB_TRUST_PROXY=1.", flush=True)
    elif args.host == "0.0.0.0":
        print("Listening on all interfaces: anyone on your network can reach it. No HTTPS here.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBye!", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
