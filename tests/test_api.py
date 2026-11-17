"""API tests for Rub rub. Run from the project folder:  python -m unittest discover tests -v"""
import contextlib
import http.client
import io
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from http.cookies import SimpleCookie
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

GOOD_REVIEW = {
    "restaurant_name": "Test Wok Cart",
    "area": "Ari",
    "menu_item": "Pad Kra Pao",
    "category": "kra_pao",
    "rating": 5,
    "price_thb": 60,
    "spice": 3,
    "payments": ["qr", "card"],
    "open_days": ["Tue", "Mon", "Wed"],
    "open_time": "18:00",
    "close_time": "02:00",
    "body": "Smoky and hot.",
}


class Client:
    """Tiny cookie-keeping HTTP client."""

    def __init__(self, port):
        self.port = port
        self.cookie = None

    def request(self, method, path, body=None, raw=None, headers=None, csrf=True):
        h = {"Host": f"127.0.0.1:{self.port}"}
        if csrf:
            h["X-Requested-With"] = "rubrub"
        if self.cookie:
            h["Cookie"] = self.cookie
        data = None
        if raw is not None:
            data = raw
        elif body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=data, headers=h)
            res = conn.getresponse()
            payload = res.read()
            for v in res.headers.get_all("Set-Cookie") or []:
                jar = SimpleCookie()
                jar.load(v)
                morsel = jar.get(server.SESSION_COOKIE)
                if morsel is not None:
                    self.cookie = f"{server.SESSION_COOKIE}={morsel.value}" if morsel.value else None
            ctype = res.headers.get("Content-Type", "")
            parsed = json.loads(payload) if "json" in ctype and payload else payload
            return res.status, parsed, res.headers
        finally:
            conn.close()


class AppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        server.configure(cls.tmp.name)
        server.init_db()
        con = server.connect()
        server.seed(con)
        con.close()
        cls.httpd = server.make_server("127.0.0.1", 0)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        server.RATE.clear()

    def client(self):
        return Client(self.port)

    def signup(self, name, password="hunter2hunter2"):
        c = self.client()
        status, body, _ = c.request("POST", "/api/signup", {"username": name, "password": password})
        self.assertEqual(status, 201, body)
        return c

    # ---- auth ------------------------------------------------------------
    def test_signup_login_logout_flow(self):
        c = self.signup("alice_1")
        status, body, _ = c.request("GET", "/api/me")
        self.assertEqual(body["user"]["username"], "alice_1")
        self.assertEqual(body["user"]["display_name"], "alice_1")
        c.request("POST", "/api/logout")
        self.assertEqual(c.request("GET", "/api/me")[1]["user"], None)
        status, body, _ = c.request("POST", "/api/login", {"username": "ALICE_1", "password": "hunter2hunter2"})
        self.assertEqual(status, 200, body)

    def test_signup_validation_and_duplicates(self):
        c = self.client()
        status, body, _ = c.request("POST", "/api/signup", {"username": "x", "password": "short"})
        self.assertEqual(status, 400)
        self.assertEqual(body["fields"]["username"], "invalid_username")
        self.assertEqual(body["fields"]["password"], "password_short")
        self.signup("bob_dup")
        status, body, _ = c.request("POST", "/api/signup", {"username": "BOB_DUP", "password": "hunter2hunter2"})
        self.assertEqual(status, 409)
        self.assertEqual(body["fields"]["username"], "username_taken")

    def test_cannot_register_the_featured_blogger_name(self):
        status, body, _ = self.client().request("POST", "/api/signup", {"username": "Claude", "password": "hunter2hunter2"})
        self.assertEqual(status, 409)

    def test_wrong_password_and_locked_seed_account(self):
        self.signup("carol_x")
        c = self.client()
        self.assertEqual(c.request("POST", "/api/login", {"username": "carol_x", "password": "nope-nope-nope"})[0], 401)
        # the seeded featured account has no password until --reset-password is used
        self.assertEqual(c.request("POST", "/api/login", {"username": "claude", "password": "anything-at-all"})[0], 401)

    def test_login_rate_limit(self):
        c = self.client()
        codes = [c.request("POST", "/api/login", {"username": "ghost", "password": "x"})[0] for _ in range(10)]
        self.assertEqual(codes[:8], [401] * 8)
        self.assertEqual(codes[8], 429)

    def test_csrf_header_required(self):
        status, body, _ = self.client().request("POST", "/api/login", {"username": "a", "password": "b"}, csrf=False)
        self.assertEqual(status, 403)
        c = self.client()
        status, _, _ = c.request("POST", "/api/login", {"username": "a", "password": "b"},
                                 headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)

    # ---- reviews ---------------------------------------------------------
    def test_seed_and_public_listing(self):
        status, body, _ = self.client().request("GET", "/api/reviews?featured=1&sort=top")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(body["total"], 8)
        first = body["reviews"][0]
        self.assertEqual(first["author"]["username"], "claude")
        self.assertTrue(first["author"]["is_featured"])
        self.assertTrue(first["is_sample"])

    def test_create_requires_login(self):
        self.assertEqual(self.client().request("POST", "/api/reviews", GOOD_REVIEW)[0], 401)

    def test_create_read_update_delete(self):
        c = self.signup("dave_rev")
        status, body, _ = c.request("POST", "/api/reviews", GOOD_REVIEW)
        self.assertEqual(status, 201, body)
        r = body["review"]
        self.assertEqual(r["open_days"], ["Mon", "Tue", "Wed"])  # canonical order
        self.assertEqual(r["payments"], ["qr", "card"])
        self.assertEqual(r["author"]["username"], "dave_rev")
        edited = dict(GOOD_REVIEW, price_thb=75, payments=["cash_only"])
        status, body, _ = c.request("PUT", f"/api/reviews/{r['id']}", edited)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["review"]["price_thb"], 75)
        self.assertEqual(body["review"]["payments"], ["cash_only"])
        self.assertEqual(c.request("GET", f"/api/reviews?payment=cash_only&q=Test%20Wok")[1]["total"], 1)
        self.assertEqual(c.request("DELETE", f"/api/reviews/{r['id']}")[0], 200)
        self.assertEqual(c.request("GET", f"/api/reviews/{r['id']}")[0], 404)

    def test_review_validation(self):
        c = self.signup("erin_val")
        bad = dict(GOOD_REVIEW, restaurant_name="", rating=9, price_thb=0, payments=[], open_days=[],
                   open_time="25:00", close_time="02:00", body="x" * 1001)
        status, body, _ = c.request("POST", "/api/reviews", bad)
        self.assertEqual(status, 400)
        f = body["fields"]
        self.assertEqual(f["restaurant_name"], "required")
        self.assertEqual(f["rating"], "invalid")
        self.assertEqual(f["price_thb"], "invalid")
        self.assertEqual(f["payments"], "pick_one")
        self.assertEqual(f["open_days"], "pick_one")
        self.assertEqual(f["open_time"], "invalid")
        self.assertEqual(f["body"], "too_long")
        status, body, _ = c.request("POST", "/api/reviews", dict(GOOD_REVIEW, payments=["cash_only", "qr"]))
        self.assertEqual(body["fields"]["payments"], "cash_only_exclusive")
        status, body, _ = c.request("POST", "/api/reviews", dict(GOOD_REVIEW, close_time="18:00"))
        self.assertEqual(body["fields"]["close_time"], "same_time")
        status, body, _ = c.request("POST", "/api/reviews", dict(GOOD_REVIEW, photo="../../etc/passwd"))
        self.assertEqual(body["fields"]["photo"], "invalid")
        status, body, _ = c.request("POST", "/api/reviews", dict(GOOD_REVIEW, rating=True))
        self.assertEqual(body["fields"]["rating"], "invalid")

    def test_only_owner_can_edit_or_delete(self):
        owner = self.signup("frank_own")
        other = self.signup("gina_oth")
        rid = owner.request("POST", "/api/reviews", GOOD_REVIEW)[1]["review"]["id"]
        self.assertEqual(other.request("PUT", f"/api/reviews/{rid}", GOOD_REVIEW)[0], 403)
        self.assertEqual(other.request("DELETE", f"/api/reviews/{rid}")[0], 403)
        self.assertEqual(self.client().request("DELETE", f"/api/reviews/{rid}")[0], 401)
        self.assertEqual(owner.request("GET", f"/api/reviews/{rid}")[1]["review"]["restaurant_name"], "Test Wok Cart")

    def test_like_toggle(self):
        c = self.signup("hank_lik")
        rid = c.request("GET", "/api/reviews?limit=1")[1]["reviews"][0]["id"]
        s, body, _ = c.request("POST", f"/api/reviews/{rid}/like")
        self.assertTrue(body["liked"])
        self.assertEqual(c.request("GET", f"/api/reviews/{rid}")[1]["review"]["liked"], True)
        s, body, _ = c.request("POST", f"/api/reviews/{rid}/like")
        self.assertFalse(body["liked"])
        self.assertEqual(self.client().request("POST", f"/api/reviews/{rid}/like")[0], 401)

    def test_user_profile_and_restaurants(self):
        status, body, _ = self.client().request("GET", "/api/users/claude")
        self.assertEqual(status, 200)
        self.assertTrue(body["user"]["is_featured"])
        self.assertGreaterEqual(body["user"]["review_count"], 8)
        self.assertEqual(self.client().request("GET", "/api/users/nobody_here")[0], 404)
        status, body, _ = self.client().request("GET", "/api/restaurants")
        names = [r["name"] for r in body["restaurants"]]
        self.assertIn("Auntie Noi's Wok Cart", names)

    # ---- categories ------------------------------------------------------
    def test_category_filter_has_samples_in_every_category(self):
        c = self.client()
        for cat in server.CATEGORIES:
            status, body, _ = c.request("GET", f"/api/reviews?category={cat}&limit=50")
            self.assertEqual(status, 200)
            self.assertGreaterEqual(body["total"], 2, cat)
            self.assertTrue(all(r["category"] == cat for r in body["reviews"]), cat)
        # an unknown value is ignored rather than breaking the page
        total = c.request("GET", "/api/reviews")[1]["total"]
        self.assertEqual(c.request("GET", "/api/reviews?category=pizza")[1]["total"], total)

    def test_category_validation_and_guessing(self):
        c = self.signup("kim_cat")
        status, body, _ = c.request("POST", "/api/reviews", dict(GOOD_REVIEW, category="pizza"))
        self.assertEqual(status, 400)
        self.assertEqual(body["fields"]["category"], "invalid")
        no_cat = {k: v for k, v in GOOD_REVIEW.items() if k != "category"}
        for menu, want in [("Khao Man Gai Pak", "khao_man_gai"), ("ข้าวมันไก่ทอด", "khao_man_gai"), ("Pad Kra Pao Moo", "kra_pao"),
                           ("ผัดกะเพราหมูสับ", "kra_pao"), ("Kuay Teow Reua", "noodles"), ("ก๋วยเตี๋ยวต้มยำ", "noodles"),
                           ("Roti Gluay", "other")]:
            r = c.request("POST", "/api/reviews", dict(no_cat, menu_item=menu))[1]["review"]
            self.assertEqual(r["category"], want, menu)

    def test_edit_can_change_category(self):
        c = self.signup("lee_edit")
        r = c.request("POST", "/api/reviews", dict(GOOD_REVIEW, menu_item="Mystery Plate", category="other"))[1]["review"]
        status, body, _ = c.request("PUT", f"/api/reviews/{r['id']}", dict(GOOD_REVIEW, menu_item="Mystery Plate", category="noodles"))
        self.assertEqual(body["review"]["category"], "noodles")

    # ---- fame ------------------------------------------------------------
    def test_tester_is_famous_because_of_likes(self):
        c = self.client()
        user = c.request("GET", "/api/users/test_blogger")[1]["user"]
        self.assertEqual(user["review_count"], 6)
        self.assertGreaterEqual(user["likes_received"], server.FAMOUS_LIKES)
        self.assertTrue(user["is_famous"])
        self.assertFalse(user["is_featured"])
        # the badge also travels with each of their reviews
        reviews = c.request("GET", "/api/reviews?user=test_blogger&limit=50")[1]["reviews"]
        self.assertEqual(len(reviews), 6)
        self.assertTrue(all(r["author"]["is_famous"] for r in reviews))
        self.assertTrue(all(r["likes"] > 0 for r in reviews))
        # the "famous reviewers" list contains them (the featured blogger has her own spotlight)
        names = [u["username"] for u in c.request("GET", "/api/users")[1]["users"]]
        self.assertIn("test_blogger", names)
        self.assertNotIn("claude", names)

    def test_new_users_are_not_famous_and_cannot_like_themselves(self):
        c = self.signup("mia_new")
        self.assertFalse(c.request("GET", "/api/users/mia_new")[1]["user"]["is_famous"])
        rid = c.request("POST", "/api/reviews", GOOD_REVIEW)[1]["review"]["id"]
        status, body, _ = c.request("POST", f"/api/reviews/{rid}/like")
        self.assertEqual((status, body["error"]), (403, "own_review"))
        self.assertEqual(c.request("GET", f"/api/reviews/{rid}")[1]["review"]["likes"], 0)

    def test_fame_is_earned_from_other_peoples_likes(self):
        star = self.signup("nina_star")
        rid = star.request("POST", "/api/reviews", GOOD_REVIEW)[1]["review"]["id"]
        con = server.connect()
        try:
            uid = con.execute("SELECT id FROM users WHERE username = 'nina_star'").fetchone()[0]
            for i in range(server.FAMOUS_LIKES):
                fan = con.execute("INSERT INTO users(username, display_name, created_at) VALUES(?,?,0)", (f"fan_{i:03d}", "fan")).lastrowid
                con.execute("INSERT INTO likes(user_id, review_id) VALUES(?,?)", (fan, rid))
            con.commit()
        finally:
            con.close()
        self.assertTrue(star.request("GET", "/api/users/nina_star")[1]["user"]["is_famous"])
        self.assertIn("nina_star", [u["username"] for u in star.request("GET", "/api/users?limit=20")[1]["users"]])

    def test_seed_is_not_repeated(self):
        con = server.connect()
        try:
            self.assertFalse(server.seed(con))  # version recorded already
            before = con.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
            server.seed(con)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], before)
        finally:
            con.close()

    def test_search_escapes_wildcards(self):
        status, body, _ = self.client().request("GET", "/api/reviews?q=%25")
        self.assertEqual(body["total"], 0)

    # ---- uploads ---------------------------------------------------------
    def test_upload_and_attach_photo(self):
        c = self.signup("ivy_pic")
        status, body, _ = c.request("POST", "/api/upload", raw=PNG, headers={"Content-Type": "image/png"})
        self.assertEqual(status, 201, body)
        name = body["photo"]
        status, data, headers = self.client().request("GET", body["url"])
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        r = c.request("POST", "/api/reviews", dict(GOOD_REVIEW, photo=name))[1]["review"]
        self.assertEqual(r["photo"], f"/uploads/{name}")
        c.request("DELETE", f"/api/reviews/{r['id']}")
        self.assertEqual(self.client().request("GET", f"/uploads/{name}")[0], 404)  # cleaned up

    def test_upload_rejects_non_images_and_big_files(self):
        c = self.signup("jon_bad")
        s, _, _ = c.request("POST", "/api/upload", raw=b"<script>alert(1)</script>", headers={"Content-Type": "image/png"})
        self.assertEqual(s, 415)
        s, _, _ = c.request("POST", "/api/upload", raw=PNG + b"0" * (server.MAX_UPLOAD + 1))
        self.assertEqual(s, 413)
        self.assertEqual(self.client().request("POST", "/api/upload", raw=PNG)[0], 401)

    # ---- static ----------------------------------------------------------
    def test_static_files_and_traversal(self):
        c = self.client()
        s, body, h = c.request("GET", "/")
        self.assertEqual(s, 200)
        self.assertIn(b"Rub rub", body)
        self.assertIn("script-src 'self'", h["Content-Security-Policy"])
        self.assertEqual(c.request("GET", "/js/app.js")[2]["Content-Type"], "text/javascript; charset=utf-8")
        for evil in ("/../server.py", "/%2e%2e/server.py", "/..%5cserver.py", "/css/../../server.py", "/uploads/../rubrub.db",
                     "/index.html::$DATA"):
            s, _, _ = c.request("GET", evil)
            self.assertEqual(s, 404, evil)
        self.assertEqual(c.request("GET", "/api/nope")[0], 404)

    # ---- installable app (PWA) ---------------------------------------------
    def test_app_manifest_is_installable(self):
        c = self.client()
        status, manifest, headers = c.request("GET", "/manifest.webmanifest")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/manifest+json"))
        self.assertIsInstance(manifest, dict)  # the test client parses any "...+json" body for us
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["start_url"].split("?")[0], "/")
        self.assertTrue(manifest["name"] and manifest["short_name"])
        self.assertRegex(manifest["theme_color"], r"^#[0-9A-Fa-f]{6}$")
        sizes = {(i["sizes"], i.get("purpose", "any")) for i in manifest["icons"]}
        self.assertTrue({("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= sizes)
        for icon in manifest["icons"]:  # every icon must be a real PNG of the size it claims
            status, data, h = c.request("GET", icon["src"])
            self.assertEqual((status, h["Content-Type"]), (200, "image/png"), icon["src"])
            self.assertEqual(data[:8], PNG[:8], icon["src"])
            w, h_ = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
            self.assertEqual(f"{w}x{h_}", icon["sizes"], icon["src"])

    def test_page_links_the_app_files(self):
        c = self.client()
        html = c.request("GET", "/")[1].decode()
        for needle in ('rel="manifest" href="/manifest.webmanifest"', 'rel="apple-touch-icon" href="/icons/apple-touch-icon.png"',
                       "viewport-fit=cover", 'name="apple-mobile-web-app-capable"', 'src="/js/pwa.js"'):
            self.assertIn(needle, html)
        status, data, h = c.request("GET", "/icons/apple-touch-icon.png")
        self.assertEqual((status, data[:8]), (200, PNG[:8]))
        self.assertEqual((int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")), (180, 180))

    def test_service_worker_precaches_only_files_that_exist(self):
        c = self.client()
        status, sw, h = c.request("GET", "/sw.js")
        self.assertEqual(status, 200)
        self.assertTrue(h["Content-Type"].startswith("text/javascript"))
        self.assertEqual(h["Cache-Control"], "no-cache")  # browsers must always re-check the worker itself
        shell = re.search(r"const SHELL = \[(.*?)\];", sw.decode(), re.S).group(1)
        urls = re.findall(r"'(/[^']*)'", shell)
        self.assertGreaterEqual(len(urls), 8)
        for url in urls:
            self.assertEqual(c.request("GET", url)[0], 200, f"{url} is pre-cached by the service worker but missing")
        self.assertIn("/api/", sw.decode())  # and it must leave live data alone


class HostingTest(unittest.TestCase):
    """Behaviour that matters once the app sits behind a hosting platform's proxy."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        server.configure(cls.tmp.name)
        server.init_db()
        cls.httpd = server.make_server("127.0.0.1", 0)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        server.RATE.clear()
        server.configure_proxy()

    def tearDown(self):
        server.configure_proxy()

    def login_attempts(self, n, xff):
        return [
            Client(self.port).request("POST", "/api/login", {"username": "ghost", "password": "x"},
                                      headers={"X-Forwarded-For": xff(i)})[0]
            for i in range(n)
        ]

    def signup_cookie(self, username, headers=None):
        status, body, h = Client(self.port).request(
            "POST", "/api/signup", {"username": username, "password": "hunter2hunter2"}, headers=headers)
        self.assertEqual(status, 201, body)
        return h.get_all("Set-Cookie")[0], h

    def test_healthz(self):
        status, body, _ = Client(self.port).request("GET", "/healthz")
        self.assertEqual((status, body), (200, {"ok": True}))
        self.assertEqual(Client(self.port).request("POST", "/healthz")[0], 405)

    def test_forwarded_for_is_ignored_unless_the_proxy_is_trusted(self):
        codes = self.login_attempts(10, lambda i: f"10.0.0.{i}")  # forged headers must not dodge the limit
        self.assertEqual(codes[:8], [401] * 8)
        self.assertEqual(codes[8], 429)

    def test_trusted_proxy_gives_each_visitor_their_own_limit(self):
        server.configure_proxy(trust=True)
        self.assertNotIn(429, self.login_attempts(10, lambda i: f"10.0.0.{i}"))
        server.RATE.clear()
        self.assertEqual(self.login_attempts(10, lambda i: "203.0.113.9")[8], 429)  # the same visitor is still limited

    def test_proxy_hops_pick_the_address_the_proxy_added(self):
        chain = lambda i: "6.6.6.6, 203.0.113.5"  # the left entry is whatever the visitor claimed
        server.configure_proxy(trust=True, hops=1)
        self.login_attempts(1, chain)
        self.assertIn("login:203.0.113.5:ghost", server.RATE)
        self.assertNotIn("login:6.6.6.6:ghost", server.RATE)
        server.RATE.clear()
        server.configure_proxy(trust=True, hops=2)
        self.login_attempts(1, chain)
        self.assertIn("login:6.6.6.6:ghost", server.RATE)

    def test_cookie_is_not_secure_by_default(self):
        cookie, h = self.signup_cookie("plain_one", {"X-Forwarded-Proto": "https"})  # header ignored: not trusted
        self.assertIn("HttpOnly", cookie)
        self.assertNotIn("Secure", cookie)
        self.assertIsNone(h.get("Strict-Transport-Security"))

    def test_cookie_is_secure_behind_an_https_proxy(self):
        server.configure_proxy(trust=True)
        cookie, h = self.signup_cookie("secure_one", {"X-Forwarded-Proto": "https"})
        self.assertIn("; Secure", cookie)
        self.assertIn("max-age", h["Strict-Transport-Security"])
        cookie, h = self.signup_cookie("secure_two")  # no header: plain http, e.g. running locally
        self.assertNotIn("Secure", cookie)
        self.assertIsNone(h.get("Strict-Transport-Security"))

    def test_secure_cookies_can_be_forced(self):
        server.configure_proxy(secure=True)
        cookie, h = self.signup_cookie("forced_one")
        self.assertIn("; Secure", cookie)
        self.assertIn("Strict-Transport-Security", h)

    def test_origin_check_allows_the_public_host_name_only_through_a_trusted_proxy(self):
        body = {"username": "nobody", "password": "x"}
        public = {"Origin": "https://rub.example", "X-Forwarded-Host": "rub.example"}
        self.assertEqual(Client(self.port).request("POST", "/api/login", body, headers=public)[0], 403)
        server.configure_proxy(trust=True)
        self.assertEqual(Client(self.port).request("POST", "/api/login", body, headers=public)[0], 401)  # let through
        forged = {"Origin": "https://evil.example", "X-Forwarded-Host": "rub.example"}
        self.assertEqual(Client(self.port).request("POST", "/api/login", body, headers=forged)[0], 403)


class ConfigTest(unittest.TestCase):
    """Command-line options and the environment variables hosting platforms use."""

    def test_defaults_are_local_only(self):
        a = server.parse_args([], {})
        self.assertEqual((a.host, a.port, a.proxy_hops), ("127.0.0.1", 8000, 1))
        self.assertFalse(any([a.trust_proxy, a.no_seed, a.secure_cookies, a.on_platform]))
        self.assertIsNone(a.data_dir)
        self.assertIsNone(a.run_as_uid)

    def test_platform_environment(self):
        a = server.parse_args([], {
            "PORT": "10000", "RUBRUB_DATA_DIR": "/data", "RUBRUB_TRUST_PROXY": "1", "RUBRUB_SEED": "0",
            "RUBRUB_PROXY_HOPS": "2", "RUBRUB_SECURE_COOKIES": "true", "RUBRUB_RUN_AS_UID": "10001"})
        self.assertEqual((a.host, a.port, a.data_dir), ("0.0.0.0", 10000, "/data"))  # a PORT means "public server"
        self.assertEqual((a.trust_proxy, a.no_seed, a.proxy_hops, a.secure_cookies), (True, True, 2, True))
        self.assertEqual((a.run_as_uid, a.on_platform), (10001, True))

    def test_flags_beat_the_environment(self):
        a = server.parse_args(["--port", "9000", "--host", "127.0.0.1"], {"PORT": "10000", "RUBRUB_HOST": "0.0.0.0"})
        self.assertEqual((a.host, a.port), ("127.0.0.1", 9000))

    def test_bad_values_are_rejected(self):
        for env in ({"PORT": "99999"}, {"PORT": "abc"}, {"RUBRUB_PROXY_HOPS": "0"}):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                server.parse_args([], env)

    def test_env_flag(self):
        for raw, want in (("1", True), ("true", True), ("YES", True), ("on", True), ("0", False), ("no", False), ("", False)):
            self.assertEqual(server.env_flag("X", False, {"X": raw}), want, raw)
        self.assertTrue(server.env_flag("X", True, {}))  # unset or blank falls back to the default
        self.assertTrue(server.env_flag("X", True, {"X": " "}))

    def test_drop_privileges_switches_user_after_fixing_the_data_folder(self):
        from unittest import mock

        old = server.DATA_DIR
        with tempfile.TemporaryDirectory() as tmp:
            try:
                server.configure(tmp)
                calls = []
                fake = lambda name: mock.Mock(side_effect=lambda *a, **k: calls.append((name, a)))
                with mock.patch.object(os, "geteuid", create=True, return_value=0), \
                        mock.patch.object(os, "chown", fake("chown"), create=True), \
                        mock.patch.object(os, "setgroups", fake("setgroups"), create=True), \
                        mock.patch.object(os, "setgid", fake("setgid"), create=True), \
                        mock.patch.object(os, "setuid", fake("setuid"), create=True):
                    server.drop_privileges(10001)
                names = [c[0] for c in calls]
                self.assertEqual(calls[0], ("chown", (server.DATA_DIR, 10001, 10001)))  # data folder first...
                self.assertEqual(names[-3:], ["setgroups", "setgid", "setuid"])  # ...then give up root, user last
            finally:
                server.configure(old)

    def test_drop_privileges_keeps_the_site_up_in_a_restricted_container(self):
        from unittest import mock

        old = server.DATA_DIR
        with tempfile.TemporaryDirectory() as tmp:
            try:
                server.configure(tmp)
                refuse = mock.Mock(side_effect=PermissionError("operation not permitted"))
                out = io.StringIO()
                with mock.patch.object(os, "geteuid", create=True, return_value=0), \
                        mock.patch.object(os, "chown", refuse, create=True), \
                        mock.patch.object(os, "setgroups", refuse, create=True), \
                        mock.patch.object(os, "setgid", refuse, create=True), \
                        mock.patch.object(os, "setuid", refuse, create=True), \
                        contextlib.redirect_stdout(out):
                    server.drop_privileges(10001)  # must warn, not crash on start-up
                self.assertIn("WARNING", out.getvalue())
            finally:
                server.configure(old)

    def test_render_blueprint_matches_the_dockerfile_and_server(self):
        root = Path(__file__).resolve().parent.parent
        blueprint = (root / "render.yaml").read_text(encoding="utf-8")
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("runtime: docker", blueprint)
        self.assertIn("healthCheckPath: /healthz", blueprint)  # the route the server really has
        self.assertRegex(blueprint, r"key: RUBRUB_TRUST_PROXY[^\n]*\n\s+value: \"1\"")
        mount = re.search(r"mountPath: (\S+)", blueprint).group(1)
        self.assertEqual(f"RUBRUB_DATA_DIR={mount}", re.search(r"RUBRUB_DATA_DIR=\S+", dockerfile).group(0))  # disk == data folder
        self.assertRegex(blueprint, r"(?m)^\s+sizeGB: \d+")
        self.assertIsNone(re.search(r"(?m)^\s+plan: free", blueprint))  # the shipped default is the one that keeps data

    def test_drop_privileges_does_nothing_without_root(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("running as root")
        server.drop_privileges(10001)  # must neither raise nor chown anything


class UpgradeTest(unittest.TestCase):
    """A database made before categories / the tester profile existed keeps working."""

    def test_old_database_is_upgraded(self):
        import sqlite3

        old_dir = server.DATA_DIR
        with tempfile.TemporaryDirectory() as tmp:
            try:
                server.configure(tmp)
                old_schema = server.SCHEMA.replace("    category        TEXT NOT NULL DEFAULT 'other',\n", "")
                self.assertNotEqual(old_schema, server.SCHEMA)
                (Path(tmp) / "uploads").mkdir()
                con = sqlite3.connect(Path(tmp) / "rubrub.db")
                con.executescript(old_schema)
                con.execute("INSERT INTO users(id, username, display_name, bio, is_featured, created_at) VALUES(1,'claude','Claude','',1,0)")
                for i, menu in enumerate(["Pad Kra Pao Moo", "Khao Man Gai", "Boat Noodles", "Som Tam"]):
                    con.execute(
                        "INSERT INTO reviews(user_id, restaurant_name, menu_item, rating, price_thb, open_days, open_time, close_time,"
                        " created_at, updated_at) VALUES(1,'Old Cart',?,4,50,'Mon','09:00','17:00',?,?)", (menu, i, i))
                con.commit()
                con.close()

                server.init_db()
                con = server.connect()
                try:
                    cats = [r[0] for r in con.execute("SELECT category FROM reviews ORDER BY id")]
                    self.assertEqual(cats, ["kra_pao", "khao_man_gai", "noodles", "other"])
                    self.assertTrue(server.seed(con))  # existing 'claude' kept, tester + fans added
                    self.assertEqual(con.execute("SELECT COUNT(*) FROM users WHERE username = 'claude'").fetchone()[0], 1)
                    self.assertEqual(con.execute("SELECT COUNT(*) FROM reviews WHERE user_id = 1").fetchone()[0], 4)
                    self.assertEqual(con.execute(
                        "SELECT COUNT(*) FROM reviews r JOIN users u ON u.id = r.user_id WHERE u.username = 'test_blogger'").fetchone()[0], 6)
                    self.assertFalse(server.seed(con))
                finally:
                    con.close()
            finally:
                server.configure(old_dir)


if __name__ == "__main__":
    unittest.main()
