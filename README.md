# Rub rub 🌶️ — Thai street food reviews

A warm, spicy review site where anyone can make an account and review Thai street food.
Reviews are posted with a guided form: restaurant name, menu, rating, price, payment
(cash only / QR code / credit card) and opening days + hours. The interface switches
between English and Thai, and a featured **Claude, Famous food blogger** profile is
pinned on the home page as "Chef's Pick".

It runs three ways, from the same code:

| Where | Database | Needs |
| --- | --- | --- |
| **Your own PC** | a SQLite file | only Python 3.9+ (no pip, no npm) |
| **Vercel** (free tier, phone-ready link) | Postgres (Neon) | a Vercel and a Neon account, see [Deploy on Vercel](#deploy-on-vercel) |
| **Render, Railway, Fly.io, a VPS** | a SQLite file on a persistent disk | Docker, see [Deploy with Docker](#deploy-with-docker-render-railway-flyio) |

## Run it on your PC

```bash
python rubrub.py
```

Then open <http://localhost:8000> (or double-click `start.bat` on Windows).

| Option | What it does |
| --- | --- |
| `--port 9000` | use another port |
| `--host 0.0.0.0` | let other devices on your Wi-Fi/LAN open the site (no HTTPS) |
| `--data-dir PATH` | keep the SQLite file somewhere else (default `./data`) |
| `--database-url URL` | use a Postgres database instead (needs `pip install "psycopg[binary]"`) |
| `--no-seed` | don't create the sample Claude blogger and demo reviews |
| `--reset-password NAME` | set a new password for a user, then exit (also your "forgot password" tool) |

Hosting options (`--trust-proxy`, `--proxy-hops`, `--secure-cookies`, `--run-as-uid`) and the
matching environment variables are listed under [Environment variables](#environment-variables).

### Sample profiles
On first run two demo profiles are created. Both are locked until you give them a password:

| Profile | What it shows | Log in as it |
| --- | --- | --- |
| `claude` | the featured **Famous food blogger**, 8 reviews, "Chef's Pick" on the home page | `python rubrub.py --reset-password claude` |
| `test_blogger` ("Test Eater") | a **Famous reviewer**: 6 reviews with 52 likes from 12 fictional "demo_…" fans | `python rubrub.py --reset-password test_blogger` |

All the sample reviews are demo data: the vendors in them are **fictional** and are
labelled "Sample" in the app. To start clean, delete the `data` folder before you launch
for real, or run with `--no-seed` on a fresh database. Demo profiles are only added once;
deleting a sample review never brings it back.

### How people become famous
Anyone whose reviews collect **25 likes from other people** (`FAMOUS_LIKES` in `rubrub.py`)
earns the **Famous reviewer** badge, which appears on their profile, next to their name on
every review, and in the "Famous reviewers" row on the home page. You can't like your own
reviews.

### Food categories
Every review has a category: **ข้าวมันไก่ (Khao Man Gai) · ข้าวกระเพรา (Pad Kra Pao) ·
ก๋วยเตี๋ยว (Noodles) · อื่นๆ (Other)**. The category chips sit under the search boxes
(hero and feed) and filter the list. In the review form the category is guessed from the
menu name (English or Thai) and can be changed. Reviews written before categories existed
are upgraded automatically the next time the server starts.

## Use it as a phone app

Rub rub is an installable web app (a PWA): same website, but with its own home-screen icon,
full-screen window without the browser bar, an app-style bottom tab bar (Home · Write · Me)
and an app shell that still opens when the phone has no signal (reviews themselves need a
connection).

| Phone | How to install |
| --- | --- |
| **Android** (Chrome, Edge, Samsung Internet) | tap **Install** on the "Get the Rub rub app" card on the home page, or the browser menu → **Install app** / **Add to Home screen** |
| **iPhone / iPad** (Safari) | **Share** button → **Add to Home Screen** (iOS has no install button for websites; the card on the home page and the "Install the app" footer link explain this) |

**It must be opened over HTTPS** (any hosted site below) or on `localhost`. On a plain
`http://192.168…` address phones only allow a simple shortcut, with no offline support.

Not included: a listing in Google Play or the App Store. That needs a native wrapper
(Capacitor, or a Trusted Web Activity on Android) and paid developer accounts.

The app files are `public/manifest.webmanifest` (name, colours, icons), `public/sw.js`
(offline cache; it never caches `/api/`, bump `VERSION` there if you change its rules),
`public/js/pwa.js` (install prompt) and `public/icons/`.

## How it works

```
rubrub.py            the app: API, accounts, validation, seed data, built-in server, WSGI entry point
db.py                database layer: the same SQL on SQLite and Postgres
api/index.py         Vercel function (hands requests to rubrub.wsgi_app)
vercel.json          Vercel routing, region and security headers
requirements.txt     psycopg, only needed for Postgres (Vercel)
public/              single-page front end (HTML, CSS, vanilla JS), served as static files
  js/app.js          router, pages, review form
  js/i18n.js         English / Thai text
  js/pwa.js          "install as an app" helpers, registers the service worker
  sw.js              service worker (offline app shell)
  manifest.webmanifest, icons/   home-screen name, colours and icons
  css/style.css      the warm, spicy theme
data/rubrub.db       SQLite file (created on first run; not used on Vercel)
Dockerfile, render.yaml   container image and one-click Render blueprint
tests/test_api.py    tests
```

- **Accounts:** sign-up with username + password (min 8 chars). Passwords are hashed with
  scrypt; sessions use random tokens stored hashed in an `HttpOnly`, `SameSite=Lax` cookie.
- **Everything lives in the database**, including review photos (resized in the browser
  before upload) and the sign-up/login rate limits, so several server instances can share it.
- **Review form:** every field is validated in the browser *and* on the server. Typing the
  name of a restaurant that was reviewed before fills in its area, payment and hours.
  "Cash only" can't be combined with QR/card; overnight hours (18:00 → 02:00) are supported.
- **Open now / Closed now** badges are calculated in Thailand time (UTC+7).
- Only the author can edit or delete a review. Anyone logged in can like other people's reviews.

## Tests

```bash
python -m unittest discover tests -v
```

This runs the whole API on SQLite, both behind the built-in server and behind the WSGI entry
point that Vercel uses. To also run it on a real Postgres, point `RUBRUB_TEST_DATABASE_URL` at a
**disposable** database (the tests drop Rub rub's tables in it) after `pip install "psycopg[binary]"`.

## Deploy on Vercel

Vercel serves the files in `public/` from its CDN and runs the API as one short-lived Python
function (`api/index.py`). It has no disk, so the accounts, reviews and photos live in a hosted
**Postgres** database that you add to the project (Neon, with a free plan).

1. **Import the project.** On [vercel.com](https://vercel.com) sign in with GitHub, choose
   **Add New… → Project**, pick `rub-rub` and press **Deploy**. Leave the settings alone:
   `vercel.json` already sets everything (no build step, Singapore region `sin1`).
   The first deploy works but shows "The site's database isn't connected yet" until step 2.
2. **Add the database.** Open the project → **Storage** → **Create Database** → **Neon**
   (Postgres), choose the **Free** plan and the **Singapore** region (it should match the
   function's region, otherwise every request waits for a trip across the ocean), and connect
   it to the project for all environments. Vercel adds `DATABASE_URL` and friends by itself.
3. **Redeploy** (Deployments → the latest one → ⋯ → Redeploy), because environment variables
   only reach new deployments.
4. Open your link on a phone. The first request creates the tables and the sample profiles
   (a few seconds), after that it is fast. Every `git push` to `main` redeploys.

Things that behave differently on Vercel:
- **Photos are stored in the database** (about 100–300 KB each after the browser shrinks them).
  Check your database plan's storage limit if you expect many photos.
- A request body can be at most 4.5 MB (photos are capped at 3 MB) and a request may run for
  at most 30 s (`maxDuration` in `vercel.json`).
- Login cookies are `Secure` and visitor IPs come from Vercel's `X-Forwarded-For` automatically.
- **Log in as `claude`:** run this on your PC with the connection string from Vercel (database →
  `.env.local` tab). It is a secret: never commit it.
  ```bash
  pip install "psycopg[binary]"
  python rubrub.py --database-url "postgres://…" --reset-password claude
  ```

## Deploy with Docker (Render, Railway, Fly.io…)

For hosts that run a container and keep a disk, with the SQLite file on it.

### Easiest: Render, in one click

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/Surakarnnerf/rub-rub)

`render.yaml` already describes everything: the Docker build, the Singapore region (closest to
Thailand), the `/healthz` health check, `RUBRUB_TRUST_PROXY=1` and a 1 GB persistent disk at `/data`.

1. Click the button, sign in to Render with GitHub and confirm. Render shows the monthly cost
   before you accept: a persistent disk needs a **paid** instance (at the time of writing about
   $7 a month for the smallest one, plus $0.25 per GB of disk; check
   [render.com/pricing](https://render.com/pricing)).
2. Wait for the first build (a few minutes). Render then shows your link, something like
   `https://rub-rub.onrender.com`. Open it on any phone.
3. Every `git push` to `main` redeploys the site automatically.

**Just trying it out?** In `render.yaml` set `plan: free` and delete the `disk:` block. The site
works, but a free service sleeps after 15 idle minutes and **loses all accounts and reviews
whenever it restarts**.

### Steps for any other host that can build a Dockerfile
1. Create a **web service from this GitHub repo**; the `Dockerfile` does the build (there is
   nothing to install).
2. Attach a **persistent disk / volume mounted at `/data`** (1 GB is plenty). Without it every
   restart or redeploy wipes the database. Many free plans don't include one, so check your
   host's current terms.
3. Set the environment variable **`RUBRUB_TRUST_PROXY=1`**. This makes the app read the
   visitor's real IP and HTTPS from the platform's proxy, so rate limits work per visitor and
   the login cookie is marked `Secure`.
4. Use **`/healthz`** as the health-check path (it returns `{"ok": true}` when the database opens).
5. Run **exactly one instance** while you use the SQLite file.

HTTPS comes from the host. The platform sets `PORT` itself and the app listens on it.

Try the image on your own PC (needs Docker):
```bash
docker build -t rub-rub .
docker run --rm -p 8000:8000 -v rubrub-data:/data rub-rub
```

## Environment variables
| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` (also `POSTGRES_URL`, `RUBRUB_DATABASE_URL`) | unset | a `postgres://` URL switches the app from the SQLite file to Postgres. Vercel's Neon integration sets it for you |
| `PORT` | `8000` | port to listen on; when set, the app binds `0.0.0.0` |
| `RUBRUB_DATA_DIR` | `./data` (`/data` in the Docker image) | where the SQLite file lives; point it at the persistent disk |
| `RUBRUB_TRUST_PROXY` | off (on in Vercel) | **set to `1` behind a hosting proxy**; leave off when clients connect directly, because they could forge the headers |
| `RUBRUB_PROXY_HOPS` | `1` | number of proxies in front of the app (e.g. `2` for Cloudflare + the host) |
| `RUBRUB_SECURE_COOKIES` | off | always mark the login cookie `Secure`, e.g. when your proxy doesn't send `X-Forwarded-Proto` |
| `RUBRUB_SEED` | on | `0` = don't add the fictional sample profiles and reviews |
| `RUBRUB_HOST` | `127.0.0.1` (`0.0.0.0` in Docker or when `PORT` is set) | interface to bind |
| `RUBRUB_RUN_AS_UID` | `10001` in Docker | if started as root, fix `RUBRUB_DATA_DIR` ownership then switch to this user |

## After it's live
- **Don't want the fictional samples on a public site?** Set `RUBRUB_SEED=0` before the first start.
- **Backups:** on Vercel use your database provider's backups; with Docker copy the `/data`
  folder regularly (the host may offer disk snapshots).
- **Still missing for a big public site:** email-based password reset, moderation tools and
  a limit on fake accounts liking each other's reviews.
