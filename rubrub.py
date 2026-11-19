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
SEED_VERSION = 4  # 2 = Claude + Test Eater, 3 = eight more sample reviews, 4 = experts and sample collections
CATEGORY_KEYWORDS = (  # checked in order; keep in sync with guessCategory() in public/js/app.js
    ("khao_man_gai", ("khao man gai", "kao man gai", "man gai", "hainan", "ข้าวมันไก่")),
    ("kra_pao", ("kra pao", "kaprao", "krapow", "ka prao", "กะเพรา", "กระเพรา")),
    ("noodles", ("noodle", "kuay teow", "guay teow", "kway teow", "ba mee", "bamee", "pad thai", "khao soi", "pad see ew",
                 "rad na", "wonton", "ก๋วยเตี๋ยว", "บะหมี่", "เส้น", "ผัดไทย", "ข้าวซอย", "ราดหน้า")),
)
CITIES = ("bangkok", "chiang_mai", "phuket", "chonburi", "hat_yai", "ayutthaya", "khon_kaen", "chiang_rai", "krabi",
          "hua_hin", "samui", "other")
CITY_KEYWORDS = (  # checked in order; keep in sync with CITY_WORDS in public/js/app.js
    ("bangkok", ("bangkok", "krung thep", "กรุงเทพ", "yaowarat", "silom", "sathorn", "sukhumvit", "thonglor", "ratchada",
                 "chatuchak", "phra nakhon", "khao san", "ratchathewi")),
    ("chiang_mai", ("chiang mai", "เชียงใหม่", "nimman")),
    ("phuket", ("phuket", "ภูเก็ต")),
    ("chonburi", ("chonburi", "pattaya", "ชลบุรี", "พัทยา", "bang saen")),
    ("hat_yai", ("hat yai", "hatyai", "songkhla", "หาดใหญ่", "สงขลา")),
    ("ayutthaya", ("ayutthaya", "อยุธยา")),
    ("khon_kaen", ("khon kaen", "ขอนแก่น")),
    ("chiang_rai", ("chiang rai", "เชียงราย")),
    ("krabi", ("krabi", "กระบี่", "ao nang")),
    ("hua_hin", ("hua hin", "หัวหิน")),
    ("samui", ("samui", "สมุย", "surat thani", "สุราษฎร์")),
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
SELECT r.*, u.username, u.display_name, u.is_featured, u.is_expert, u.expert_title,
       (SELECT COUNT(*) FROM likes l2 JOIN reviews r2 ON r2.id = l2.review_id WHERE r2.user_id = u.id) AS author_likes,
       (SELECT COUNT(*) FROM likes l WHERE l.review_id = r.id) AS like_count,
       EXISTS(SELECT 1 FROM likes l WHERE l.review_id = r.id AND l.user_id = ?) AS liked,
       EXISTS(SELECT 1 FROM saves sv WHERE sv.review_id = r.id AND sv.user_id = ?) AS saved
FROM reviews r JOIN users u ON u.id = r.user_id
"""


def viewer_args(me) -> list:
    """The two values REVIEW_SELECT asks for first: who is looking (-1 = nobody), for liked and saved."""
    vid = me["id"] if me else -1
    return [vid, vid]

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


def guess_city(text: str) -> str:
    s = (text or "").lower()
    for city, words in CITY_KEYWORDS:
        if any(w in s for w in words):
            return city
    return "other"


# Columns added after the first release: (table, column, definition). Older databases get them in init_db.
LATER_COLUMNS = (
    ("reviews", "category", "TEXT NOT NULL DEFAULT 'other'"),
    ("reviews", "city", "TEXT NOT NULL DEFAULT 'other'"),
    ("users", "is_expert", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "expert_title", "TEXT NOT NULL DEFAULT ''"),
)


def init_db() -> None:
    """Create the tables and upgrade an older database. Safe to run on every start."""
    if db.engine() == "sqlite":
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = connect()
    try:
        if con.engine == "sqlite":
            con.execute("PRAGMA journal_mode = WAL")
        con.executescript(db.schema_sql(con.engine))
        added = set()
        for table, column, definition in LATER_COLUMNS:  # a database made before these columns existed
            if not db.column_exists(con, table, column):
                con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                added.add(column)
        if "category" in added:
            for r in con.execute("SELECT id, menu_item FROM reviews").fetchall():
                con.execute("UPDATE reviews SET category = ? WHERE id = ?", (guess_category(r["menu_item"]), r["id"]))
        if "city" in added:
            for r in con.execute("SELECT id, area, restaurant_name FROM reviews").fetchall():
                con.execute("UPDATE reviews SET city = ? WHERE id = ?", (guess_city(r["area"] + " " + r["restaurant_name"]), r["id"]))
        con.execute("CREATE INDEX IF NOT EXISTS idx_reviews_city ON reviews(city)")
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

    city = d.get("city")
    if city in (None, ""):
        city = guess_city(f"{out['area']} {out['restaurant_name']}")  # API clients may omit it
    elif not isinstance(city, str) or city not in CITIES:
        errors["city"] = "invalid"
        city = "other"
    out["city"] = city

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
    return {"username": u["username"], "display_name": u["display_name"], "is_featured": bool(u["is_featured"]),
            "is_expert": bool(u["is_expert"]), "expert_title": u["expert_title"]}


def review_json(r) -> dict:
    payments = [name for name, col in (("cash_only", "cash_only"), ("qr", "pay_qr"), ("card", "pay_card")) if r[col]]
    return {
        "id": r["id"],
        "restaurant_name": r["restaurant_name"],
        "area": r["area"],
        "city": r["city"],
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
        "saved": bool(r["saved"]),
        "author": {
            "username": r["username"],
            "display_name": r["display_name"],
            "is_featured": bool(r["is_featured"]),
            "is_famous": r["author_likes"] >= FAMOUS_LIKES,
            "is_expert": bool(r["is_expert"]),
            "expert_title": r["expert_title"],
        },
    }


def user_json(u, review_count: int, avg, likes_received: int) -> dict:
    return {
        "username": u["username"],
        "display_name": u["display_name"],
        "bio": u["bio"],
        "is_featured": bool(u["is_featured"]),
        "is_expert": bool(u["is_expert"]),
        "expert_title": u["expert_title"],
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
    city = qstr(q, "city")
    if city in CITIES:
        where.append("r.city = ?")
        args.append(city)
    if qstr(q, "expert") == "1":
        where.append("u.is_expert = 1")  # tips from Experts only
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
        REVIEW_SELECT + clause + f" ORDER BY {order} LIMIT ? OFFSET ?", [*viewer_args(me), *args, limit, offset]
    ).fetchall()
    return {"reviews": [review_json(r) for r in rows], "total": total}


def fetch_review(h, review_id: int):
    return h.con.execute(REVIEW_SELECT + " WHERE r.id = ?", (*viewer_args(h.current_user()), review_id)).fetchone()


@route("GET", r"/api/reviews/(\d+)")
def api_review_get(h, q, rid):
    row = fetch_review(h, int(rid))
    if not row:
        raise ApiError(404, "not_found")
    return {"review": review_json(row)}


REVIEW_COLUMNS = (
    "restaurant_name", "area", "city", "menu_item", "category", "rating", "price_thb", "cash_only", "pay_qr", "pay_card",
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


# --------------------------------------------------------------------------
# Saved places, collections, destinations, experts and "ask the community"
# --------------------------------------------------------------------------
MAX_COLLECTIONS = 30
MAX_COLLECTION_ITEMS = 200


@route("POST", r"/api/reviews/(\d+)/save")
def api_save(h, q, rid):
    """Save for later (a private wishlist). Toggles."""
    me = h.require_user()
    if not h.con.execute("SELECT 1 FROM reviews WHERE id = ?", (int(rid),)).fetchone():
        raise ApiError(404, "not_found")
    removed = h.con.execute("DELETE FROM saves WHERE user_id = ? AND review_id = ?", (me["id"], int(rid))).rowcount
    saved = removed == 0
    if saved:
        # milliseconds, not seconds: two taps in the same second must still come back newest first
        h.con.execute("INSERT INTO saves(user_id, review_id, created_at) VALUES(?,?,?) ON CONFLICT DO NOTHING",
                      (me["id"], int(rid), int(time.time() * 1000)))
    return {"saved": saved}


@route("GET", "/api/saved")
def api_saved(h, q):
    me = h.require_user()
    limit, offset = qint(q, "limit", 24, 1, 50), qint(q, "offset", 0, 0, 100000)
    total = h.con.execute("SELECT COUNT(*) FROM saves WHERE user_id = ?", (me["id"],)).fetchone()[0]
    rows = h.con.execute(
        REVIEW_SELECT + " JOIN saves sv2 ON sv2.review_id = r.id AND sv2.user_id = ?"
        " ORDER BY sv2.created_at DESC, r.id DESC LIMIT ? OFFSET ?",
        [*viewer_args(me), me["id"], limit, offset],
    ).fetchall()
    return {"reviews": [review_json(r) for r in rows], "total": total}


COLLECTION_SELECT = """
SELECT c.*, u.username, u.display_name, u.is_featured, u.is_expert, u.expert_title,
       (SELECT COUNT(*) FROM collection_items ci WHERE ci.collection_id = c.id) AS item_count,
       (SELECT r.photo FROM collection_items ci JOIN reviews r ON r.id = ci.review_id
         WHERE ci.collection_id = c.id AND r.photo IS NOT NULL ORDER BY ci.added_at DESC, r.id DESC LIMIT 1) AS cover_photo,
       (SELECT r.category FROM collection_items ci JOIN reviews r ON r.id = ci.review_id
         WHERE ci.collection_id = c.id ORDER BY ci.added_at DESC, r.id DESC LIMIT 1) AS cover_category
FROM collections c JOIN users u ON u.id = c.user_id
"""


def collection_json(c) -> dict:
    return {
        "id": c["id"],
        "name": c["name"],
        "description": c["description"],
        "is_public": bool(c["is_public"]),
        "item_count": c["item_count"],
        "cover_photo": f"/uploads/{c['cover_photo']}" if c["cover_photo"] else None,
        "cover_category": c["cover_category"],
        "created_at": c["created_at"],
        "updated_at": c["updated_at"],
        "owner": {
            "username": c["username"],
            "display_name": c["display_name"],
            "is_featured": bool(c["is_featured"]),
            "is_expert": bool(c["is_expert"]),
            "expert_title": c["expert_title"],
        },
    }


def validate_collection(d: dict) -> dict:
    errors = {}
    name = clean_line(d.get("name"))
    if not name:
        errors["name"] = "required"
    elif len(name) < 2:
        errors["name"] = "too_short"
    elif len(name) > 60:
        errors["name"] = "too_long"
    description = clean_text(d.get("description"))
    if len(description) > 200:
        errors["description"] = "too_long"
    public = d.get("is_public", True)
    if not isinstance(public, bool):
        if public in (0, 1):
            public = bool(public)
        else:
            errors["is_public"] = "invalid"
            public = True
    if errors:
        raise ApiError(400, "validation", errors)
    return {"name": name, "description": description, "is_public": int(public)}


def visible_collection(h, cid: int):
    """A collection the current visitor may see: public ones, and their own private ones."""
    c = h.con.execute(COLLECTION_SELECT + " WHERE c.id = ?", (cid,)).fetchone()
    me = h.current_user()
    if not c or not (c["is_public"] or (me and me["id"] == c["user_id"])):
        raise ApiError(404, "not_found")
    return c


def owned_collection(h, cid: int):
    me = h.require_user()
    c = h.con.execute(COLLECTION_SELECT + " WHERE c.id = ?", (cid,)).fetchone()
    if not c:
        raise ApiError(404, "not_found")
    if c["user_id"] != me["id"]:
        raise ApiError(403, "forbidden")
    return c


@route("POST", "/api/collections")
def api_collection_create(h, q):
    me = h.require_user()
    if not rate_ok(h.con, f"collection:{me['id']}", 30, 3600):
        raise ApiError(429, "rate_limited")
    data = validate_collection(h.read_json())
    if h.con.execute("SELECT COUNT(*) FROM collections WHERE user_id = ?", (me["id"],)).fetchone()[0] >= MAX_COLLECTIONS:
        raise ApiError(400, "validation", {"name": "too_many_collections"})
    now = int(time.time())
    try:
        cid = h.con.execute(
            "INSERT INTO collections(user_id, name, description, is_public, created_at, updated_at) VALUES(?,?,?,?,?,?) RETURNING id",
            (me["id"], data["name"], data["description"], data["is_public"], now, now),
        ).fetchone()[0]
    except db.integrity_errors:
        raise ApiError(409, "validation", {"name": "name_taken"})  # one collection per name for each person
    return 201, {"collection": collection_json(visible_collection(h, cid))}


@route("GET", r"/api/collections/(\d+)")
def api_collection_get(h, q, cid):
    c = visible_collection(h, int(cid))
    me = h.current_user()
    rows = h.con.execute(
        REVIEW_SELECT + " JOIN collection_items ci2 ON ci2.review_id = r.id AND ci2.collection_id = ?"
        " ORDER BY ci2.added_at DESC, r.id DESC LIMIT 100",
        [*viewer_args(me), c["id"]],
    ).fetchall()
    return {"collection": collection_json(c), "reviews": [review_json(r) for r in rows],
            "mine": bool(me and me["id"] == c["user_id"])}


@route("PUT", r"/api/collections/(\d+)")
def api_collection_update(h, q, cid):
    c = owned_collection(h, int(cid))
    data = validate_collection(h.read_json())
    try:
        h.con.execute("UPDATE collections SET name = ?, description = ?, is_public = ?, updated_at = ? WHERE id = ?",
                      (data["name"], data["description"], data["is_public"], int(time.time()), c["id"]))
    except db.integrity_errors:
        raise ApiError(409, "validation", {"name": "name_taken"})
    return {"collection": collection_json(visible_collection(h, c["id"]))}


@route("DELETE", r"/api/collections/(\d+)")
def api_collection_delete(h, q, cid):
    c = owned_collection(h, int(cid))
    h.con.execute("DELETE FROM collections WHERE id = ?", (c["id"],))
    return {"ok": True}


@route("POST", r"/api/collections/(\d+)/items")
def api_collection_add(h, q, cid):
    c = owned_collection(h, int(cid))
    rid = as_int(h.read_json().get("review_id"))
    if rid is None or not h.con.execute("SELECT 1 FROM reviews WHERE id = ?", (rid,)).fetchone():
        raise ApiError(404, "not_found")
    if c["item_count"] >= MAX_COLLECTION_ITEMS:
        raise ApiError(400, "validation", {"review_id": "collection_full"})
    added = h.con.execute(
        "INSERT INTO collection_items(collection_id, review_id, added_at) VALUES(?,?,?) ON CONFLICT DO NOTHING",
        (c["id"], rid, int(time.time() * 1000)),  # milliseconds, so the order of additions is exact
    ).rowcount == 1
    h.con.execute("UPDATE collections SET updated_at = ? WHERE id = ?", (int(time.time()), c["id"]))
    count = h.con.execute("SELECT COUNT(*) FROM collection_items WHERE collection_id = ?", (c["id"],)).fetchone()[0]
    return 201 if added else 200, {"added": added, "item_count": count}


@route("DELETE", r"/api/collections/(\d+)/items/(\d+)")
def api_collection_remove(h, q, cid, rid):
    c = owned_collection(h, int(cid))
    removed = h.con.execute("DELETE FROM collection_items WHERE collection_id = ? AND review_id = ?", (c["id"], int(rid))).rowcount
    h.con.execute("UPDATE collections SET updated_at = ? WHERE id = ?", (int(time.time()), c["id"]))
    count = h.con.execute("SELECT COUNT(*) FROM collection_items WHERE collection_id = ?", (c["id"],)).fetchone()[0]
    return {"removed": removed == 1, "item_count": count}


@route("GET", r"/api/users/([A-Za-z0-9_]{3,20})/collections")
def api_user_collections(h, q, username):
    owner = h.con.execute("SELECT id FROM users WHERE lower(username) = lower(?)", (username,)).fetchone()
    if not owner:
        raise ApiError(404, "not_found")
    me = h.current_user()
    mine = bool(me and me["id"] == owner["id"])
    rows = h.con.execute(
        COLLECTION_SELECT + " WHERE c.user_id = ?" + ("" if mine else " AND c.is_public = 1")
        + " ORDER BY c.updated_at DESC, c.id DESC",
        (owner["id"],),
    ).fetchall()
    return {"collections": [collection_json(c) for c in rows], "mine": mine}


@route("GET", "/api/me/collections")
def api_my_collections(h, q):
    """My collections, and (when review_id is given) which of them already hold that review."""
    me = h.require_user()
    rows = h.con.execute(COLLECTION_SELECT + " WHERE c.user_id = ? ORDER BY c.updated_at DESC, c.id DESC", (me["id"],)).fetchall()
    holding = set()
    rid = qint(q, "review_id", 0, 0, 10 ** 9)
    if rid:
        holding = {r[0] for r in h.con.execute(
            "SELECT collection_id FROM collection_items WHERE review_id = ? AND collection_id IN "
            "(SELECT id FROM collections WHERE user_id = ?)", (rid, me["id"]))}
    return {"collections": [dict(collection_json(c), has_review=c["id"] in holding) for c in rows]}


@route("GET", "/api/cities")
def api_cities(h, q):
    """Destinations that have reviews, busiest first."""
    rows = h.con.execute("SELECT city, COUNT(*) AS n FROM reviews GROUP BY city ORDER BY n DESC, city").fetchall()
    return {"cities": [{"city": r["city"], "count": r["n"]} for r in rows]}


@route("GET", "/api/experts")
def api_experts(h, q):
    rows = h.con.execute(
        """SELECT * FROM (
               SELECT u.*, COUNT(r.id) AS n, AVG(r.rating) AS avg,
                      (SELECT COUNT(*) FROM likes l JOIN reviews r2 ON r2.id = l.review_id WHERE r2.user_id = u.id) AS got
               FROM users u LEFT JOIN reviews r ON r.user_id = u.id
               WHERE u.is_expert = 1
               GROUP BY u.id
           ) t
           ORDER BY n DESC, got DESC LIMIT 50"""
    ).fetchall()
    return {"experts": [user_json(u, u["n"], u["avg"], u["got"]) for u in rows]}


# --- ask the community: understands a question with plain rules (no paid AI) and answers from our own reviews ---
ASK_CATEGORY_EXTRA = {
    "khao_man_gai": ("chicken rice", "hainanese"),
    "kra_pao": ("basil", "krapao", "pad kaprow"),
    "noodles": ("noodles", "ramen", "mee ", "bamee"),
}
ASK_STOPWORDS = frozenset(
    "the and for with from that this what where which who how any can you your are best good great nice find show give want "
    "need looking look place places spot spots restaurant restaurants food eat eating near around some tell please recommend "
    "recommendation recommendations tips tip please under below than less cheaper cheap cheapest budget affordable only "
    "spicy hot mild not late night midnight early morning breakfast expert experts baht thb pay paying payment accept "
    "accepts open cash qr promptpay card cards credit scan dish dishes menu lunch dinner snack snacks street thai thailand "
    "stall stalls cart carts vendor vendors shop shops koh".split())
# Thai words that carry no dish or place ("price", "shop", "open", "from"...): removed from a chunk of Thai text
ASK_THAI_FILLER = ("ราคา", "ถูก", "ร้าน", "เปิด", "ดึก", "เผ็ด", "ไม่เผ็ด", "เงินสด", "สแกน", "บัตร", "จาก", "ผู้เชี่ยวชาญ",
                   "แนะนำ", "อร่อย", "เช้า", "ที่", "ใน", "ๆ", "ข้าว", "อาหาร", "เมนู", "กิน")


def interpret_question(text: str) -> dict:
    """What the visitor asked for, as filters. Understands English and some Thai."""
    s = " " + text.lower() + " "
    found = {"category": None, "city": None, "payment": None, "max_price": None, "spice": None,
             "late": False, "morning": False, "expert": False}
    used = set()
    for cat, words in CATEGORY_KEYWORDS:
        hit = [w for w in (*words, *ASK_CATEGORY_EXTRA.get(cat, ())) if w in s]
        if hit:
            found["category"] = cat
            used.update(hit)
            break
    for city, words in CITY_KEYWORDS:
        hit = [w for w in words if w in s]
        if hit:
            found["city"] = city
            used.update(hit)
            break
    if re.search(r"\bcash\b|เงินสด", s):
        found["payment"] = "cash_only"
    elif re.search(r"\bqr\b|promptpay|สแกน", s):
        found["payment"] = "qr"
    elif re.search(r"\bcards?\b|credit|บัตร", s):
        found["payment"] = "card"
    price = re.search(r"(?:under|below|less than|cheaper than|max|ไม่เกิน|ต่ำกว่า)\s*(?:฿|thb|baht)?\s*(\d{2,4})", s)
    if price:
        found["max_price"] = int(price.group(1))
    elif re.search(r"\b(?:cheap|cheapest|budget|affordable)\b|ถูก", s):
        found["max_price"] = 60
    if re.search(r"not spicy|non-spicy|\bmild\b|no chili|ไม่เผ็ด", s):
        found["spice"] = "mild"
    elif re.search(r"\b(?:spicy|hot|fiery)\b|เผ็ด", s):
        found["spice"] = "hot"
    found["late"] = bool(re.search(r"\b(?:late|midnight|night)\b|ดึก", s))
    found["morning"] = bool(re.search(r"\b(?:breakfast|morning|early)\b|เช้า", s))
    found["expert"] = bool(re.search(r"\bexperts?\b|ผู้เชี่ยวชาญ", s))
    leftovers = []
    for token in re.findall(r"[a-z0-9ก-๙']{3,}", s):
        if re.search(r"[ก-๙]", token):  # Thai has no spaces: take the words we know out of the chunk, keep what is left
            rest = token
            for word in sorted((*used, *ASK_THAI_FILLER), key=len, reverse=True):
                rest = rest.replace(word.strip(), "")
            if len(rest) >= 3:
                leftovers.append(rest)
        elif token not in ASK_STOPWORDS and not any(token in u or u.strip() in token for u in used):
            leftovers.append(token)
    found["words"] = leftovers[:5]
    return found


def ask_filter(found: dict, with_words: bool):
    where, args = [], []
    if found["category"]:
        where.append("r.category = ?")
        args.append(found["category"])
    if found["city"]:
        where.append("r.city = ?")
        args.append(found["city"])
    pay_column = {"cash_only": "r.cash_only", "qr": "r.pay_qr", "card": "r.pay_card"}.get(found["payment"])
    if pay_column:
        where.append(f"{pay_column} = 1")
    if found["max_price"]:
        where.append("r.price_thb <= ?")
        args.append(found["max_price"])
    if found["spice"] == "mild":
        where.append("r.spice <= 1")
    elif found["spice"] == "hot":
        where.append("r.spice >= 2")
    if found["late"]:
        where.append("(r.close_time < r.open_time OR r.close_time >= '22:00')")
    if found["morning"]:
        where.append("r.open_time <= '08:00'")
    if found["expert"]:
        where.append("u.is_expert = 1")
    if with_words and found["words"]:
        ors = []
        for word in found["words"]:
            like = "%" + like_escape(word) + "%"
            ors.append("(lower(r.menu_item) LIKE ? ESCAPE '\\' OR lower(r.restaurant_name) LIKE ? ESCAPE '\\' "
                       "OR lower(r.area) LIKE ? ESCAPE '\\' OR lower(r.body) LIKE ? ESCAPE '\\')")
            args += [like] * 4
        where.append("(" + " OR ".join(ors) + ")")
    return where, args


@route("GET", "/api/ask")
def api_ask(h, q):
    if not rate_ok(h.con, f"ask:{h.client_ip()}", 60, 600):
        raise ApiError(429, "rate_limited")
    question = clean_line(qstr(q, "q"))
    if not question:
        raise ApiError(400, "validation", {"q": "required"})
    if len(question) > 200:
        raise ApiError(400, "validation", {"q": "too_long"})
    me = h.current_user()
    found = interpret_question(question)
    structured = any(found[k] for k in ("category", "city", "payment", "max_price", "spice", "late", "morning", "expert"))

    def run(with_words: bool):
        where, args = ask_filter(found, with_words)
        if not where:
            return []
        return h.con.execute(
            REVIEW_SELECT + " WHERE " + " AND ".join(where)
            + " ORDER BY u.is_expert DESC, like_count DESC, r.rating DESC, r.id DESC LIMIT 5",
            [*viewer_args(me), *args],
        ).fetchall()

    rows = run(True)
    loosened = False
    if not rows and structured and found["words"]:
        rows, loosened = run(False), True  # nothing matches every word: show the closest by the clear filters
    interpretation = {k: v for k, v in found.items() if v}
    body = {"found": bool(rows), "loosened": loosened and bool(rows), "interpretation": interpretation,
            "results": [review_json(r) for r in rows]}
    if not rows:  # an honest "I don't know", with places to start from
        cities = h.con.execute("SELECT city, COUNT(*) AS n FROM reviews WHERE city <> 'other' GROUP BY city ORDER BY n DESC, city LIMIT 4").fetchall()
        body["suggestions"] = {"cities": [r["city"] for r in cities], "categories": [c for c in CATEGORIES if c != "other"]}
    return body


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
            self.send_json(database_problem(e), 503)
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


def database_problem(error) -> dict:
    """What a visitor may learn about a database problem: a code, and for a site that has no database
    address yet the NAMES of related settings (never values), so the owner can see what is missing."""
    body = {"error": error.code}
    details = getattr(error, "details", None)
    if details:
        body["variables_seen"] = details
    return body


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
        seen = db.describe_variables(env)
        raise db.DatabaseNotConfigured(
            "No Postgres address found in the environment. In the Vercel dashboard open Storage, create a "
            "Neon Postgres database, connect it to this project (tick Production) and REDEPLOY (variables only "
            "reach new deployments). Variables that look related (names only): " + (", ".join(seen) or "none"),
            details=seen)
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
            req.send_json(database_problem(e), 503)
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

# Fictional "sample foodies": they like the tester's reviews and write a few of their own (they can't log in).
DEMO_FANS = [("demo_nok", "Nok"), ("demo_pim", "Pim"), ("demo_ton", "Ton"), ("demo_mali", "Mali"),
             ("demo_beam", "Beam"), ("demo_ploy", "Ploy"), ("demo_arm", "Arm"), ("demo_fah", "Fah"),
             ("demo_nan", "Nan"), ("demo_pete", "Pete"), ("demo_gift", "Gift"), ("demo_mint", "Mint")]
TESTER_LIKES = [12, 10, 9, 8, 7, 6]  # per review, 52 in total, comfortably over FAMOUS_LIKES

# Eight more sample reviews, one for each of eight different sample foodies, so the feed and the
# category filters look lively. The vendors are fictional.
MORE_REVIEWS = [
    dict(
        author="demo_pim", restaurant_name="Auntie Dao's Fried Chicken Rice", area="Hat Yai, Songkhla",
        menu_item="Khao Man Gai Tod (fried chicken rice) + nam jim", category="khao_man_gai", rating=5, price_thb=60,
        payments=["qr"], open_days=DAYS, open_time="07:00", close_time="15:00", spice=1,
        body="Shatteringly crisp skin, juicy meat, and rice that smells of chicken fat and garlic. The sweet-spicy "
             "dipping sauce is the reason to come. Eat it at the plastic table next to the cart.",
    ),
    dict(
        author="demo_nok", restaurant_name="Uncle Chai's Chicken Rice Cart", area="Ratchada, Bangkok",
        menu_item="Khao Man Gai Tom (poached chicken) + clear soup", category="khao_man_gai", rating=4, price_thb=50,
        payments=["cash_only"], open_days=DAYS[:6], open_time="06:30", close_time="12:30", spice=0,
        body="Plain, honest and comforting: tender chicken, fragrant rice, a bowl of mild winter-melon soup. Ask for "
             "extra ginger sauce if you like a kick. Gone by half past twelve, so go early.",
    ),
    dict(
        author="demo_ton", restaurant_name="Wok Fire Corner", area="Sukhumvit Soi 22, Bangkok",
        menu_item="Pad Kra Pao Moo Krob (crispy pork) + fried egg", category="kra_pao", rating=5, price_thb=70,
        payments=["qr", "card"], open_days=DAYS, open_time="11:00", close_time="21:00", spice=3,
        body="Crackling pork belly in a screaming-hot basil stir fry, with a runny egg to calm it down. Order it "
             "'phet mak' and bring a cold drink. The queue moves faster than it looks.",
    ),
    dict(
        author="demo_mali", restaurant_name="Basil Brothers Night Cart", area="Ratchathewi, Bangkok",
        menu_item="Pad Kra Pao Nuea (beef) rad khao", category="kra_pao", rating=4, price_thb=65,
        payments=["cash_only"], open_days=DAYS[:6], open_time="17:00", close_time="00:30", spice=2,
        body="Thin-sliced beef, loads of holy basil and a flame taller than the cook. Perfect after a long day. "
             "Cash only, and it stays open past midnight from Monday to Saturday.",
    ),
    dict(
        author="demo_beam", restaurant_name="Rolled Noodle Corner", area="Yaowarat, Bangkok",
        menu_item="Guay Jub Nam Khon (rolled rice noodle soup)", category="noodles", rating=4, price_thb=60,
        payments=["cash_only"], open_days=DAYS, open_time="16:00", close_time="23:00", spice=1,
        body="Peppery broth, curly rolled noodles, crispy pork belly and a soft egg. Warming on a rainy evening "
             "and ready in two minutes. Add the pickled chili for sparkle.",
    ),
    dict(
        author="demo_ploy", restaurant_name="Pink Noodle Lady", area="Chonburi Market",
        menu_item="Yen Ta Fo (pink noodle soup) with fish balls", category="noodles", rating=4, price_thb=55,
        payments=["qr"], open_days=DAYS[1:], open_time="09:00", close_time="15:00", spice=1,
        body="Tangy, sweet, slightly funky pink broth with squid, fish balls and morning glory. It looks wild and "
             "tastes better. A good first try if you want something different.",
    ),
    dict(
        author="demo_arm", restaurant_name="Uncle Daeng's Braised Pork Leg", area="Phra Nakhon, Bangkok",
        menu_item="Khao Kha Moo (braised pork leg on rice) + boiled egg", category="other", rating=5, price_thb=60,
        payments=["cash_only"], open_days=DAYS, open_time="10:00", close_time="19:00", spice=0,
        body="Pork leg braised for hours until it falls apart, sweet-salty gravy, pickled mustard greens and a "
             "perfect egg. Spoon the garlic-chili vinegar over it. A bargain for sixty baht.",
    ),
    dict(
        author="demo_fah", restaurant_name="Little Coconut Pancake Stall", area="Old Town, Phuket",
        menu_item="Kanom Krok (coconut pancakes) x10", category="other", rating=5, price_thb=30,
        payments=["cash_only"], open_days=DAYS[2:], open_time="15:00", close_time="20:00", spice=0,
        body="Crispy edges, a creamy coconut middle, and scallions or sweet corn on top. Ten fresh from the pan "
             "disappear very quickly. Open Wednesday to Sunday afternoons only.",
    ),
]
MORE_LIKES = [5, 4, 4, 3, 3, 2, 2, 1]  # per review, a few each: nobody here becomes famous

# Sample "Experts" (a curated label, set by the site owner; see --set-expert). Fictional demo people.
DEMO_EXPERTS = {"claude": "Food blogger", "demo_ton": "Street food chef", "demo_nok": "Food writer"}

# Sample collections: (owner, name, description, which reviews to put in it). The conditions are fixed
# SQL written here, never user input.
SAMPLE_COLLECTIONS = [
    ("claude", "Noodle crawl", "Bowls worth a detour, from boat noodles to khao soi.", "category = 'noodles'"),
    ("claude", "Cash-only classics", "Bring notes: these stalls take cash and nothing else.", "cash_only = 1"),
    ("claude", "Late-night bites", "Still serving when everything else has closed.",
     "(close_time < open_time OR close_time >= '22:00')"),
    ("test_blogger", "Basil lovers", "Every pad kra pao I would cross the city for.", "category = 'kra_pao'"),
]


def _add_sample_review(con, user_id: int, s: dict, created: int) -> int:
    row = {k: v for k, v in s.items() if k not in ("payments", "open_days")}
    row.setdefault("category", guess_category(s["menu_item"]))
    row.setdefault("city", guess_city(f"{s.get('area', '')} {s.get('restaurant_name', '')}"))
    row.update(payment_columns(s["payments"]))
    row["open_days"] = ",".join(s["open_days"])
    row.update(user_id=user_id, photo=None, is_sample=1, created_at=created, updated_at=created)
    cols = list(row)
    return con.execute(
        f"INSERT INTO reviews({', '.join(cols)}) VALUES({', '.join('?' * len(cols))}) RETURNING id", [row[c] for c in cols]
    ).fetchone()[0]


def ensure_demo_fans(con, now: int) -> dict:
    """The fictional sample foodies as {username: id}, created when missing."""
    names = [u for u, _ in DEMO_FANS]
    marks = ",".join("?" * len(names))
    taken = {r[0] for r in con.execute(f"SELECT lower(username) FROM users WHERE lower(username) IN ({marks})", names)}
    new_fans = [(u, n) for u, n in DEMO_FANS if u not in taken]
    if new_fans:
        con.execute(
            "INSERT INTO users(username, display_name, pw_hash, bio, created_at) VALUES "
            + ",".join(["(?,?,NULL,?,?)"] * len(new_fans)),
            [x for u, n in new_fans for x in (u, n, "Sample foodie account (demo data: fictional reviews and likes).", now - 20 * 86400)],
        )
    return {r[0]: r[1] for r in con.execute(f"SELECT lower(username), id FROM users WHERE lower(username) IN ({marks})", names)}


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
    version = seed_version(con)
    if version >= SEED_VERSION:
        return False

    def exists(name: str) -> bool:
        return con.execute("SELECT 1 FROM users WHERE lower(username) = lower(?)", (name,)).fetchone() is not None

    now = int(time.time())
    added = False
    try:
        if not exists("claude"):
            uid = con.execute(
                "INSERT INTO users(username, display_name, pw_hash, bio, is_featured, is_expert, expert_title, created_at) "
                "VALUES(?,?,NULL,?,1,1,?,?) RETURNING id",
                ("claude", "Claude", CLAUDE_BIO, DEMO_EXPERTS["claude"], now),
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
            ids = ensure_demo_fans(con, now)
            fans = [ids[u] for u, _ in DEMO_FANS]
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
        if version < 3:  # a database seeded before these existed gets just this part
            ids = ensure_demo_fans(con, now)
            fans = [ids[u] for u, _ in DEMO_FANS]
            likes = []
            for i, s in enumerate(MORE_REVIEWS):
                author = ids[s["author"]]
                rid = _add_sample_review(con, author, {k: v for k, v in s.items() if k != "author"}, now - i * 4200 - 900)
                others = [f for f in fans if f != author]
                likes += [(others[(i * 3 + k) % len(others)], rid) for k in range(MORE_LIKES[i])]
            con.execute(
                "INSERT INTO likes(user_id, review_id) VALUES " + ",".join(["(?,?)"] * len(likes)) + " ON CONFLICT DO NOTHING",
                [x for pair in likes for x in pair],
            )
            con.commit()
            added = True
        if version < 4:  # sample Experts and collections (cities are filled in by the review upgrade)
            for username, title in DEMO_EXPERTS.items():
                con.execute("UPDATE users SET is_expert = 1, expert_title = ? WHERE lower(username) = ? AND is_expert = 0",
                            (title, username))
            for owner, name, description, condition in SAMPLE_COLLECTIONS:
                row = con.execute("SELECT id FROM users WHERE lower(username) = ?", (owner,)).fetchone()
                if not row or con.execute("SELECT 1 FROM collections WHERE user_id = ? AND name = ?", (row[0], name)).fetchone():
                    continue
                cid = con.execute(
                    "INSERT INTO collections(user_id, name, description, is_public, created_at, updated_at) VALUES(?,?,?,1,?,?) RETURNING id",
                    (row[0], name, description, now, now),
                ).fetchone()[0]
                con.execute(
                    "INSERT INTO collection_items(collection_id, review_id, added_at) "
                    f"SELECT CAST(? AS BIGINT), id, CAST(? AS BIGINT) FROM reviews WHERE {condition} ORDER BY id LIMIT 8",
                    (cid, now * 1000),
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


def set_expert(username: str, title: str, on: bool = True) -> int:
    """Give a user the Expert badge (or take it away). There is no sign-up for it: the site owner decides."""
    con = connect()
    try:
        row = con.execute("SELECT id, username FROM users WHERE lower(username) = lower(?)", (username,)).fetchone()
        if not row:
            print(f"No such user: {username}", file=sys.stderr)
            return 1
        title = clean_line(title)[:60] if on else ""
        con.execute("UPDATE users SET is_expert = ?, expert_title = ? WHERE id = ?", (1 if on else 0, title, row["id"]))
        con.commit()
        print(f"{row['username']} is now an Expert" + (f" ({title})." if title else ".") if on else f"{row['username']} is no longer an Expert.")
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
    ap.add_argument("--set-expert", metavar="USERNAME", help="give a user the Expert badge, then exit (see --expert-title)")
    ap.add_argument("--expert-title", default="", help="what the Expert is, e.g. 'Food writer' (with --set-expert)")
    ap.add_argument("--remove-expert", metavar="USERNAME", help="take the Expert badge away, then exit")
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
    if args.set_expert:
        return set_expert(args.set_expert, args.expert_title, True)
    if args.remove_expert:
        return set_expert(args.remove_expert, "", False)

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
