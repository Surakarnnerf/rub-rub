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
import urllib.parse
from http.cookies import SimpleCookie
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server as make_wsgi_server

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402
import rubrub as server  # noqa: E402  (the module used to be called server.py)

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
    """The whole API against the built-in server and SQLite. WsgiAppTest and PostgresAppTest below
    run these very same tests behind the WSGI entry point and on Postgres by overriding the hooks."""

    @classmethod
    def prepare_database(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        server.configure(cls.tmp.name)
        server.init_db()
        con = server.connect()
        server.seed(con)
        con.close()

    @classmethod
    def cleanup_database(cls):
        cls.tmp.cleanup()

    @classmethod
    def start_server(cls):
        cls.httpd = server.make_server("127.0.0.1", 0)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def setUpClass(cls):
        cls.prepare_database()
        cls.start_server()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.cleanup_database()

    def setUp(self):
        server.reset_rate_limits()

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
                fan = con.execute(
                    "INSERT INTO users(username, display_name, created_at) VALUES(?,?,0) RETURNING id", (f"fan_{i:03d}", "fan")
                ).fetchone()[0]
                con.execute("INSERT INTO likes(user_id, review_id) VALUES(?,?)", (fan, rid))
            con.commit()
        finally:
            con.close()
        self.assertTrue(star.request("GET", "/api/users/nina_star")[1]["user"]["is_famous"])
        self.assertIn("nina_star", [u["username"] for u in star.request("GET", "/api/users?limit=20")[1]["users"]])

    def test_sample_catalogue_is_varied_enough_to_show_every_filter(self):
        con = server.connect()
        try:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM reviews WHERE is_sample = 1").fetchone()[0], 22)
            self.assertEqual(con.execute("SELECT COUNT(DISTINCT user_id) FROM reviews WHERE is_sample = 1").fetchone()[0], 10)
            for cat in server.CATEGORIES:
                n = con.execute("SELECT COUNT(*) FROM reviews WHERE is_sample = 1 AND category = ?", (cat,)).fetchone()[0]
                self.assertGreaterEqual(n, 3, cat)
            for column in ("cash_only", "pay_qr", "pay_card"):  # cards are rare at street stalls, so 4 is realistic
                self.assertGreaterEqual(con.execute(f"SELECT COUNT(*) FROM reviews WHERE is_sample = 1 AND {column} = 1").fetchone()[0], 4, column)
            self.assertGreaterEqual(con.execute("SELECT COUNT(DISTINCT rating) FROM reviews WHERE is_sample = 1").fetchone()[0], 2)
        finally:
            con.close()
        c = self.client()
        self.assertEqual(c.request("GET", "/api/users/demo_pim")[1]["user"]["review_count"], 1)
        pim = c.request("GET", "/api/reviews?user=demo_pim")[1]["reviews"][0]
        self.assertEqual((pim["category"], pim["is_sample"], pim["author"]["display_name"]), ("khao_man_gai", True, "Pim"))
        self.assertGreater(pim["likes"], 0)
        famous = [u["username"] for u in c.request("GET", "/api/users?limit=20")[1]["users"]]
        self.assertIn("test_blogger", famous)
        self.assertFalse([u for u in famous if u.startswith("demo_")])  # the extra foodies stay ordinary

    # ---- saved places ------------------------------------------------------
    def test_save_for_later_is_private_and_toggles(self):
        c = self.signup("sam_save")
        first = self.client().request("GET", "/api/reviews?limit=2&sort=top")[1]["reviews"]
        a, b = first[0]["id"], first[1]["id"]
        self.assertEqual(self.client().request("POST", f"/api/reviews/{a}/save")[0], 401)
        self.assertEqual(c.request("GET", "/api/saved")[1], {"reviews": [], "total": 0})
        self.assertEqual(c.request("POST", f"/api/reviews/{a}/save")[1], {"saved": True})
        self.assertEqual(c.request("POST", f"/api/reviews/{b}/save")[1], {"saved": True})
        self.assertTrue(c.request("GET", f"/api/reviews/{a}")[1]["review"]["saved"])
        self.assertFalse(self.client().request("GET", f"/api/reviews/{a}")[1]["review"]["saved"])  # a visitor sees nothing
        status, body, _ = c.request("GET", "/api/saved")
        self.assertEqual((body["total"], [r["id"] for r in body["reviews"]]), (2, [b, a]))  # newest save first
        self.assertTrue(all(r["saved"] for r in body["reviews"]))
        self.assertEqual(c.request("POST", f"/api/reviews/{a}/save")[1], {"saved": False})  # second tap removes it
        self.assertEqual(c.request("GET", "/api/saved")[1]["total"], 1)
        self.assertEqual(c.request("POST", "/api/reviews/999999/save")[0], 404)
        self.assertEqual(self.signup("sam_other").request("GET", "/api/saved")[1]["total"], 0)  # nobody else sees my list

    # ---- collections -------------------------------------------------------
    def test_collection_lifecycle_and_privacy(self):
        owner, other, anon = self.signup("cora_coll"), self.signup("otto_coll"), self.client()
        ids = [r["id"] for r in anon.request("GET", "/api/reviews?limit=3&sort=top")[1]["reviews"]]
        self.assertEqual(anon.request("POST", "/api/collections", {"name": "Nope"})[0], 401)
        status, body, _ = owner.request("POST", "/api/collections", {"name": "  Rainy day   soups ", "description": "Warm bowls", "is_public": True})
        self.assertEqual(status, 201, body)
        coll = body["collection"]
        self.assertEqual((coll["name"], coll["item_count"], coll["owner"]["username"]), ("Rainy day soups", 0, "cora_coll"))
        cid = coll["id"]
        # validation: name rules and one collection per name
        for bad, code in (({"name": ""}, "required"), ({"name": "x"}, "too_short"), ({"name": "y" * 61}, "too_long")):
            status, body, _ = owner.request("POST", "/api/collections", bad)
            self.assertEqual((status, body["fields"]["name"]), (400, code))
        self.assertEqual(owner.request("POST", "/api/collections", {"name": "RAINY DAY SOUPS"})[1]["fields"]["name"], "name_taken")
        self.assertEqual(other.request("POST", "/api/collections", {"name": "Rainy day soups"})[0], 201)  # names are per person
        self.assertEqual(owner.request("POST", "/api/collections", {"name": "fine", "is_public": "maybe"})[1]["fields"]["is_public"], "invalid")
        # adding and removing places
        self.assertEqual(anon.request("POST", f"/api/collections/{cid}/items", {"review_id": ids[0]})[0], 401)
        self.assertEqual(other.request("POST", f"/api/collections/{cid}/items", {"review_id": ids[0]})[0], 403)  # not their collection
        status, body, _ = owner.request("POST", f"/api/collections/{cid}/items", {"review_id": ids[0]})
        self.assertEqual((status, body), (201, {"added": True, "item_count": 1}))
        status, body, _ = owner.request("POST", f"/api/collections/{cid}/items", {"review_id": ids[0]})
        self.assertEqual((status, body), (200, {"added": False, "item_count": 1}))  # already in there
        owner.request("POST", f"/api/collections/{cid}/items", {"review_id": ids[1]})
        self.assertEqual(owner.request("POST", f"/api/collections/{cid}/items", {"review_id": 999999})[0], 404)
        self.assertEqual(owner.request("POST", f"/api/collections/{cid}/items", {"review_id": "x"})[0], 404)
        # a public collection is readable by anyone, with its reviews (newest addition first)
        status, body, _ = anon.request("GET", f"/api/collections/{cid}")
        self.assertEqual((status, body["mine"], [r["id"] for r in body["reviews"]]), (200, False, [ids[1], ids[0]]))
        self.assertEqual(owner.request("GET", f"/api/collections/{cid}")[1]["mine"], True)
        # my collections, with the ones that already hold a given place marked
        mine = owner.request("GET", f"/api/me/collections?review_id={ids[0]}")[1]["collections"]
        self.assertEqual([(c["name"], c["has_review"]) for c in mine], [("Rainy day soups", True)])
        self.assertFalse(owner.request("GET", f"/api/me/collections?review_id={ids[2]}")[1]["collections"][0]["has_review"])
        self.assertEqual(anon.request("GET", "/api/me/collections")[0], 401)
        # making it private hides it from everybody but the owner
        status, body, _ = owner.request("PUT", f"/api/collections/{cid}", {"name": "Rainy day soups", "is_public": False})
        self.assertEqual((status, body["collection"]["is_public"]), (200, False))
        self.assertEqual(other.request("PUT", f"/api/collections/{cid}", {"name": "Hijacked"})[0], 403)
        self.assertEqual(anon.request("GET", f"/api/collections/{cid}")[0], 404)
        self.assertEqual(other.request("GET", f"/api/collections/{cid}")[0], 404)
        self.assertEqual(owner.request("GET", f"/api/collections/{cid}")[0], 200)
        self.assertEqual(anon.request("GET", "/api/users/cora_coll/collections")[1], {"collections": [], "mine": False})
        self.assertEqual(len(owner.request("GET", "/api/users/cora_coll/collections")[1]["collections"]), 1)
        # removing a place, then the whole collection
        status, body, _ = owner.request("DELETE", f"/api/collections/{cid}/items/{ids[0]}")
        self.assertEqual((status, body), (200, {"removed": True, "item_count": 1}))
        self.assertEqual(other.request("DELETE", f"/api/collections/{cid}")[0], 403)
        self.assertEqual(owner.request("DELETE", f"/api/collections/{cid}")[0], 200)
        self.assertEqual(owner.request("GET", f"/api/collections/{cid}")[0], 404)
        self.assertEqual(anon.request("GET", f"/api/reviews/{ids[0]}")[0], 200)  # the reviews themselves are untouched

    def test_deleting_a_review_removes_it_from_saves_and_collections(self):
        author, fan = self.signup("dee_author"), self.signup("fin_fan")
        rid = author.request("POST", "/api/reviews", GOOD_REVIEW)[1]["review"]["id"]
        cid = fan.request("POST", "/api/collections", {"name": "Keepers"})[1]["collection"]["id"]
        fan.request("POST", f"/api/collections/{cid}/items", {"review_id": rid})
        fan.request("POST", f"/api/reviews/{rid}/save")
        self.assertEqual(fan.request("GET", "/api/saved")[1]["total"], 1)
        author.request("DELETE", f"/api/reviews/{rid}")
        self.assertEqual(fan.request("GET", "/api/saved")[1]["total"], 0)
        self.assertEqual(fan.request("GET", f"/api/collections/{cid}")[1]["collection"]["item_count"], 0)

    def test_sample_collections_exist_and_are_filled(self):
        anon = self.client()
        names = {c["name"]: c for c in anon.request("GET", "/api/users/claude/collections")[1]["collections"]}
        self.assertEqual(set(names), {"Noodle crawl", "Cash-only classics", "Late-night bites"})
        self.assertGreaterEqual(names["Noodle crawl"]["item_count"], 5)
        self.assertGreaterEqual(names["Late-night bites"]["item_count"], 2)
        body = anon.request("GET", f"/api/collections/{names['Noodle crawl']['id']}")[1]
        self.assertTrue(all(r["category"] == "noodles" for r in body["reviews"]))
        self.assertEqual([c["name"] for c in anon.request("GET", "/api/users/test_blogger/collections")[1]["collections"]], ["Basil lovers"])

    # ---- cities --------------------------------------------------------------
    def test_cities_filter_and_are_guessed_from_the_area(self):
        c = self.signup("cam_city")
        cities = {x["city"]: x["count"] for x in c.request("GET", "/api/cities")[1]["cities"]}
        self.assertGreaterEqual(cities["bangkok"], 10)
        self.assertEqual(c.request("GET", "/api/cities")[1]["cities"][0]["city"], "bangkok")  # busiest first
        for city in ("chiang_mai", "phuket", "hat_yai", "ayutthaya", "khon_kaen", "chonburi"):
            self.assertGreaterEqual(cities.get(city, 0), 1, city)
        body = c.request("GET", "/api/reviews?city=chiang_mai&limit=50")[1]
        self.assertEqual(body["total"], cities["chiang_mai"])
        self.assertTrue(body["reviews"] and all(r["city"] == "chiang_mai" for r in body["reviews"]))
        self.assertEqual(c.request("GET", "/api/reviews?city=atlantis")[1]["total"], c.request("GET", "/api/reviews")[1]["total"])  # unknown: ignored
        # a new review: city guessed from the area when left out, kept when given, rejected when unknown
        no_city = {k: v for k, v in GOOD_REVIEW.items() if k != "city"}
        self.assertEqual(c.request("POST", "/api/reviews", dict(no_city, area="Nimman, Chiang Mai"))[1]["review"]["city"], "chiang_mai")
        self.assertEqual(c.request("POST", "/api/reviews", dict(no_city, area="Somewhere new"))[1]["review"]["city"], "other")
        self.assertEqual(c.request("POST", "/api/reviews", dict(no_city, area="Hat Yai", city="krabi"))[1]["review"]["city"], "krabi")
        status, body, _ = c.request("POST", "/api/reviews", dict(no_city, city="atlantis"))
        self.assertEqual((status, body["fields"]["city"]), (400, "invalid"))
        self.assertEqual(server.guess_city("ถนนนิมมาน เชียงใหม่"), "chiang_mai")

    # ---- experts -------------------------------------------------------------
    def test_experts_are_labelled_and_filterable(self):
        anon = self.client()
        experts = {e["username"]: e for e in anon.request("GET", "/api/experts")[1]["experts"]}
        self.assertEqual({"claude", "demo_ton", "demo_nok"} <= set(experts), True)
        self.assertEqual((experts["claude"]["expert_title"], experts["claude"]["review_count"]), ("Food blogger", 8))
        self.assertTrue(all(e["is_expert"] for e in experts.values()))
        self.assertFalse(anon.request("GET", "/api/users/test_blogger")[1]["user"]["is_expert"])  # famous, but not an Expert
        reviews = anon.request("GET", "/api/reviews?expert=1&limit=50")[1]
        self.assertTrue(reviews["reviews"] and all(r["author"]["is_expert"] for r in reviews["reviews"]))
        self.assertLess(reviews["total"], anon.request("GET", "/api/reviews")[1]["total"])
        one = anon.request("GET", "/api/reviews?user=demo_ton")[1]["reviews"][0]["author"]
        self.assertEqual((one["is_expert"], one["expert_title"]), (True, "Street food chef"))
        fresh = self.signup("newbie_member")
        self.assertFalse(fresh.request("GET", "/api/me")[1]["user"]["is_expert"])  # nobody can award it to themselves

    def test_set_expert_command_gives_and_takes_the_badge(self):
        self.signup("eve_expert")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(server.set_expert("EVE_EXPERT", "  Noodle   critic ", True), 0)
        self.assertIn("Noodle critic", out.getvalue())
        user = self.client().request("GET", "/api/users/eve_expert")[1]["user"]
        self.assertEqual((user["is_expert"], user["expert_title"]), (True, "Noodle critic"))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(server.set_expert("eve_expert", "", False), 0)
        user = self.client().request("GET", "/api/users/eve_expert")[1]["user"]
        self.assertEqual((user["is_expert"], user["expert_title"]), (False, ""))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(server.set_expert("nobody_here", "x", True), 1)

    # ---- ask the community ---------------------------------------------------
    def ask(self, text, client=None):
        status, body, _ = (client or self.client()).request("GET", "/api/ask?q=" + urllib.parse.quote(text))
        self.assertEqual(status, 200, body)
        return body

    def test_ask_understands_city_and_category(self):
        body = self.ask("best noodles in Chiang Mai")
        self.assertTrue(body["found"])
        self.assertEqual((body["interpretation"]["city"], body["interpretation"]["category"]), ("chiang_mai", "noodles"))
        self.assertTrue(body["results"] and all((r["city"], r["category"]) == ("chiang_mai", "noodles") for r in body["results"]))
        thai = self.ask("ก๋วยเตี๋ยวเชียงใหม่")
        self.assertEqual((thai["interpretation"]["city"], thai["interpretation"]["category"]), ("chiang_mai", "noodles"))
        self.assertTrue(thai["found"])

    def test_ask_understands_price_payment_spice_and_time(self):
        body = self.ask("cheap cash only khao man gai")
        self.assertEqual(body["interpretation"], {"category": "khao_man_gai", "payment": "cash_only", "max_price": 60})
        self.assertTrue(body["found"] and all(r["price_thb"] <= 60 and "cash_only" in r["payments"] for r in body["results"]))
        body = self.ask("spicy kra pao in bangkok under 100 baht")
        self.assertEqual((body["interpretation"]["spice"], body["interpretation"]["max_price"]), ("hot", 100))
        self.assertTrue(body["found"] and all(r["spice"] >= 2 and r["price_thb"] <= 100 and r["city"] == "bangkok" for r in body["results"]))
        self.assertTrue(self.ask("not spicy noodles")["found"])
        self.assertTrue(all(r["spice"] <= 1 for r in self.ask("not spicy noodles")["results"]))
        late = self.ask("late night food")
        self.assertTrue(late["found"] and all(r["close_time"] < r["open_time"] or r["close_time"] >= "22:00" for r in late["results"]))
        early = self.ask("breakfast")
        self.assertTrue(early["found"] and all(r["open_time"] <= "08:00" for r in early["results"]))
        self.assertTrue(self.ask("pay with QR code")["found"])

    def test_every_example_question_on_the_ask_page_gets_a_real_answer(self):
        i18n = (Path(__file__).resolve().parent.parent / "public" / "js" / "i18n.js").read_text(encoding="utf-8")
        english, thai = i18n.split("\n  th: {")
        for block, name in ((english, "English"), (thai, "Thai")):
            examples = re.findall(r'"ask\.ex\d": "([^"]+)"', block)
            self.assertEqual(len(examples), 4, name)
            for question in examples:
                body = self.ask(question)
                self.assertTrue(body["found"] and not body["loosened"], f"{name}: {question!r} -> {body['interpretation']}")
        for city in server.CITIES[:-1]:  # the "Ask about <city>" button on every city page
            english_name = re.search(rf'"city\.{city}": "([^"]+)"', english).group(1)
            thai_name = re.search(rf'"city\.{city}": "([^"]+)"', thai).group(1)
            for question in (f"best street food in {english_name}", f"ร้านเด็ดใน{thai_name}"):
                self.assertEqual(self.ask(question)["interpretation"].get("city"), city, question)

    def test_thai_questions_drop_filler_words_instead_of_searching_for_them(self):
        found = server.interpret_question("ข้าวมันไก่เงินสด ราคาถูก")
        self.assertEqual((found["category"], found["payment"], found["max_price"], found["words"]), ("khao_man_gai", "cash_only", 60, []))
        found = server.interpret_question("ร้านเปิดดึกจากผู้เชี่ยวชาญ")
        self.assertEqual((found["late"], found["expert"], found["words"]), (True, True, []))
        self.assertEqual(server.interpret_question("หมูปิ้งภูเก็ตอร่อย")["words"], ["หมูปิ้ง"])  # a real dish name is kept
        self.assertEqual(server.interpret_question("ข้าวขาหมู")["words"], ["ขาหมู"])  # only the word for rice is dropped
        self.assertEqual(server.interpret_question("best street food in Koh Samui")["words"], [])

    def test_ask_puts_experts_first_and_can_ask_for_experts_only(self):
        body = self.ask("spicy kra pao in bangkok")
        self.assertTrue(body["results"][0]["author"]["is_expert"])  # an Expert's tip leads
        only = self.ask("noodles recommended by experts")
        self.assertTrue(only["found"] and all(r["author"]["is_expert"] for r in only["results"]))
        self.assertLessEqual(len(self.ask("noodles")["results"]), 5)

    def test_ask_uses_other_words_and_loosens_honestly(self):
        moo = self.ask("anything good with moo ping in phuket")
        self.assertTrue(moo["found"] and not moo["loosened"])
        self.assertIn("Moo Ping", moo["results"][0]["menu_item"])
        loose = self.ask("noodles in Chiang Mai with unicorn sauce")
        self.assertTrue(loose["found"] and loose["loosened"])  # nothing matched "unicorn", the clear filters still do

    def test_ask_says_when_it_does_not_know(self):
        body = self.ask("qzxv plugh frobnicate")
        self.assertFalse(body["found"])
        self.assertEqual(body["results"], [])
        self.assertIn("bangkok", body["suggestions"]["cities"])
        self.assertIn("noodles", body["suggestions"]["categories"])
        self.assertFalse(self.ask("noodles in krabi")["found"])  # a real city we have no noodle tip for: no invented answer

    def test_ask_input_limits_and_rate_limit(self):
        c = self.client()
        self.assertEqual(c.request("GET", "/api/ask")[1]["fields"]["q"], "required")
        self.assertEqual(c.request("GET", "/api/ask?q=%20%20")[0], 400)
        self.assertEqual(c.request("GET", "/api/ask?q=" + "x" * 201)[1]["fields"]["q"], "too_long")
        self.assertEqual(self.ask("o'brien \"noodles\" %_ \\ ; --")["found"], True)  # odd characters are just text, never SQL
        for _ in range(60):
            c.request("GET", "/api/ask?q=noodles")
        self.assertEqual(c.request("GET", "/api/ask?q=noodles")[0], 429)

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
        self.assertEqual(data, PNG)  # the photo is stored in the database and comes back byte for byte
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("immutable", headers["Cache-Control"])
        r = c.request("POST", "/api/reviews", dict(GOOD_REVIEW, photo=name))[1]["review"]
        self.assertEqual(r["photo"], f"/uploads/{name}")
        c.request("DELETE", f"/api/reviews/{r['id']}")
        self.assertEqual(self.client().request("GET", f"/uploads/{name}")[0], 404)  # cleaned up

    def test_replacing_a_photo_removes_the_old_one_and_orphans_expire(self):
        c = self.signup("kay_pics")
        up = lambda: c.request("POST", "/api/upload", raw=PNG, headers={"Content-Type": "image/png"})[1]["photo"]
        first, second, forgotten = up(), up(), up()
        rid = c.request("POST", "/api/reviews", dict(GOOD_REVIEW, photo=first))[1]["review"]["id"]
        c.request("PUT", f"/api/reviews/{rid}", dict(GOOD_REVIEW, photo=second))
        anon = self.client()
        self.assertEqual(anon.request("GET", f"/uploads/{first}")[0], 404)  # no longer used by any review
        self.assertEqual(anon.request("GET", f"/uploads/{second}")[0], 200)
        con = server.connect()
        try:
            server.cleanup_orphan_photos(con)  # too fresh: the poster may still be filling in the form
            self.assertEqual(anon.request("GET", f"/uploads/{forgotten}")[0], 200)
            con.execute("UPDATE photos SET created_at = 0 WHERE name = ?", (forgotten,))
            con.commit()
            server.cleanup_orphan_photos(con)
        finally:
            con.close()
        self.assertEqual(anon.request("GET", f"/uploads/{forgotten}")[0], 404)
        self.assertEqual(anon.request("GET", f"/uploads/{second}")[0], 200)  # in use: kept

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
        for evil in ("/../rubrub.py", "/%2e%2e/rubrub.py", "/..%5crubrub.py", "/css/../../rubrub.py", "/uploads/../rubrub.db",
                     "/index.html::$DATA", "/../db.py"):
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
        server.reset_rate_limits()
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
        server.reset_rate_limits()
        self.assertEqual(self.login_attempts(10, lambda i: "203.0.113.9")[8], 429)  # the same visitor is still limited

    def test_proxy_hops_pick_the_address_the_proxy_added(self):
        chain = lambda i: "6.6.6.6, 203.0.113.5"  # the left entry is whatever the visitor claimed
        server.configure_proxy(trust=True, hops=1)
        self.login_attempts(1, chain)
        self.assertIn("login:203.0.113.5:ghost", server.rate_keys())
        self.assertNotIn("login:6.6.6.6:ghost", server.rate_keys())
        server.reset_rate_limits()
        server.configure_proxy(trust=True, hops=2)
        self.login_attempts(1, chain)
        self.assertIn("login:6.6.6.6:ghost", server.rate_keys())

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


class QuietWsgiHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


class WsgiAppTest(AppTest):
    """Every API test again, this time behind the WSGI entry point that Vercel uses
    (served here by wsgiref), including the lazy "cold start" set-up on the first request."""

    @classmethod
    def prepare_database(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        server.configure(cls.tmp.name)
        server.ensure_ready(force=True)  # what the first request on a cold serverless instance does

    @classmethod
    def start_server(cls):
        cls.httpd = make_wsgi_server("127.0.0.1", 0, server.wsgi_app, handler_class=QuietWsgiHandler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    def test_vercel_rewrites_pass_the_real_path_as_rr_path(self):
        c = self.client()
        # vercel.json sends /api/reviews?limit=1 to the function as /api/index?rr_path=/api/reviews&limit=1
        status, body, _ = c.request("GET", "/api/index?rr_path=/api/reviews&limit=1")
        self.assertEqual((status, len(body["reviews"])), (200, 1))
        self.assertGreater(body["total"], 1)  # and the other query parameters survive
        self.assertEqual(c.request("GET", "/api/index?rr_path=/healthz")[1], {"ok": True})
        self.assertEqual(c.request("GET", "/api/index?rr_path=/api/users/claude")[1]["user"]["username"], "claude")
        status, data, h = c.request("GET", "/api/index?rr_path=/uploads/" + "0" * 24 + ".png")
        self.assertEqual(status, 404)  # a well-formed photo name that doesn't exist
        # a rewrite target must stay inside our own routes
        self.assertEqual(c.request("GET", "/api/index?rr_path=/etc/passwd")[0], 404)
        self.assertEqual(c.request("GET", "/api/index")[0], 404)

    def test_rewritten_post_keeps_body_cookies_and_headers(self):
        c = self.client()
        status, body, h = c.request("POST", "/api/index?rr_path=/api/signup", {"username": "vercel_one", "password": "hunter2hunter2"})
        self.assertEqual(status, 201, body)
        self.assertEqual(c.request("GET", "/api/index?rr_path=/api/me")[1]["user"]["username"], "vercel_one")

    def test_database_not_configured_is_a_clear_503(self):
        saved = db.settings()
        try:
            db.configure(sqlite_path=None)  # nothing to connect to, like a Vercel project without a database
            status, body, _ = self.client().request("GET", "/api/me")
            self.assertEqual((status, body["error"]), (503, "database_not_configured"))
            self.assertEqual(self.client().request("GET", "/healthz")[0], 503)
        finally:
            db.configure(*saved)  # exactly what was there before (SQLite or Postgres)

    def test_each_database_problem_has_its_own_code(self):
        """An owner who only sees the site (not the logs) can still tell the causes apart."""
        saved, real_driver = db.settings(), db.psycopg
        try:
            db.configure(sqlite_path=None, postgres_url="postgres://user:secret@db.example/app")
            db.psycopg = None  # the driver could not be imported
            status, body, _ = self.client().request("GET", "/healthz")
            self.assertEqual((status, body["error"]), (503, "database_driver_missing"))
            db.psycopg = FakePsycopg  # driver present, database refuses
            status, body, _ = self.client().request("GET", "/api/me")
            self.assertEqual((status, body["error"]), (503, "database_unreachable"))
            self.assertNotIn("secret", json.dumps(body))  # the reason goes to the log, never to the visitor
            self.assertNotIn("db.example", json.dumps(body))
        finally:
            db.psycopg = real_driver
            db.configure(*saved)


class FakePsycopg:
    """Stands in for psycopg when a test needs the database to refuse a connection."""

    class Error(Exception):
        pass

    @staticmethod
    def connect(*args, **kwargs):
        raise FakePsycopg.Error("connection to server at db.example failed: Connection refused")


@unittest.skipUnless(os.environ.get("RUBRUB_TEST_DATABASE_URL") and db.psycopg,
                     "set RUBRUB_TEST_DATABASE_URL (a DISPOSABLE postgres:// database) and install psycopg to run")
class PostgresAppTest(AppTest):
    """Every API test again on a real Postgres. WARNING: it drops Rub rub's tables in that database."""

    @classmethod
    def prepare_database(cls):
        cls.previous = server.DATA_DIR
        server.use_postgres(os.environ["RUBRUB_TEST_DATABASE_URL"])
        con = server.connect()
        try:
            con.execute("DROP TABLE IF EXISTS " + ", ".join(db.TABLE_NAMES) + " CASCADE")
            con.commit()
        finally:
            con.close()
        server.init_db()
        con = server.connect()
        try:
            server.seed(con)
        finally:
            con.close()

    @classmethod
    def cleanup_database(cls):
        server.configure(cls.previous)  # back to SQLite for the tests that follow

    def test_engine_really_is_postgres(self):
        self.assertEqual(db.engine(), "postgres")
        con = server.connect()
        try:
            self.assertEqual(con.engine, "postgres")
            self.assertIn("PostgreSQL", con.execute("SELECT version()").fetchone()[0])
        finally:
            con.close()


@unittest.skipUnless(os.environ.get("RUBRUB_TEST_DATABASE_URL") and db.psycopg,
                     "set RUBRUB_TEST_DATABASE_URL (a DISPOSABLE postgres:// database) and install psycopg to run")
class PostgresUpgradeTest(unittest.TestCase):
    """A Postgres database made before cities, categories and Experts existed is upgraded in place."""

    def test_old_postgres_tables_get_the_new_columns_and_backfill(self):
        previous = server.DATA_DIR
        try:
            server.use_postgres(os.environ["RUBRUB_TEST_DATABASE_URL"])
            con = server.connect()
            try:
                con.execute("DROP TABLE IF EXISTS " + ", ".join(db.TABLE_NAMES) + " CASCADE")
                con.executescript(db.schema_sql("postgres"))
                con.execute("ALTER TABLE reviews DROP COLUMN city, DROP COLUMN category")  # make them look old
                con.execute("ALTER TABLE users DROP COLUMN is_expert, DROP COLUMN expert_title")
                uid = con.execute("INSERT INTO users(username, display_name, created_at) VALUES('old_one','Old',0) RETURNING id").fetchone()[0]
                for i, (menu, area) in enumerate([("Pad Kra Pao Moo", "Thonglor, Bangkok"), ("Khao Soi", "Nimman, Chiang Mai")]):
                    con.execute(
                        "INSERT INTO reviews(user_id, restaurant_name, area, menu_item, rating, price_thb, open_days, open_time, close_time,"
                        " created_at, updated_at) VALUES(?,'Old Cart',?,?,4,50,'Mon','09:00','17:00',?,?)", (uid, area, menu, i, i))
                con.commit()
                self.assertFalse(db.column_exists(con, "reviews", "city"))
                self.assertTrue(db.column_exists(con, "reviews", "area"))
            finally:
                con.close()
            server.init_db()
            server.init_db()  # and again: nothing to do, nothing breaks
            con = server.connect()
            try:
                self.assertEqual([tuple(r) for r in con.execute("SELECT category, city FROM reviews ORDER BY id")],
                                 [("kra_pao", "bangkok"), ("noodles", "chiang_mai")])
                self.assertEqual(tuple(con.execute("SELECT is_expert, expert_title FROM users").fetchone()), (0, ""))
                self.assertTrue(db.column_exists(con, "reviews", "city"))
            finally:
                con.close()
        finally:
            server.configure(previous)


class PostgresWsgiAppTest(PostgresAppTest, WsgiAppTest):
    """Exactly what runs on Vercel: the WSGI app on Postgres, configured only from Vercel-style
    environment variables (VERCEL=1 and DATABASE_URL), with the lazy cold-start set-up."""

    @classmethod
    def prepare_database(cls):
        from unittest import mock

        cls.previous = server.DATA_DIR
        url = os.environ["RUBRUB_TEST_DATABASE_URL"]
        cls.environ = mock.patch.dict(os.environ, {"VERCEL": "1", "DATABASE_URL": url})
        cls.environ.start()
        server.use_postgres(url)
        con = server.connect()
        try:
            con.execute("DROP TABLE IF EXISTS " + ", ".join(db.TABLE_NAMES) + " CASCADE")
            con.commit()
        finally:
            con.close()
        server.ensure_ready(force=True)  # picks Postgres up from DATABASE_URL, creates the tables, seeds

    @classmethod
    def cleanup_database(cls):
        cls.environ.stop()
        server.configure_proxy()
        server.configure(cls.previous)

    def test_vercel_environment_was_applied(self):
        self.assertTrue(server.TRUST_PROXY)  # Vercel sits in front of the app
        self.assertEqual(db.engine(), "postgres")

    def test_visitor_ip_and_https_come_from_vercels_headers(self):
        c = self.client()
        status, body, h = c.request("POST", "/api/signup", {"username": "edge_user", "password": "hunter2hunter2"},
                                    headers={"X-Forwarded-For": "203.0.113.77", "X-Forwarded-Proto": "https"})
        self.assertEqual(status, 201, body)
        self.assertIn("; Secure", h.get_all("Set-Cookie")[0])
        self.assertIn("signup:203.0.113.77", server.rate_keys())  # limited per real visitor, not per proxy


class DatabaseLayerTest(unittest.TestCase):
    """db.py: the small layer that lets the same SQL run on SQLite and Postgres."""

    def test_postgres_url_comes_from_the_first_matching_variable(self):
        pick = db.postgres_url_from_env
        self.assertIsNone(pick({}))
        self.assertIsNone(pick({"DATABASE_URL": "sqlite:///x.db", "POSTGRES_URL": ""}))  # not a postgres URL
        self.assertEqual(pick({"POSTGRES_URL": "postgres://a/b"}), "postgres://a/b")
        self.assertEqual(pick({"DATABASE_URL": "postgresql://pooled/x", "POSTGRES_URL": "postgres://other/y"}), "postgresql://pooled/x")
        self.assertEqual(pick({"RUBRUB_DATABASE_URL": "postgres://mine/z", "DATABASE_URL": "postgres://o/p"}), "postgres://mine/z")

    def test_postgres_url_under_a_custom_prefix_is_found_on_vercel(self):
        env = {"STORAGE_URL_NON_POOLING": "postgres://direct/db", "STORAGE_URL": "postgres://pooled/db",
               "NEXT_PUBLIC_SITE_URL": "https://example.com", "STORAGE_PRISMA_URL": "postgres://prisma/db"}
        self.assertIsNone(db.postgres_url_from_env(env))  # on a PC only the well-known names count
        self.assertEqual(db.postgres_url_from_env(env, any_name=True), "postgres://pooled/db")  # pooled beats direct
        self.assertEqual(db.postgres_url_from_env({"A_URL_UNPOOLED": "postgresql://u", "B": "postgres://prisma"}, any_name=True),
                         "postgres://prisma")
        self.assertIsNone(db.postgres_url_from_env({"SOME_URL": "https://x", "N": "123"}, any_name=True))
        # a well-known name always wins over a scan
        self.assertEqual(db.postgres_url_from_env({"DATABASE_URL": "postgres://known/db", "AAA_URL": "postgres://other/db"}, any_name=True),
                         "postgres://known/db")

    def test_related_variable_names_never_include_values(self):
        names = db.related_variable_names({"STORAGE_URL": "postgres://user:secret@h/db", "PATH": "x", "NEON_PROJECT_ID": "p"})
        self.assertEqual(names, ["NEON_PROJECT_ID", "STORAGE_URL"])
        described = db.describe_variables({"STORAGE_URL": "postgres://user:secret@h/db", "OTHER_URL": "https://x/y", "PATH": "p"})
        self.assertEqual(described, ["OTHER_URL=other", "STORAGE_URL=postgres-url"])
        self.assertNotIn("secret", " ".join(described))

    def test_address_is_found_inside_quotes_or_a_pasted_psql_command(self):
        pick = lambda value: db.postgres_url_from_env({"DATABASE_URL": value})
        self.assertEqual(pick("postgresql://u:p@h/db?sslmode=require"), "postgresql://u:p@h/db?sslmode=require")
        self.assertEqual(pick('"postgresql://u:p@h/db"'), "postgresql://u:p@h/db")
        self.assertEqual(pick("'postgres://u:p@h/db'"), "postgres://u:p@h/db")
        self.assertEqual(pick("psql 'postgresql://u:p@h/db?sslmode=require'"), "postgresql://u:p@h/db?sslmode=require")
        self.assertIsNone(pick("https://example.com"))
        self.assertIsNone(pick(""))

    def test_address_is_built_from_the_separate_pg_variables_on_vercel_only(self):
        parts = {"PGHOST": "ep-cool-123-pooler.ap-southeast-1.aws.neon.tech", "PGUSER": "neondb_owner",
                 "PGPASSWORD": "p@ss/w:rd#1", "PGDATABASE": "neondb"}
        url = db.postgres_url_from_env(parts, any_name=True)
        self.assertEqual(url, "postgresql://neondb_owner:p%40ss%2Fw%3Ard%231@ep-cool-123-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require")
        self.assertIsNone(db.postgres_url_from_env(parts))  # on a PC the PG* variables are not guessed at
        self.assertIsNone(db.postgres_url_from_env({"PGHOST": "h", "PGUSER": "u"}, any_name=True))  # incomplete
        with_port = db.postgres_url_from_env(dict(parts, PGPORT="6543", PGSSLMODE="verify-full"), any_name=True)
        self.assertIn("@ep-cool-123-pooler.ap-southeast-1.aws.neon.tech:6543/neondb?sslmode=verify-full", with_port)
        # a complete URL variable always beats the assembled one
        self.assertEqual(db.postgres_url_from_env(dict(parts, STORAGE_URL="postgres://real/db"), any_name=True), "postgres://real/db")

    def test_placeholders_become_psycopg_style(self):
        self.assertEqual(db._pg_sql("SELECT 1 WHERE a = ? AND b LIKE '%x' AND c = ?"),
                         "SELECT 1 WHERE a = %s AND b LIKE '%%x' AND c = %s")

    def test_schema_is_filled_in_for_each_engine(self):
        sqlite_sql, pg_sql = db.schema_sql("sqlite"), db.schema_sql("postgres")
        self.assertIn("INTEGER PRIMARY KEY AUTOINCREMENT", sqlite_sql)
        self.assertIn("BLOB", sqlite_sql)
        self.assertIn("BIGSERIAL PRIMARY KEY", pg_sql)
        self.assertIn("BYTEA", pg_sql)
        for sql in (sqlite_sql, pg_sql):
            self.assertNotRegex(sql, r"[{}]")  # every placeholder was substituted
            for table in db.TABLE_NAMES:  # the cleanup list and the schema must never drift apart
                self.assertIn(f"CREATE TABLE IF NOT EXISTS {table} ", sql)
            self.assertEqual(sql.count("CREATE TABLE IF NOT EXISTS"), len(db.TABLE_NAMES))

    def test_row_behaves_like_sqlite3_row(self):
        row = db.Row({"id": 0, "name": 1}, (7, "x"))
        self.assertEqual((row["id"], row[0], row["name"], row[1], len(row), list(row), row.keys()), (7, 7, "x", "x", 2, [7, "x"], ["id", "name"]))
        self.assertEqual(row[0:2], (7, "x"))
        with self.assertRaises(KeyError):
            row["nope"]

    def test_meta_round_trip_and_upsert(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = server.DATA_DIR
            try:
                server.configure(tmp)
                server.init_db()
                con = server.connect()
                try:
                    self.assertIsNone(db.get_meta(con, "k"))
                    db.set_meta(con, "k", 1)
                    db.set_meta(con, "k", 2)  # second write replaces the first
                    self.assertEqual(db.get_meta(con, "k"), "2")
                finally:
                    con.close()
            finally:
                server.configure(old)

    def test_connect_errors_carry_codes_and_explain_themselves_in_the_log(self):
        saved, real_driver, real_error = db.settings(), db.psycopg, db.psycopg_import_error
        try:
            db.configure(sqlite_path=None, postgres_url="postgres://u:p@h/d")
            db.psycopg, db.psycopg_import_error = None, "no pq wrapper available"
            with self.assertRaises(db.DriverMissing) as ctx:
                db.connect()
            self.assertEqual(ctx.exception.code, "database_driver_missing")
            self.assertIn("no pq wrapper available", str(ctx.exception))  # why the import failed
            self.assertIsInstance(ctx.exception, db.DatabaseNotConfigured)  # old handlers still catch it
            db.psycopg = FakePsycopg
            with self.assertRaises(db.DatabaseUnavailable) as ctx:
                db.connect()
            self.assertEqual(ctx.exception.code, "database_unreachable")
            self.assertIn("Connection refused", str(ctx.exception))
            self.assertEqual(db.DatabaseNotConfigured.code, "database_not_configured")
        finally:
            db.psycopg, db.psycopg_import_error = real_driver, real_error
            db.configure(*saved)

    def test_unconfigured_database_raises_a_clear_error(self):
        old = server.DATA_DIR
        try:
            db.configure(sqlite_path=None)
            with self.assertRaises(db.DatabaseNotConfigured):
                db.connect()
        finally:
            server.configure(old)


class VercelEnvironmentTest(unittest.TestCase):
    """How the app configures itself when it finds Vercel's environment."""

    def tearDown(self):
        server.configure_proxy()
        server.configure(server.DATA_DIR)  # back to SQLite

    def test_vercel_without_a_database_says_what_to_do(self):
        with self.assertRaises(db.DatabaseNotConfigured) as ctx:
            server.apply_environment({"VERCEL": "1"})
        self.assertIn("Postgres", str(ctx.exception))

    def test_vercel_finds_a_database_whatever_prefix_the_integration_used(self):
        server.apply_environment({"VERCEL": "1", "STORAGE_URL": "postgres://u:p@host/db?sslmode=require"})
        self.assertEqual(db.engine(), "postgres")

    def test_a_site_without_a_database_address_tells_its_owner_which_settings_it_saw(self):
        """The 503 lists the NAMES of related settings, so the owner can see what is missing without logs."""
        from unittest import mock

        def call(path):
            out = {}
            environ = {"REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": "", "wsgi.input": io.BytesIO()}
            body = b"".join(server.wsgi_app(environ, lambda status, headers: out.update(status=status)))
            return out["status"], json.loads(body)

        old_ready = server._ready
        server._ready = False  # a cold start
        try:
            fake_vercel = {"VERCEL": "1", "STORAGE_URL": "https://secret.example/token123", "PGHOST": "h"}
            with mock.patch.dict(os.environ, fake_vercel, clear=True):  # isolated from this PC's own settings
                status, body = call("/healthz")
        finally:
            server._ready = old_ready
        self.assertTrue(status.startswith("503"), status)
        self.assertEqual(body["error"], "database_not_configured")
        self.assertEqual(body["variables_seen"], ["PGHOST=other", "STORAGE_URL=other"])
        self.assertNotIn("token123", json.dumps(body))
        self.assertNotIn("secret.example", json.dumps(body))

    def test_the_error_names_nearby_variables_but_never_leaks_values(self):
        with self.assertRaises(db.DatabaseNotConfigured) as ctx:
            server.apply_environment({"VERCEL": "1", "SOME_API_URL": "https://secret.example/token123"})
        message = str(ctx.exception)
        self.assertIn("SOME_API_URL", message)
        self.assertIn("REDEPLOY", message)
        self.assertNotIn("token123", message)

    def test_vercel_with_a_database_trusts_the_proxy_and_uses_postgres(self):
        server.apply_environment({"VERCEL": "1", "POSTGRES_URL": "postgres://u:p@host/db?sslmode=require"})
        self.assertEqual(db.engine(), "postgres")
        self.assertTrue(server.TRUST_PROXY)  # Vercel always sits in front and overwrites X-Forwarded-For
        self.assertEqual(server.PROXY_HOPS, 1)

    def test_plain_environment_changes_nothing(self):
        server.configure_proxy()
        server.apply_environment({})
        self.assertEqual(db.engine(), "sqlite")
        self.assertFalse(server.TRUST_PROXY)

    def test_explicit_settings_win_over_vercel_defaults(self):
        server.apply_environment({"VERCEL": "1", "DATABASE_URL": "postgres://h/d", "RUBRUB_TRUST_PROXY": "0", "RUBRUB_PROXY_HOPS": "2"})
        self.assertFalse(server.TRUST_PROXY)
        self.assertEqual(server.PROXY_HOPS, 2)


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


class DeployFilesTest(unittest.TestCase):
    """The deployment files must agree with the code (a typo here only shows up after deploying)."""

    root = Path(__file__).resolve().parent.parent

    def read(self, name):
        return (self.root / name).read_text(encoding="utf-8")

    def test_vercel_config_matches_the_app(self):
        cfg = json.loads(self.read("vercel.json"))
        self.assertEqual(cfg["outputDirectory"], "public")  # the CDN serves the same folder the built-in server does
        self.assertEqual(server.STATIC_DIR, self.root / "public")
        self.assertTrue((self.root / "public" / "index.html").is_file())
        self.assertIn("sin1", cfg["regions"])
        sources = {r["source"] for r in cfg["rewrites"]}
        self.assertEqual(sources, {"/api/:path*", "/uploads/:name", "/healthz"})  # every dynamic route of the app
        for rule in cfg["rewrites"]:
            dest, _, real = rule["destination"].partition("?rr_path=")
            self.assertEqual(dest, "/api/index")  # the one function
            self.assertTrue(real.startswith(server.API_PREFIXES), real)  # request_target() accepts the real path
            self.assertIn({"type": "query", "key": "rr_path"}, rule["missing"])  # never rewrite a rewrite (no loops)
        self.assertTrue((self.root / "api" / "index.py").is_file())

    def test_vercel_security_headers_match_the_ones_the_server_sends(self):
        rule = json.loads(self.read("vercel.json"))["headers"][0]
        self.assertEqual({h["key"]: h["value"] for h in rule["headers"]}, server.SECURITY_HEADERS)
        pattern = re.compile(rule["source"])  # static files get them, the function adds its own
        for path in ("/", "/js/app.js", "/sw.js", "/icons/icon-192.png"):
            self.assertTrue(pattern.fullmatch(path), path)
        for path in ("/api/me", "/uploads/x.png", "/healthz"):
            self.assertFalse(pattern.fullmatch(path), path)

    def test_vercel_function_is_the_wsgi_app(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("vercel_entry_point", self.root / "api" / "index.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertIs(module.app, server.wsgi_app)
        exclude = json.loads(self.read("vercel.json"))["functions"]["api/index.py"]["excludeFiles"]
        for needed in ("rubrub.py", "db.py", "api/"):
            self.assertNotIn(needed, exclude)  # never leave out what the function imports
        self.assertIn("psycopg", self.read("requirements.txt"))

    def test_every_english_text_has_a_thai_twin_and_every_error_code_a_message(self):
        source = self.read("public/js/i18n.js")
        english, thai = source.split("\n  th: {")
        keys = lambda text: re.findall(r'(?:^[ \t]*|,[ \t]*)"?([A-Za-z][\w.]*)"?[ \t]*:[ \t]*"', text, re.M)
        en_keys, th_keys = keys(english), keys(thai)
        self.assertGreater(len(en_keys), 200)  # the pattern really found the strings
        self.assertEqual(sorted(set(en_keys) - set(th_keys)), [], "missing in Thai")
        self.assertEqual(sorted(set(th_keys) - set(en_keys)), [], "only in Thai")
        self.assertEqual(len(en_keys), len(set(en_keys)))  # no duplicated key hiding another
        # every error code the server can answer with has a message the page can show
        codes = set(re.findall(r'ApiError\(\d+, "(\w+)"', self.read("rubrub.py")))
        codes |= set(re.findall(r'\{"\w+": "(\w+)"\}', self.read("rubrub.py")))  # field errors such as {"name": "name_taken"}
        codes |= {db.DatabaseNotConfigured.code, db.DriverMissing.code, db.DatabaseUnavailable.code, "server_error"}
        for code in codes - {"validation"}:
            self.assertIn(f"err.{code}", en_keys, f"no message for error code {code}")

    def test_every_text_the_page_asks_for_exists(self):
        """app.js looks texts up by key. A typo would show the raw key to visitors, so check them all."""
        js, i18n = self.read("public/js/app.js"), self.read("public/js/i18n.js")
        english = i18n.split("\n  th: {")[0]
        have = set(re.findall(r'(?:^[ \t]*|,[ \t]*)"?([A-Za-z][\w.]*)"?[ \t]*:[ \t]*"', english, re.M))
        used = set(re.findall(r"""\bt\('([A-Za-z][\w.]*)'""", js)) | set(re.findall(r"""askLogin\('([\w.]+)'\)""", js))
        used = {key for key in used if not key.endswith(".")}  # t('cat.' + x) builds its key: checked below
        self.assertGreater(len(used), 150)  # the pattern really found the lookups
        self.assertEqual(sorted(used - have), [], "texts used by app.js that do not exist")
        dynamic = ([f"cat.{c}" for c in server.CATEGORIES] + [f"city.{c}" for c in server.CITIES]
                   + [f"pay.{p}" for p in server.PAYMENTS] + [f"rate.{n}" for n in range(1, 6)] + [f"spice.{n}" for n in range(4)]
                   + [f"day.{d}" for d in server.DAYS] + [f"dayFull.{d}" for d in server.DAYS])
        self.assertEqual(sorted(set(dynamic) - have), [], "texts built from a prefix that do not exist")

    def test_city_and_category_words_match_between_python_and_javascript(self):
        js = self.read("public/js/app.js")
        for table, name in ((server.CITY_KEYWORDS, "CITY_WORDS"), (server.CATEGORY_KEYWORDS, "CAT_WORDS")):
            block = js.split(f"const {name} = ")[1].split("function ")[0]
            for key, words in table:
                self.assertIn(f"'{key}'", block, key)
                for word in words:
                    self.assertIn(f"'{word}'", block, f"{word} ({key}) is in rubrub.py but not in app.js")
        self.assertEqual([c for c in server.CITIES if c != "other"], [k for k, _ in server.CITY_KEYWORDS])
        self.assertIn("const CITIES = [" + ", ".join(f"'{c}'" for c in server.CITIES) + "]", js)

    def test_dockerfile_copies_the_files_the_app_needs(self):
        dockerfile, ignore = self.read("Dockerfile"), self.read(".dockerignore").split()
        self.assertIn("COPY rubrub.py db.py ./", dockerfile)
        self.assertIn("COPY public ./public", dockerfile)
        self.assertIn('CMD ["python", "rubrub.py"]', dockerfile)
        for needed in ("rubrub.py", "db.py", "public"):
            self.assertNotIn(needed, ignore)
        self.assertTrue((self.root / "start.bat").read_text().count("rubrub.py") == 1)


class UpgradeTest(unittest.TestCase):
    """A database made before categories / the tester profile existed keeps working."""

    def test_old_database_is_upgraded(self):
        import sqlite3

        old_dir = server.DATA_DIR
        with tempfile.TemporaryDirectory() as tmp:
            try:
                server.configure(tmp)
                current = db.schema_sql("sqlite")
                old_schema = current
                for line in ("    category        TEXT NOT NULL DEFAULT 'other',\n", "    city            TEXT NOT NULL DEFAULT 'other',\n",
                             "    is_expert    INTEGER NOT NULL DEFAULT 0,\n", "    expert_title TEXT NOT NULL DEFAULT '',\n"):
                    self.assertIn(line, old_schema)
                    old_schema = old_schema.replace(line, "")  # what the tables looked like before these columns existed
                self.assertNotEqual(old_schema, current)
                con = sqlite3.connect(Path(tmp) / "rubrub.db")
                con.executescript(old_schema)
                con.execute("INSERT INTO users(id, username, display_name, bio, is_featured, created_at) VALUES(1,'claude','Claude','',1,0)")
                for i, (menu, area) in enumerate([("Pad Kra Pao Moo", "Ari, Bangkok"), ("Khao Man Gai", "Nimman, Chiang Mai"),
                                                  ("Boat Noodles", "Phuket Old Town"), ("Som Tam", "")]):
                    con.execute(
                        "INSERT INTO reviews(user_id, restaurant_name, area, menu_item, rating, price_thb, open_days, open_time, close_time,"
                        " created_at, updated_at) VALUES(1,'Old Cart',?,?,4,50,'Mon','09:00','17:00',?,?)", (area, menu, i, i))
                con.commit()
                con.close()

                server.init_db()
                con = server.connect()
                try:
                    cats = [r[0] for r in con.execute("SELECT category FROM reviews ORDER BY id")]
                    self.assertEqual(cats, ["kra_pao", "khao_man_gai", "noodles", "other"])
                    cities = [r[0] for r in con.execute("SELECT city FROM reviews ORDER BY id")]
                    self.assertEqual(cities, ["bangkok", "chiang_mai", "phuket", "other"])
                    self.assertEqual(con.execute("SELECT is_expert, expert_title FROM users").fetchone()[:], (0, ""))
                    server.init_db()  # running it again changes nothing and does not fail
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

    def test_database_seeded_before_the_extra_reviews_gets_just_those(self):
        old_dir = server.DATA_DIR
        with tempfile.TemporaryDirectory() as tmp:
            try:
                server.configure(tmp)
                server.init_db()
                con = server.connect()
                try:
                    count = lambda sql: con.execute(sql).fetchone()[0]
                    self.assertTrue(server.seed(con))
                    self.assertEqual(count("SELECT COUNT(*) FROM reviews"), 22)
                    # make it look like a site that was seeded when only Claude and Test Eater existed
                    con.execute("DELETE FROM reviews WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'demo_%')")
                    db.set_meta(con, "seed_version", 2)
                    con.commit()
                    self.assertEqual(count("SELECT COUNT(*) FROM reviews"), 14)
                    self.assertTrue(server.seed(con))  # the next start adds only the new part
                    self.assertEqual(count("SELECT COUNT(*) FROM reviews"), 22)
                    self.assertEqual(count("SELECT COUNT(*) FROM reviews r JOIN users u ON u.id = r.user_id WHERE u.username = 'claude'"), 8)
                    self.assertEqual(count("SELECT COUNT(*) FROM users WHERE username = 'claude'"), 1)
                    self.assertEqual(count("SELECT COUNT(*) FROM users WHERE username LIKE 'demo_%'"), 12)  # no duplicate foodies
                    self.assertFalse(server.seed(con))  # and never again
                    self.assertEqual(count("SELECT COUNT(*) FROM reviews"), 22)
                    self.assertEqual(db.get_meta(con, "seed_version"), str(server.SEED_VERSION))
                finally:
                    con.close()
            finally:
                server.configure(old_dir)

    def test_seed_marker_from_the_old_file_header_is_respected(self):
        """Before the meta table existed the seed version lived in SQLite's user_version."""
        import sqlite3

        old_dir = server.DATA_DIR
        with tempfile.TemporaryDirectory() as tmp:
            try:
                server.configure(tmp)
                server.init_db()
                raw = sqlite3.connect(Path(tmp) / "rubrub.db")
                raw.execute(f"PRAGMA user_version = {server.SEED_VERSION}")
                raw.commit()
                raw.close()
                con = server.connect()
                try:
                    self.assertEqual(server.seed_version(con), server.SEED_VERSION)
                    self.assertFalse(server.seed(con))  # already seeded in the past: stay out of the way
                    self.assertEqual(con.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
                finally:
                    con.close()
            finally:
                server.configure(old_dir)


if __name__ == "__main__":
    unittest.main()
