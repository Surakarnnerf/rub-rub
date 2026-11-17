# Rub rub 🌶️ — Thai street food reviews

A warm, spicy review site where anyone can make an account and review Thai street food.
Reviews are posted with a guided form: restaurant name, menu, rating, price, payment
(cash only / QR code / credit card) and opening days + hours. The interface switches
between English and Thai, and a featured **Claude, Famous food blogger** profile is
pinned on the home page as "Chef's Pick".

No installs: only **Python 3.9+** (standard library). No npm, no pip.

## Run it

```bash
python server.py
```

Then open <http://localhost:8000> (or double-click `start.bat` on Windows).

| Option | What it does |
| --- | --- |
| `--port 9000` | use another port |
| `--host 0.0.0.0` | let other devices on your Wi-Fi/LAN open the site (no HTTPS) |
| `--data-dir PATH` | keep the database and photos somewhere else (default `./data`) |
| `--no-seed` | don't create the sample Claude blogger and demo reviews |
| `--reset-password NAME` | set a new password for a user, then exit (also your "forgot password" tool) |

Hosting options (`--trust-proxy`, `--proxy-hops`, `--secure-cookies`, `--run-as-uid`) are
described in [Deploy](#deploy-to-a-real-host). Every option can also be set with an
environment variable.

### Sample profiles
On first run two demo profiles are created. Both are locked until you give them a password:

| Profile | What it shows | Log in as it |
| --- | --- | --- |
| `claude` | the featured **Famous food blogger**, 8 reviews, "Chef's Pick" on the home page | `python server.py --reset-password claude` |
| `test_blogger` ("Test Eater") | a **Famous reviewer**: 6 reviews with 52 likes from 12 fictional "demo_…" fans | `python server.py --reset-password test_blogger` |

All the sample reviews are demo data: the vendors in them are **fictional** and are
labelled "Sample" in the app. To start clean, delete the `data` folder before you launch
for real, or run with `--no-seed` on a fresh `data` folder. Demo profiles are only added
once; deleting a sample review never brings it back.

### How people become famous
Anyone whose reviews collect **25 likes from other people** (`FAMOUS_LIKES` in `server.py`)
earns the **Famous reviewer** badge, which appears on their profile, next to their name on
every review, and in the "Famous reviewers" row on the home page. You can't like your own
reviews.

### Food categories
Every review has a category: **ข้าวมันไก่ (Khao Man Gai) · ข้าวกระเพรา (Pad Kra Pao) ·
ก๋วยเตี๋ยว (Noodles) · อื่นๆ (Other)**. The category chips sit under the search boxes
(hero and feed) and filter the list. In the review form the category is guessed from the
menu name (English or Thai) and can be changed. Reviews written before categories existed
are upgraded automatically the next time the server starts.

## How it works

```
server.py            API + static file server + SQLite (stdlib only)
static/              single-page front end (HTML, CSS, vanilla JS)
  js/app.js          router, pages, review form
  js/i18n.js         English / Thai text
  css/style.css      the warm, spicy theme
data/rubrub.db       users, sessions, reviews, likes (created on first run)
data/uploads/        review photos (resized in the browser before upload)
tests/test_api.py    API, hosting and configuration tests
Dockerfile           container image for hosting platforms
```

- **Accounts:** sign-up with username + password (min 8 chars). Passwords are hashed with
  scrypt; sessions use random tokens stored hashed in an `HttpOnly`, `SameSite=Lax` cookie.
- **Review form:** every field is validated in the browser *and* on the server. Typing the
  name of a restaurant that was reviewed before fills in its area, payment and hours.
  "Cash only" can't be combined with QR/card; overnight hours (18:00 → 02:00) are supported.
- **Open now / Closed now** badges are calculated in Thailand time (UTC+7).
- Only the author can edit or delete a review. Anyone logged in can like other people's reviews.

## Tests

```bash
python -m unittest discover tests -v
```

## Deploy to a real host

### Why not Vercel (or Netlify, GitHub Pages…)?
Those run static files and short-lived serverless functions. Rub rub is a long-running server
that keeps its accounts, reviews and photos in a SQLite file and an uploads folder, so it needs
a host that **runs a process and keeps a disk**: Render, Railway, Fly.io, a VPS, or your own PC.

### Steps (any host that can build a Dockerfile)
1. Create a **web service from this GitHub repo**; the `Dockerfile` does the build (there is
   nothing to install).
2. Attach a **persistent disk / volume mounted at `/data`** (1 GB is plenty). Without it every
   restart or redeploy wipes the database. Many free plans don't include one, so check your
   host's current terms.
3. Set the environment variable **`RUBRUB_TRUST_PROXY=1`**. This makes the app read the
   visitor's real IP and HTTPS from the platform's proxy, so rate limits work per visitor and
   the login cookie is marked `Secure`.
4. Use **`/healthz`** as the health-check path (it returns `{"ok": true}` when the database opens).
5. Run **exactly one instance**. SQLite and the in-memory rate limits don't work across several.

HTTPS comes from the host. The platform sets `PORT` itself and the app listens on it.

### Environment variables
| Variable | Default | Meaning |
| --- | --- | --- |
| `PORT` | `8000` | port to listen on; when set, the app binds `0.0.0.0` |
| `RUBRUB_DATA_DIR` | `./data` (`/data` in the Docker image) | database and photos; point it at the persistent disk |
| `RUBRUB_TRUST_PROXY` | off | **set to `1` behind a hosting proxy**; leave off when clients connect directly, because they could forge the headers |
| `RUBRUB_PROXY_HOPS` | `1` | number of proxies in front of the app (e.g. `2` for Cloudflare + the host) |
| `RUBRUB_SECURE_COOKIES` | off | always mark the login cookie `Secure`, e.g. when your proxy doesn't send `X-Forwarded-Proto` |
| `RUBRUB_SEED` | on | `0` = don't add the fictional sample profiles and reviews |
| `RUBRUB_HOST` | `127.0.0.1` (`0.0.0.0` in Docker or when `PORT` is set) | interface to bind |
| `RUBRUB_RUN_AS_UID` | `10001` in Docker | if started as root, fix `RUBRUB_DATA_DIR` ownership then switch to this user |

### Try the image on your own PC (needs Docker)
```bash
docker build -t rub-rub .
docker run --rm -p 8000:8000 -v rubrub-data:/data rub-rub
```

### After it's live
- **Log in as `claude` or `test_blogger`:** open a shell on the service (your host's console
  or SSH) and run `python server.py --reset-password claude`.
- **Don't want the fictional samples on a public site?** Set `RUBRUB_SEED=0` before the first start.
- **Backups:** copy the `/data` folder regularly (the host may offer disk snapshots).
- **Still missing for a big public site:** email-based password reset, moderation tools and
  a limit on fake accounts liking each other's reviews.
