/* Rub rub: "install as an app" helpers. Loaded before app.js, which draws the buttons.
 *  - registers the service worker (offline app shell) where browsers allow it: https:// or localhost
 *  - keeps the browser's install prompt so our own button can show it later
 *  - knows whether we already run as an installed app, and whether this is an iPhone/iPad
 *    (iOS has no install prompt: people must use Share > Add to Home Screen) */
(function () {
  'use strict';

  let deferred = null;
  const listeners = new Set();
  const notify = () => listeners.forEach((fn) => { try { fn(); } catch (e) { /* a broken listener must not stop the others */ } });
  const mq = window.matchMedia ? window.matchMedia('(display-mode: standalone)') : null;

  const standalone = () => !!(mq && mq.matches) || window.navigator.standalone === true;
  const ios = () => /iphone|ipad|ipod/i.test(navigator.userAgent)
    || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1); // iPadOS pretends to be a Mac

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault(); // we show our own button instead of the browser's mini bar
    deferred = e;
    notify();
  });
  window.addEventListener('appinstalled', () => { deferred = null; notify(); });
  if (mq && mq.addEventListener) mq.addEventListener('change', notify);

  const secure = location.protocol === 'https:' || location.hostname === 'localhost' || location.hostname === '127.0.0.1';
  if ('serviceWorker' in navigator && secure) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => { /* the site works without it */ });
    });
  }

  window.RubPWA = {
    standalone,
    ios,
    canPrompt: () => !!deferred,
    onChange: (fn) => listeners.add(fn),
    /** Shows the browser's install dialog. Resolves to 'accepted', 'dismissed' or 'unavailable'. */
    async install() {
      if (!deferred) return 'unavailable';
      const ev = deferred;
      deferred = null;
      ev.prompt();
      const choice = await ev.userChoice;
      notify();
      return choice.outcome;
    },
  };
})();
