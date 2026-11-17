/* Rub rub service worker. It makes the site installable as an app and keeps the app itself
 * (screens, styles, code, icons) opening when the phone has no signal.
 *
 *  - Pages and code: network first, so you always get the newest version when online; the saved
 *    copy is used when offline or when the network takes longer than a few seconds.
 *  - Photos: saved the first time they are viewed.
 *  - /api/ is never cached or touched: it holds live, per-user data (logins, likes, drafts).
 *  - Other sites (Google Fonts) are left to the browser.
 *
 * Bump VERSION when this file's caching rules change; old caches are removed on activation. */
const VERSION = 'v1';
const SHELL_CACHE = `rubrub-shell-${VERSION}`;
const PHOTO_CACHE = `rubrub-photos-${VERSION}`;
const MAX_PHOTOS = 80;
const NETWORK_TIMEOUT_MS = 4000;

// Every file here must exist (tests/test_api.py checks it): one 404 would break installation.
const SHELL = [
  '/',
  '/css/style.css',
  '/js/i18n.js',
  '/js/pwa.js',
  '/js/app.js',
  '/favicon.svg',
  '/manifest.webmanifest',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL.map((url) => new Request(url, { cache: 'reload' }))))
      .then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(names
        .filter((n) => n.startsWith('rubrub-') && n !== SHELL_CACHE && n !== PHOTO_CACHE)
        .map((n) => caches.delete(n))))
      .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname === '/healthz') return;
  event.respondWith(url.pathname.startsWith('/uploads/') ? photo(req) : networkFirst(req));
});

async function networkFirst(req) {
  const cache = await caches.open(SHELL_CACHE);
  const key = req.mode === 'navigate' ? '/' : req.url; // the whole site is one page, whatever the address
  const fetched = fetch(req).then((res) => {
    if (res.ok && res.type === 'basic') cache.put(key, res.clone());
    return res;
  });
  fetched.catch(() => {}); // the race below may already be settled; don't report it twice
  const slow = new Promise((resolve) => setTimeout(resolve, NETWORK_TIMEOUT_MS, null));
  try {
    const first = await Promise.race([fetched, slow]);
    if (first) return first; // the network answered in time
  } catch (e) { /* offline or failed: use the saved copy */ }
  const saved = await cache.match(key);
  return saved || fetched; // nothing saved yet: wait for the network (or fail)
}

async function photo(req) {
  const cache = await caches.open(PHOTO_CACHE);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) {
    cache.put(req, res.clone()).then(() => trim(cache, MAX_PHOTOS));
  }
  return res;
}

async function trim(cache, max) {
  const keys = await cache.keys();
  for (let i = 0; i < keys.length - max; i++) await cache.delete(keys[i]); // oldest first
}
