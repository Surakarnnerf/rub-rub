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
tests/test_api.py    API tests
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

## Before putting it on the public internet
This is built to run locally or on a trusted network. For a public site you should add:
HTTPS (put it behind a reverse proxy such as Caddy or nginx, and add the `Secure` flag to
the session cookie in `server.py`), email-based password reset, and regular backups of the
`data` folder (copy it while the server is stopped).
