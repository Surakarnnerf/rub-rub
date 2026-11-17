/* Rub rub - single-page front end (no build step, no dependencies).
 * All user-written text is inserted with textContent / text nodes only.
 * The `html:` option of el() is reserved for the constant SVG strings in ICON. */
(function () {
  'use strict';

  /* ------------------------------------------------------------------ */
  /* i18n                                                                */
  /* ------------------------------------------------------------------ */
  const I18N = window.I18N;
  // pwa.js (service worker + install prompt) is optional: the site works without it
  const PWA = window.RubPWA || { standalone: () => false, ios: () => false, canPrompt: () => false, onChange() {}, install: async () => 'unavailable' };
  const LANGS = ['en', 'th'];
  let lang = '';
  try { lang = localStorage.getItem('rr_lang') || ''; } catch (e) { /* storage blocked */ }
  if (!LANGS.includes(lang)) lang = (navigator.language || '').toLowerCase().startsWith('th') ? 'th' : 'en';

  function t(key, vars) {
    let s = (I18N[lang] && I18N[lang][key]) ?? I18N.en[key] ?? key;
    if (vars) for (const k in vars) s = s.split('{' + k + '}').join(vars[k]);
    return s;
  }

  /* ------------------------------------------------------------------ */
  /* DOM helpers                                                         */
  /* ------------------------------------------------------------------ */
  const $ = (sel, root = document) => root.querySelector(sel);

  function append(node, kids) {
    for (const k of kids.flat(Infinity)) {
      if (k == null || k === false) continue;
      node.append(k.nodeType ? k : document.createTextNode(String(k)));
    }
  }

  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') n.className = v;
      else if (k === 'html') n.innerHTML = v; // constants only
      else if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2).toLowerCase(), v);
      else n.setAttribute(k, v === true ? '' : v);
    }
    append(n, kids);
    return n;
  }

  const ICON = {
    flame: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2c.5 3.200-1.200 4.800-2.800 6.600C7.600 10.400 6 12.200 6 15a6 6 0 0 0 12 0c0-2.200-1-3.800-2.200-5.200-.3 1.200-.9 2-1.800 2.500.4-3.900-.8-7.900-2-10.300z"/></svg>',
    heart: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 21s-7.500-4.600-9.600-9.200C.8 8.300 3 4.500 6.600 4.500c2 0 3.500 1 5.400 3 1.900-2 3.400-3 5.400-3 3.600 0 5.800 3.800 4.200 7.300C19.500 16.400 12 21 12 21z"/></svg>',
    heartO: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M12 21s-7.500-4.600-9.600-9.200C.8 8.300 3 4.500 6.600 4.500c2 0 3.500 1 5.400 3 1.900-2 3.400-3 5.400-3 3.600 0 5.800 3.800 4.200 7.300C19.500 16.400 12 21 12 21z"/></svg>',
    pin: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="M12 22s7-6.200 7-12a7 7 0 1 0-14 0c0 5.800 7 12 7 12zm0-9a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"/></svg>',
    clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
    camera: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M4 8h3l1.500-2h7L17 8h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z"/><circle cx="12" cy="13" r="3.500"/></svg>',
    pencil: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M4 20h4L19 9l-4-4L4 16v4z"/></svg>',
    trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13"/></svg>',
    search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.500" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="6.500"/><path d="M16 16l4.500 4.500"/></svg>',
    plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
    link: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M10 14a4 4 0 0 0 5.700 0l3-3a4 4 0 0 0-5.700-5.700l-1 1M14 10a4 4 0 0 0-5.700 0l-3 3a4 4 0 0 0 5.700 5.700l1-1"/></svg>',
    badge: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 1.500l2.600 2 3.200-.3 1.100 3 2.800 1.700-.9 3.100.9 3.100-2.800 1.700-1.100 3-3.200-.3-2.600 2-2.600-2-3.200.3-1.100-3L2.300 14.700l.9-3.100-.9-3.100L5.100 6.800l1.100-3 3.200.3z"/><path d="M8 12.200l2.800 2.800L16.200 9.500" fill="none" stroke="#fff" stroke-width="2.200" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    home: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 11.500L12 4l8 7.500V20a1 1 0 0 1-1 1h-4.500v-6h-5v6H5a1 1 0 0 1-1-1z"/></svg>',
    user: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.400 3.600-7 8-7s8 2.600 8 7"/></svg>',
  };
  const icon = (name, cls) => el('span', { class: 'ic' + (cls ? ' ' + cls : ''), html: ICON[name] });

  /* ------------------------------------------------------------------ */
  /* State + API                                                         */
  /* ------------------------------------------------------------------ */
  const state = {
    user: null,
    next: null,
    filters: { q: '', category: '', payment: '', min_rating: '', sort: 'new' },
  };

  class ApiErr extends Error {
    constructor(status, code, fields) {
      super(code);
      this.status = status;
      this.code = code;
      this.fields = fields || {};
    }
  }

  async function api(method, path, body, rawBlob) {
    const opts = { method, headers: { 'X-Requested-With': 'rubrub' } };
    if (rawBlob) {
      opts.body = rawBlob;
      opts.headers['Content-Type'] = rawBlob.type || 'application/octet-stream';
    } else if (body !== undefined) {
      opts.body = JSON.stringify(body);
      opts.headers['Content-Type'] = 'application/json';
    }
    let res;
    try { res = await fetch(path, opts); } catch (e) { throw new ApiErr(0, 'network'); }
    let data = null;
    try { data = await res.json(); } catch (e) { /* empty body */ }
    if (!res.ok) throw new ApiErr(res.status, (data && data.error) || 'server_error', data && data.fields);
    return data;
  }

  const errText = (code) => {
    const k = 'err.' + code;
    return (I18N[lang] && I18N[lang][k]) || I18N.en[k] || t('err.server_error');
  };

  let toastTimer;
  function toast(msg) {
    const n = $('#toast');
    n.textContent = msg;
    n.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => n.classList.remove('show'), 3400);
  }

  function handleErr(e) {
    if (e instanceof ApiErr && e.status === 401) {
      state.user = null;
      state.next = location.hash;
      renderChrome();
      toast(t('err.unauthorized'));
      location.hash = '#/login';
      return;
    }
    toast(errText(e && e.code));
  }

  /* ------------------------------------------------------------------ */
  /* Formatting + opening hours                                          */
  /* ------------------------------------------------------------------ */
  const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  /* food categories: ข้าวมันไก่ / ข้าวกระเพรา / ก๋วยเตี๋ยว / อื่นๆ */
  const CATS = ['khao_man_gai', 'kra_pao', 'noodles', 'other'];
  const CAT_ICON = { khao_man_gai: '🍗', kra_pao: '🍳', noodles: '🍜', other: '🍽️' };
  const CAT_WORDS = [ // same order and words as CATEGORY_KEYWORDS in rubrub.py
    ['khao_man_gai', ['khao man gai', 'kao man gai', 'man gai', 'hainan', 'ข้าวมันไก่']],
    ['kra_pao', ['kra pao', 'kaprao', 'krapow', 'ka prao', 'กะเพรา', 'กระเพรา']],
    ['noodles', ['noodle', 'kuay teow', 'guay teow', 'kway teow', 'ba mee', 'bamee', 'pad thai', 'khao soi', 'pad see ew',
      'rad na', 'wonton', 'ก๋วยเตี๋ยว', 'บะหมี่', 'เส้น', 'ผัดไทย', 'ข้าวซอย', 'ราดหน้า']],
  ];
  function guessCategory(text) {
    const s = (text || '').toLowerCase();
    for (const [cat, words] of CAT_WORDS) if (words.some((w) => s.includes(w))) return cat;
    return 'other';
  }
  /** [label in the current language, the same label in the other language] */
  function catLabels(key) {
    return [t('cat.' + key), I18N[lang === 'th' ? 'en' : 'th']['cat.' + key]];
  }

  const overnight = (r) => r.close_time < r.open_time;
  const baht = (n) => '฿' + Number(n).toLocaleString('en-US');

  function fmtDays(days) {
    const idx = DAYS.map((d, i) => (days.includes(d) ? i : -1)).filter((i) => i >= 0);
    if (idx.length === 7) return t('everyDay');
    const parts = [];
    let i = 0;
    while (i < idx.length) {
      let j = i;
      while (j + 1 < idx.length && idx[j + 1] === idx[j] + 1) j++;
      if (j - i + 1 >= 3) parts.push(t('day.' + DAYS[idx[i]]) + '–' + t('day.' + DAYS[idx[j]]));
      else for (let k = i; k <= j; k++) parts.push(t('day.' + DAYS[idx[k]]));
      i = j + 1;
    }
    return parts.join(', ');
  }

  function hoursText(r) {
    return `${fmtDays(r.open_days)} · ${r.open_time}–${r.close_time}` + (overnight(r) ? ' ' + t('nextDay') : '');
  }

  function bangkokNow() {
    try {
      const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: 'Asia/Bangkok', weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
      }).formatToParts(new Date());
      const get = (type) => parts.find((p) => p.type === type).value;
      return { day: get('weekday'), min: (Number(get('hour')) % 24) * 60 + Number(get('minute')) };
    } catch (e) {
      return null;
    }
  }

  /** true / false, or null when the browser can't tell. Uses Thailand time. */
  function isOpenNow(r) {
    const now = bangkokNow();
    if (!now || !DAYS.includes(now.day)) return null;
    const toMin = (s) => Number(s.slice(0, 2)) * 60 + Number(s.slice(3));
    const o = toMin(r.open_time);
    const c = toMin(r.close_time);
    const di = DAYS.indexOf(now.day);
    const today = r.open_days.includes(now.day);
    const yesterday = r.open_days.includes(DAYS[(di + 6) % 7]);
    if (o < c) return today && now.min >= o && now.min < c;
    return (today && now.min >= o) || (yesterday && now.min < c);
  }

  function fmtDate(ts) {
    try {
      return new Intl.DateTimeFormat(lang === 'th' ? 'th-TH' : 'en-GB', { dateStyle: 'medium' }).format(new Date(ts * 1000));
    } catch (e) {
      return new Date(ts * 1000).toDateString();
    }
  }

  function dishEmoji(name) {
    const s = name.toLowerCase();
    const has = (...w) => w.some((x) => s.includes(x));
    if (has('mango', 'mamuang', 'ข้าวเหนียวมะม่วง')) return '🥭';
    if (has('som tam', 'salad', 'larb', 'ส้มตำ', 'ลาบ')) return '🥗';
    if (has('moo ping', 'skewer', 'satay', 'gai yang', 'grill', 'หมูปิ้ง', 'ไก่ย่าง')) return '🍢';
    if (has('soi', 'noodle', 'teow', 'pad thai', 'mee', 'ก๋วยเตี๋ยว', 'ผัดไทย', 'ข้าวซอย')) return '🍜';
    if (has('tom ', 'soup', 'ต้ม')) return '🍲';
    if (has('rice', 'khao', 'kra pao', 'กะเพรา', 'ข้าว')) return '🍚';
    return '🌶️';
  }

  /* ------------------------------------------------------------------ */
  /* Small shared components                                             */
  /* ------------------------------------------------------------------ */
  const PAY_ICON = { cash_only: '💵', qr: '📱', card: '💳' };

  function flames(n, label) {
    const wrap = el('span', { class: 'flames', role: 'img', 'aria-label': label || `${n}/5` });
    for (let i = 1; i <= 5; i++) wrap.append(icon('flame', i <= n ? 'on' : ''));
    return wrap;
  }

  function spiceText(n) {
    return n === 0 ? t('spice.0') : '🌶️'.repeat(n) + ' ' + t('spice.' + n);
  }

  function payChips(payments) {
    return el('ul', { class: 'pay-list' }, payments.map((p) =>
      el('li', { class: 'pay pay-' + p }, el('span', { 'aria-hidden': 'true' }, PAY_ICON[p]), t('pay.' + p))));
  }

  function tag(text, cls) { return el('span', { class: 'tag ' + (cls || '') }, text); }

  function openTag(r) {
    const open = isOpenNow(r);
    if (open === null) return null;
    return open
      ? tag('● ' + t('open.now'), 'tag-open')
      : tag(t('open.closed'), 'tag-closed');
  }

  function avatar(user, big) {
    const initial = ([...(user.display_name || user.username)][0] || '?').toUpperCase();
    const hue = [...user.username].reduce((a, c) => a + c.charCodeAt(0), 0) % 5;
    const star = user.is_featured || user.is_famous;
    return el('span', { class: `avatar av-${hue}${star ? ' av-star' : ''}${big === 'md' ? ' av-md' : big ? ' av-big' : ''}`, 'aria-hidden': 'true' }, initial);
  }

  /** The official food blogger gets "Famous food blogger"; anyone else who collected enough likes, "Famous reviewer". */
  const fameLabel = (user) => (user.is_featured ? t('famousBlogger') : t('famous.reviewer'));
  function badge(user) { return el('span', { class: 'blogger-badge' }, icon('badge'), user ? fameLabel(user) : t('famousBlogger')); }

  function authorChip(author) {
    return el('a', { class: 'author', href: '#/user/' + encodeURIComponent(author.username) },
      avatar(author), el('span', { class: 'author-name' }, author.display_name),
      (author.is_featured || author.is_famous) && el('span', { class: 'ic badge-ic', title: fameLabel(author), html: ICON.badge }));
  }

  function placeholder(r, big) {
    return el('div', { class: `ph ph-${r.id % 4}${big ? ' ph-big' : ''}`, 'aria-hidden': 'true' },
      r.category && r.category !== 'other' ? CAT_ICON[r.category] : dishEmoji(r.menu_item));
  }

  function catTag(r) {
    return r.category ? tag(`${CAT_ICON[r.category] || ''} ${t('cat.' + r.category)}`, 'tag-cat') : null;
  }

  function likeButton(r) {
    const btn = el('button', { type: 'button', class: 'like' });
    function paint() {
      btn.classList.toggle('on', r.liked);
      btn.setAttribute('aria-pressed', String(r.liked));
      btn.setAttribute('aria-label', t('like') + ` (${r.likes})`);
      btn.replaceChildren(icon(r.liked ? 'heart' : 'heartO'), el('span', null, String(r.likes)));
    }
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      if (!state.user) {
        state.next = location.hash;
        toast(t('loginToLike'));
        location.hash = '#/login';
        return;
      }
      btn.disabled = true;
      try {
        const res = await api('POST', `/api/reviews/${r.id}/like`);
        r.liked = res.liked;
        r.likes = res.likes;
        paint();
      } catch (err) { handleErr(err); }
      btn.disabled = false;
    });
    paint();
    return btn;
  }

  function reviewCard(r) {
    const href = '#/review/' + r.id;
    return el('article', { class: 'card' },
      el('a', { class: 'card-media', href, tabindex: '-1', 'aria-hidden': 'true' },
        r.photo ? el('img', { src: r.photo, alt: '', loading: 'lazy' }) : placeholder(r)),
      el('div', { class: 'card-body' },
        el('div', { class: 'tags' }, r.is_sample && tag(t('sample'), 'tag-sample'), catTag(r), openTag(r)),
        el('h3', { class: 'card-title' }, el('a', { href }, r.menu_item)),
        el('p', { class: 'card-sub' }, icon('pin'), el('span', null, r.restaurant_name + (r.area ? ' · ' + r.area : ''))),
        el('div', { class: 'card-meta' }, flames(r.rating, `${r.rating}/5 · ${t('rate.' + r.rating)}`), el('span', { class: 'price' }, baht(r.price_thb))),
        payChips(r.payments),
        el('p', { class: 'hours' }, icon('clock'), el('span', null, hoursText(r))),
        el('div', { class: 'card-foot' }, authorChip(r.author), likeButton(r))));
  }

  const loadingNode = () => el('div', { class: 'loading' }, el('span', { class: 'spinner', 'aria-hidden': 'true' }), t('loading'));

  function skeletons(n) {
    return Array.from({ length: n }, () => el('div', { class: 'card skeleton', 'aria-hidden': 'true' }));
  }

  function messageBox(title, sub, action) {
    return el('div', { class: 'wrap' }, el('div', { class: 'message' },
      el('div', { class: 'message-emoji', 'aria-hidden': 'true' }, '🌶️'),
      el('h2', null, title), sub && el('p', null, sub), action));
  }

  function errorBox(e, retry) {
    const code = e && e.code;
    if (code === 'not_found') return messageBox(t('notfound.title'), t('notfound.sub'), el('a', { class: 'btn btn-primary', href: '#/' }, t('nav.home')));
    return messageBox(t('err.oops'), errText(code), retry && el('button', { class: 'btn btn-primary', type: 'button', onClick: retry }, t('retry')));
  }

  function setTitle(s) { document.title = s ? `${s} · Rub rub` : 'Rub rub · ' + t('tagline'); }

  /* ------------------------------------------------------------------ */
  /* Chrome: header, footer, floating button                             */
  /* ------------------------------------------------------------------ */
  function langToggle() {
    return el('div', { class: 'lang', role: 'group', 'aria-label': 'Language' },
      LANGS.map((l) => el('button', {
        type: 'button', class: 'lang-btn' + (l === lang ? ' on' : ''), 'aria-pressed': String(l === lang),
        lang: l, onClick: () => setLang(l),
      }, l === 'en' ? 'EN' : 'ไทย')));
  }

  function accountArea() {
    if (!state.user) {
      return el('div', { class: 'account' },
        el('a', { class: 'btn btn-ghost btn-sm', href: '#/login' }, t('nav.login')),
        el('a', { class: 'btn btn-primary btn-sm', href: '#/signup' }, t('nav.signup')));
    }
    return el('div', { class: 'account' },
      el('a', { class: 'me', href: '#/user/' + encodeURIComponent(state.user.username) },
        avatar(state.user), el('span', { class: 'me-name' }, state.user.display_name)),
      el('button', { class: 'btn btn-ghost btn-sm', type: 'button', onClick: logout }, t('nav.logout')));
  }

  function renderChrome() {
    document.documentElement.lang = lang;
    $('#skip-link').textContent = t('skip');
    $('#topbar').replaceChildren(el('div', { class: 'topbar-in' },
      el('a', { class: 'brand', href: '#/', 'aria-label': 'Rub rub' },
        el('span', { class: 'brand-mark', html: ICON.flame }), el('span', { class: 'brand-name' }, 'Rub rub')),
      el('div', { class: 'topbar-actions' }, langToggle(), accountArea())));
    $('#footer').replaceChildren(el('div', { class: 'wrap footer-in' },
      el('strong', null, 'Rub rub'), el('span', null, t('footer')),
      !PWA.standalone() && el('button', { class: 'link-btn footer-link', type: 'button', onClick: installApp }, t('install.footer'))));
    $('#fab-slot').replaceChildren(el('a', { class: 'fab', href: '#/new', 'aria-label': t('nav.write') }, icon('plus'), el('span', { class: 'fab-text' }, t('nav.write'))));
    renderTabbar();
    renderInstall();
    renderNet();
  }

  /** App-style bottom bar (phones): Home, Write, and Me / Log in. */
  function renderTabbar() {
    const path = (location.hash.replace(/^#/, '') || '/').split('?')[0];
    const me = state.user;
    const meActive = me ? path.toLowerCase() === '/user/' + me.username.toLowerCase() : /^\/(login|signup)$/.test(path);
    const tab = (href, ic, label, active, cls) => el('a', {
      class: 'tab' + (cls ? ' ' + cls : '') + (active ? ' on' : ''), href, 'aria-current': active ? 'page' : null,
    }, icon(ic), el('span', null, label));
    $('#tabbar').replaceChildren(
      tab('#/', 'home', t('nav.home'), path === '/'),
      tab('#/new', 'plus', t('tab.write'), /^\/(new|edit)/.test(path), 'tab-write'),
      tab(me ? '#/user/' + encodeURIComponent(me.username) : '#/login', 'user', me ? t('tab.me') : t('tab.signin'), meActive));
  }

  /* ------------------------------------------------------------------ */
  /* Install as an app + offline notice                                  */
  /* ------------------------------------------------------------------ */
  const INSTALL_KEY = 'rr_install_dismissed';
  function installDismissed() {
    try { return Date.now() - Number(localStorage.getItem(INSTALL_KEY) || 0) < 14 * 86400000; } catch (e) { return false; }
  }
  function dismissInstall() {
    try { localStorage.setItem(INSTALL_KEY, String(Date.now())); } catch (e) { /* ignore */ }
    renderInstall();
  }

  function showInstallHelp() {
    const dlg = el('dialog', { class: 'sheet', 'aria-labelledby': 'sheet-title' },
      el('h2', { id: 'sheet-title' }, t('install.title')),
      el('p', null, PWA.ios() ? t('install.ios') : t('install.other')),
      el('button', { class: 'btn btn-primary btn-block', type: 'button', onClick: () => dlg.close() }, t('install.gotIt')));
    dlg.addEventListener('close', () => dlg.remove());
    dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
    document.body.append(dlg);
    if (dlg.showModal) dlg.showModal(); else dlg.setAttribute('open', '');
  }

  async function installApp() {
    if (PWA.canPrompt()) {
      if ((await PWA.install()) === 'accepted') toast(t('install.done'));
    } else {
      showInstallHelp(); // iPhone/iPad and browsers without an install prompt
    }
  }

  /** "Add to home screen" card on the home page of phones. */
  function renderInstall() {
    const slot = $('#install-slot');
    const path = (location.hash.replace(/^#/, '') || '/').split('?')[0];
    const canShow = path === '/' && !PWA.standalone() && !installDismissed() && (PWA.canPrompt() || PWA.ios());
    if (!canShow) { slot.replaceChildren(); return; }
    slot.replaceChildren(el('aside', { class: 'install-banner', 'aria-label': t('install.title') },
      el('span', { class: 'brand-mark', html: ICON.flame }),
      el('div', { class: 'install-text' }, el('strong', null, t('install.title')),
        el('span', null, PWA.canPrompt() ? t('install.sub') : t('install.ios'))),
      el('div', { class: 'install-actions' },
        PWA.canPrompt() && el('button', { class: 'btn btn-primary btn-sm', type: 'button', onClick: installApp }, t('install.btn')),
        el('button', { class: 'btn btn-ghost btn-sm', type: 'button', onClick: dismissInstall }, PWA.canPrompt() ? t('install.later') : t('install.gotIt')))));
  }

  function renderNet() {
    const bar = $('#netbar');
    const off = navigator.onLine === false;
    bar.hidden = !off;
    bar.textContent = off ? t('offline') : '';
  }

  function setLang(l) {
    lang = l;
    try { localStorage.setItem('rr_lang', l); } catch (e) { /* ignore */ }
    renderChrome();
    render();
  }

  async function logout() {
    try { await api('POST', '/api/logout'); } catch (e) { /* ignore */ }
    state.user = null;
    renderChrome();
    toast(t('loggedOut'));
    if (location.hash === '#/' || location.hash === '') render(); else location.hash = '#/';
  }

  /* ------------------------------------------------------------------ */
  /* Home                                                                */
  /* ------------------------------------------------------------------ */
  function feedSection(onReady) {
    const f = state.filters;
    const grid = el('div', { class: 'grid' });
    const count = el('p', { class: 'count', 'aria-live': 'polite' });
    const moreBtn = el('button', { class: 'btn btn-ghost', type: 'button', hidden: true }, t('loadMore'));
    let token = 0;
    let offset = 0;

    async function load(reset) {
      const my = ++token;
      if (reset) { offset = 0; grid.replaceChildren(...skeletons(3)); moreBtn.hidden = true; }
      moreBtn.disabled = true;
      try {
        const qs = new URLSearchParams({ limit: '9', offset: String(offset), sort: f.sort });
        if (f.q) qs.set('q', f.q);
        if (f.category) qs.set('category', f.category);
        if (f.payment) qs.set('payment', f.payment);
        if (f.min_rating) qs.set('min_rating', f.min_rating);
        const res = await api('GET', '/api/reviews?' + qs);
        if (my !== token) return;
        if (reset) grid.replaceChildren();
        res.reviews.forEach((r) => grid.append(reviewCard(r)));
        offset += res.reviews.length;
        count.textContent = t('resultCount', { n: res.total });
        moreBtn.hidden = offset >= res.total;
        if (!res.total) {
          grid.replaceChildren(el('div', { class: 'empty' },
            el('div', { class: 'message-emoji', 'aria-hidden': 'true' }, '🍜'),
            el('h3', null, t('empty.title')), el('p', null, t('empty.sub'))));
        }
      } catch (e) {
        if (my === token) grid.replaceChildren(errorBox(e, () => load(true)));
      }
      moreBtn.disabled = false;
    }
    moreBtn.addEventListener('click', () => load(false));

    const search = el('input', {
      type: 'search', class: 'input', id: 'feed-q', placeholder: t('feed.search'), 'aria-label': t('feed.search'), maxlength: '80',
    });
    search.value = f.q;
    let debounce;
    search.addEventListener('input', () => {
      clearTimeout(debounce);
      debounce = setTimeout(() => { f.q = search.value.trim(); load(true); }, 300);
    });

    const catItems = ['', ...CATS];
    const catBtns = catItems.map((val) => el('button', {
      type: 'button', class: 'chip chip-cat', 'aria-pressed': String(f.category === val), onClick: () => setCategory(val),
    }, val
      ? [el('span', { class: 'chip-ic', 'aria-hidden': 'true' }, CAT_ICON[val]),
        el('span', { class: 'chip-text' }, el('b', null, catLabels(val)[0]), el('small', { lang: lang === 'th' ? 'en' : 'th' }, catLabels(val)[1]))]
      : t('filter.all')));
    function setCategory(val) {
      f.category = val;
      catBtns.forEach((b, i) => b.setAttribute('aria-pressed', String(catItems[i] === val)));
      load(true);
    }

    const payItems = [['', t('filter.all')], ['cash_only', '💵 ' + t('pay.cash_only')], ['qr', '📱 ' + t('pay.qr')], ['card', '💳 ' + t('pay.card')]];
    const payBtns = payItems.map(([val, label]) => el('button', {
      type: 'button', class: 'chip', 'aria-pressed': String(f.payment === val),
      onClick: () => { f.payment = val; payBtns.forEach((b, i) => b.setAttribute('aria-pressed', String(payItems[i][0] === val))); load(true); },
    }, label));

    const select = (id, label, opts, key) => {
      const s = el('select', { class: 'input', id, 'aria-label': label }, opts.map(([v, text]) => el('option', { value: v }, text)));
      s.value = f[key];
      s.addEventListener('change', () => { f[key] = s.value; load(true); });
      return el('label', { class: 'select-wrap', for: id }, el('span', { class: 'select-label' }, label), s);
    };

    const node = el('section', { class: 'wrap section', id: 'feed' },
      el('div', { class: 'section-head' },
        el('h2', null, t('feed.title')), count),
      el('div', { class: 'filters' },
        el('div', { class: 'search' }, icon('search'), search),
        el('div', { class: 'chips cats', role: 'group', 'aria-label': t('filter.category') }, catBtns),
        el('div', { class: 'chips', role: 'group', 'aria-label': t('filter.payment') }, payBtns),
        el('div', { class: 'selects' },
          select('feed-rating', t('filter.rating'), [['', t('filter.any')], ['3', '3+'], ['4', '4+'], ['5', '5']], 'min_rating'),
          select('feed-sort', t('filter.sort'), [['new', t('sort.new')], ['top', t('sort.top')], ['cheap', t('sort.cheap')], ['liked', t('sort.liked')]], 'sort'))),
      grid,
      el('div', { class: 'more' }, moreBtn));

    return {
      node,
      load,
      setCategory,
      setQuery(q) { f.q = q; search.value = q; load(true); },
    };
  }

  async function viewHome() {
    setTitle();
    const [picks, famous] = await Promise.all([
      api('GET', '/api/reviews?featured=1&sort=top&limit=6').catch(() => ({ reviews: [] })),
      api('GET', '/api/users?limit=6').then((r) => r.users).catch(() => []),
    ]);
    let blogger = null;
    if (picks.reviews.length) {
      blogger = await api('GET', '/api/users/' + encodeURIComponent(picks.reviews[0].author.username)).then((r) => r.user).catch(() => null);
    }

    const feed = feedSection();
    const heroSearch = el('input', {
      type: 'search', class: 'input', placeholder: t('hero.search'), 'aria-label': t('hero.search'), maxlength: '80',
    });
    const hero = el('section', { class: 'hero' }, el('div', { class: 'wrap hero-in' },
      el('p', { class: 'eyebrow' }, '🌶️ ' + t('hero.eyebrow')),
      el('h1', null, t('hero.title1'), ' ', el('em', null, t('hero.title2'))),
      el('p', { class: 'lead' }, t('hero.sub')),
      el('form', {
        class: 'hero-search', role: 'search',
        onSubmit: (e) => { e.preventDefault(); feed.setQuery(heroSearch.value.trim()); $('#feed').scrollIntoView({ behavior: 'smooth' }); },
      }, el('div', { class: 'search' }, icon('search'), heroSearch), el('button', { class: 'btn btn-gold', type: 'submit' }, t('hero.searchBtn'))),
      el('div', { class: 'hero-cats', role: 'group', 'aria-label': t('filter.category') }, CATS.map((c) => el('button', {
        type: 'button', class: 'chip chip-hero',
        onClick: () => { feed.setCategory(c); $('#feed').scrollIntoView({ behavior: 'smooth' }); },
      }, el('span', { 'aria-hidden': 'true' }, CAT_ICON[c]), catLabels(c)[0]))),
      el('div', { class: 'hero-cta' },
        el('a', { class: 'btn btn-cream', href: '#/new' }, icon('plus'), t('hero.write')),
        blogger && el('a', { class: 'btn btn-outline-light', href: '#/user/' + encodeURIComponent(blogger.username) }, t('hero.meet', { name: blogger.display_name })))));

    const frag = el('div', null, hero);

    if (blogger && picks.reviews.length) {
      frag.append(el('section', { class: 'wrap section pick' },
        el('div', { class: 'section-head' }, el('h2', null, t('pick.title')), el('p', { class: 'sub' }, t('pick.sub'))),
        el('div', { class: 'spotlight' },
          avatar(blogger, true),
          el('div', { class: 'spotlight-text' },
            badge(),
            el('h3', null, blogger.display_name),
            el('p', { class: 'bio' }, blogger.bio),
            el('p', { class: 'stats-line' },
              t('stat.reviews', { n: blogger.review_count }), ' · ',
              blogger.avg_rating != null ? t('stat.avg', { n: blogger.avg_rating }) : '', ' · ',
              t('stat.likes', { n: blogger.likes_received }))),
          el('a', { class: 'btn btn-primary', href: '#/user/' + encodeURIComponent(blogger.username) }, t('pick.seeAll'))),
        el('div', { class: 'rail' }, picks.reviews.map((r) => el('div', { class: 'rail-item' }, reviewCard(r))))));
    }

    if (famous.length) {
      frag.append(el('section', { class: 'wrap section famous' },
        el('div', { class: 'section-head' }, el('h2', null, '🔥 ' + t('famous.title')), el('p', { class: 'sub' }, t('famous.sub'))),
        el('div', { class: 'famous-grid' }, famous.map((u) => el('a', { class: 'famous-card', href: '#/user/' + encodeURIComponent(u.username) },
          avatar(u, 'md'),
          el('div', { class: 'famous-text' },
            badge(u),
            el('h3', null, u.display_name),
            el('p', { class: 'stats-line' }, t('famous.stats', { reviews: u.review_count, likes: u.likes_received }),
              u.avg_rating != null ? ' · ' + t('stat.avg', { n: u.avg_rating }) : '')))))));
    }

    frag.append(feed.node);
    feed.load(true);
    return frag;
  }

  /* ------------------------------------------------------------------ */
  /* Review detail                                                       */
  /* ------------------------------------------------------------------ */
  async function viewReview(id) {
    const { review: r } = await api('GET', '/api/reviews/' + id);
    setTitle(`${r.menu_item} @ ${r.restaurant_name}`);
    const mine = state.user && state.user.username.toLowerCase() === r.author.username.toLowerCase();
    const open = isOpenNow(r);

    const actions = el('div', { class: 'detail-actions' },
      likeButton(r),
      el('button', {
        class: 'btn btn-ghost btn-sm', type: 'button',
        onClick: async () => {
          try { await navigator.clipboard.writeText(location.href); toast(t('copied')); } catch (e) { toast(location.href); }
        },
      }, icon('link'), t('copyLink')),
      mine && el('a', { class: 'btn btn-ghost btn-sm', href: '#/edit/' + r.id }, icon('pencil'), t('edit')),
      mine && el('button', {
        class: 'btn btn-danger btn-sm', type: 'button',
        onClick: async () => {
          if (!confirm(t('confirmDelete'))) return;
          try { await api('DELETE', '/api/reviews/' + r.id); toast(t('deleted')); location.hash = '#/'; } catch (e) { handleErr(e); }
        },
      }, icon('trash'), t('delete')));

    return el('article', { class: 'wrap detail' },
      el('a', { class: 'back', href: '#/' }, '← ' + t('back')),
      el('div', { class: 'detail-grid' },
        el('div', { class: 'detail-media' }, r.photo ? el('img', { src: r.photo, alt: `${r.menu_item} — ${r.restaurant_name}` }) : placeholder(r, true)),
        el('div', { class: 'detail-main' },
          el('div', { class: 'tags' }, r.is_sample && tag(t('sample'), 'tag-sample'), catTag(r), openTag(r)),
          el('h1', null, r.menu_item),
          el('p', { class: 'detail-sub' }, icon('pin'), el('span', null, r.restaurant_name + (r.area ? ' · ' + r.area : ''))),
          el('div', { class: 'facts' },
            fact(t('detail.rating'), el('span', null, flames(r.rating, `${r.rating}/5`), ' ', el('strong', null, t('rate.' + r.rating)))),
            fact(t('detail.price'), el('strong', { class: 'big-price' }, baht(r.price_thb))),
            fact(t('detail.spice'), el('span', null, spiceText(r.spice)))),
          el('section', { class: 'info' }, el('h2', null, t('detail.pay')), payChips(r.payments)),
          el('section', { class: 'info' },
            el('h2', null, t('detail.hours'), open !== null && ' ', open !== null && openTag(r)),
            el('div', { class: 'days', role: 'list' }, DAYS.map((d) =>
              el('span', { class: 'day' + (r.open_days.includes(d) ? ' on' : ''), role: 'listitem', 'aria-label': t('dayFull.' + d) + (r.open_days.includes(d) ? '' : ' ✕') }, t('day.' + d)))),
            el('p', { class: 'hours big' }, icon('clock'), el('span', null, `${r.open_time} – ${r.close_time}` + (overnight(r) ? ' ' + t('nextDay') : ''))),
            el('p', { class: 'hint' }, t('tzNote'))),
          r.body && el('blockquote', { class: 'story' }, r.body),
          el('div', { class: 'byline' },
            authorChip(r.author),
            el('span', { class: 'posted' }, t('postedOn', { date: fmtDate(r.created_at) }))),
          actions)));
  }

  function fact(label, value) {
    return el('div', { class: 'fact' }, el('span', { class: 'fact-label' }, label), value);
  }

  /* ------------------------------------------------------------------ */
  /* Profile                                                             */
  /* ------------------------------------------------------------------ */
  async function viewUser(username) {
    const { user } = await api('GET', '/api/users/' + encodeURIComponent(username));
    const { reviews } = await api('GET', '/api/reviews?limit=50&sort=new&user=' + encodeURIComponent(user.username));
    setTitle(user.display_name);
    const mine = state.user && state.user.username.toLowerCase() === user.username.toLowerCase();
    return el('div', { class: 'wrap profile' },
      el('section', { class: 'profile-head' + (user.is_featured || user.is_famous ? ' featured' : '') },
        avatar(user, true),
        el('div', { class: 'profile-text' },
          (user.is_featured || user.is_famous) && badge(user),
          el('h1', null, user.display_name),
          el('p', { class: 'handle' }, '@' + user.username + ' · ' + t('profile.joined', { date: fmtDate(user.created_at) })),
          user.bio && el('p', { class: 'bio' }, user.bio),
          el('div', { class: 'stats' },
            stat(user.review_count, t('stat.reviewsLabel')),
            stat(user.avg_rating != null ? user.avg_rating : '–', t('stat.avgLabel')),
            stat(user.likes_received, t('stat.likesLabel'))),
          mine && !user.is_featured && !user.is_famous && el('p', { class: 'hint' }, t('famous.hint', { n: user.famous_at })),
          mine && el('button', { class: 'btn btn-ghost btn-sm', type: 'button', onClick: logout }, t('nav.logout')))),
      el('h2', { class: 'profile-h2' }, t('profile.reviewsBy', { name: user.display_name })),
      reviews.length
        ? el('div', { class: 'grid' }, reviews.map(reviewCard))
        : el('div', { class: 'empty' }, el('div', { class: 'message-emoji', 'aria-hidden': 'true' }, '🍜'),
          el('p', null, mine ? t('profile.noneMine') : t('profile.none')),
          mine && el('a', { class: 'btn btn-primary', href: '#/new' }, icon('plus'), t('nav.write'))));
  }

  const stat = (n, label) => el('div', { class: 'stat' }, el('strong', null, String(n)), el('span', null, label));

  /* ------------------------------------------------------------------ */
  /* Auth                                                                */
  /* ------------------------------------------------------------------ */
  function setFieldError(root, name, code) {
    const w = $(`[data-field="${name}"]`, root);
    if (!w) return;
    w.classList.toggle('has-err', !!code);
    const p = $('.field-err', w);
    if (p) p.textContent = code ? errText(code) : '';
    const input = $('input, select, textarea', w);
    if (input) input.setAttribute('aria-invalid', code ? 'true' : 'false');
  }

  function field(name, label, control, opts = {}) {
    const lid = 'lbl-' + name;
    return el('div', { class: 'field', 'data-field': name },
      opts.group
        ? el('span', { class: 'label', id: lid }, label, opts.required && el('span', { class: 'req', 'aria-hidden': 'true' }, ' *'), opts.optional && el('span', { class: 'opt' }, ' ' + t('f.optional')))
        : el('label', { class: 'label', for: 'in-' + name, id: lid }, label, opts.required && el('span', { class: 'req', 'aria-hidden': 'true' }, ' *'), opts.optional && el('span', { class: 'opt' }, ' ' + t('f.optional'))),
      control,
      opts.hint && el('p', { class: 'hint' }, opts.hint),
      el('p', { class: 'field-err', role: 'alert' }));
  }

  function goAfterAuth() {
    const next = state.next;
    state.next = null;
    location.hash = next && next !== '#/login' && next !== '#/signup' ? next : '#/';
  }

  function authView(mode) {
    const signup = mode === 'signup';
    setTitle(t(signup ? 'nav.signup' : 'nav.login'));
    if (state.user) { location.hash = '#/'; return el('div'); }
    const vals = { username: '', display_name: '', password: '' };
    const mk = (name, type, extra = {}) => {
      const i = el('input', { class: 'input', id: 'in-' + name, name, type, autocomplete: 'off', maxlength: extra.max, ...extra.attrs });
      i.addEventListener('input', () => { vals[name] = i.value; setFieldError(form, name, null); });
      return i;
    };
    const btn = el('button', { class: 'btn btn-primary btn-block', type: 'submit' }, t(signup ? 'signup.submit' : 'login.submit'));
    const banner = el('p', { class: 'form-err', role: 'alert', hidden: true });
    const form = el('form', { class: 'panel auth-panel', novalidate: true, onSubmit: submit },
      el('h1', null, t(signup ? 'signup.title' : 'login.title')),
      el('p', { class: 'sub' }, t(signup ? 'signup.sub' : 'login.sub')),
      banner,
      field('username', t('login.username'), mk('username', 'text', { max: 20, attrs: { autocomplete: signup ? 'username' : 'username', autocapitalize: 'none', spellcheck: 'false' } }),
        { required: true, hint: signup ? t('signup.usernameHint') : null }),
      signup && field('display_name', t('signup.display'), mk('display_name', 'text', { max: 40, attrs: { autocomplete: 'nickname' } }), { optional: true }),
      field('password', t('login.password'), mk('password', 'password', { max: 128, attrs: { autocomplete: signup ? 'new-password' : 'current-password' } }),
        { required: true, hint: signup ? t('signup.passwordHint') : null }),
      btn,
      el('p', { class: 'switch' }, t(signup ? 'signup.haveAccount' : 'login.noAccount'), ' ',
        el('a', { href: signup ? '#/login' : '#/signup' }, t(signup ? 'nav.login' : 'nav.signup'))));

    async function submit(e) {
      e.preventDefault();
      banner.hidden = true;
      ['username', 'display_name', 'password'].forEach((n) => setFieldError(form, n, null));
      const errs = {};
      if (!vals.username.trim()) errs.username = 'required';
      if (!vals.password) errs.password = 'required';
      else if (signup && vals.password.length < 8) errs.password = 'password_short';
      if (Object.keys(errs).length) { Object.entries(errs).forEach(([k, v]) => setFieldError(form, k, v)); return; }
      btn.disabled = true;
      try {
        const body = { username: vals.username.trim(), password: vals.password };
        if (signup) body.display_name = vals.display_name;
        const res = await api('POST', signup ? '/api/signup' : '/api/login', body);
        state.user = res.user;
        renderChrome();
        toast(t(signup ? 'welcome' : 'welcomeBack', { name: res.user.display_name }));
        goAfterAuth();
      } catch (err) {
        if (err.code === 'validation') Object.entries(err.fields).forEach(([k, v]) => setFieldError(form, k, v));
        else { banner.textContent = errText(err.code); banner.hidden = false; }
      }
      btn.disabled = false;
    }
    return el('div', { class: 'wrap auth' }, form);
  }

  /* ------------------------------------------------------------------ */
  /* Review form                                                         */
  /* ------------------------------------------------------------------ */
  const DISHES = ['Pad Kra Pao', 'Pad Thai', 'Som Tam', 'Khao Man Gai', 'Moo Ping', 'Kuay Teow Reua', 'Khao Soi', 'Tom Yum Goong',
    'Khao Pad', 'Gai Yang', 'Larb Moo', 'Sai Oua', 'Mango Sticky Rice', 'Roti Gluay', 'Kanom Krok', 'Satay', 'Pad See Ew', 'Rad Na',
    'Khao Moo Daeng', 'Tom Kha Gai', 'Khao Kha Moo', 'Hoy Tod', 'Pad Kee Mao', 'Khanom Jeen'];

  async function shrinkImage(file, maxSide = 1280, quality = 0.85) {
    const bmp = await createImageBitmap(file);
    const scale = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(bmp.width * scale));
    canvas.height = Math.max(1, Math.round(bmp.height * scale));
    canvas.getContext('2d').drawImage(bmp, 0, 0, canvas.width, canvas.height);
    return new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('encode'))), 'image/jpeg', quality));
  }

  function chipGroup(items, isOn, onPick, cls) {
    const buttons = items.map((it) => el('button', {
      type: 'button', class: 'chip ' + (cls || ''), 'aria-pressed': 'false',
      onClick: () => { onPick(it.key); paint(); },
    }, it.icon && el('span', { class: 'chip-ic', 'aria-hidden': 'true' }, it.icon), it.label));
    const node = el('div', { class: 'chips', role: 'group' }, buttons);
    function paint() { buttons.forEach((b, i) => b.setAttribute('aria-pressed', String(isOn(items[i].key)))); }
    paint();
    return { node, paint };
  }

  async function viewForm(id) {
    if (!state.user) {
      state.next = location.hash || '#/new';
      toast(t('err.unauthorized'));
      location.hash = '#/login';
      return el('div');
    }
    let editing = null;
    if (id) {
      editing = (await api('GET', '/api/reviews/' + id)).review;
      if (editing.author.username.toLowerCase() !== state.user.username.toLowerCase()) return errorBox({ code: 'forbidden' });
    }
    let restaurants = [];
    try { restaurants = (await api('GET', '/api/restaurants')).restaurants; } catch (e) { /* suggestions are optional */ }
    setTitle(t(editing ? 'form.editTitle' : 'form.newTitle'));

    const f = editing
      ? {
        restaurant_name: editing.restaurant_name, area: editing.area, menu_item: editing.menu_item, rating: editing.rating,
        category: editing.category || 'other', catTouched: true, price_thb: String(editing.price_thb), spice: editing.spice, payments: [...editing.payments], open_days: [...editing.open_days],
        open_time: editing.open_time, close_time: editing.close_time, body: editing.body,
        photo: editing.photo ? editing.photo.split('/').pop() : null, photoUrl: editing.photo, photoBlob: null,
      }
      : {
        restaurant_name: '', area: '', menu_item: '', rating: 0, category: 'other', catTouched: false, price_thb: '', spice: 0, payments: [], open_days: [],
        open_time: '', close_time: '', body: '', photo: null, photoUrl: null, photoBlob: null,
      };

    const inputs = {};
    const groups = {};
    let autofilledFor = '';

    const clearErr = (name) => setFieldError(form, name, null);
    const text = (name, o = {}) => {
      const i = el('input', {
        class: 'input', id: 'in-' + name, name, type: 'text', autocomplete: 'off', maxlength: o.max, list: o.list,
        placeholder: o.placeholder, inputmode: o.inputmode,
      });
      i.value = f[name] ?? '';
      i.addEventListener('input', () => {
        if (o.digits) i.value = i.value.replace(/\D/g, '').slice(0, 5);
        f[name] = i.value;
        clearErr(name);
        if (o.onInput) o.onInput();
      });
      if (o.onChange) i.addEventListener('change', o.onChange);
      inputs[name] = i;
      return i;
    };

    function paintSummary() {
      const ok = f.open_days.length && f.open_time && f.close_time && f.open_time !== f.close_time;
      summary.textContent = ok
        ? t('f.summary', { days: fmtDays(f.open_days), open: f.open_time, close: f.close_time }) + (f.close_time < f.open_time ? ' ' + t('f.closesNext') : '')
        : '';
      summary.hidden = !ok;
    }

    function maybeAutofill() {
      if (editing) return;
      const key = f.restaurant_name.trim().toLowerCase();
      if (!key || autofilledFor === key) return;
      const hit = restaurants.find((r) => r.name.toLowerCase() === key);
      if (!hit) return;
      autofilledFor = key;
      let used = false;
      if (!f.area && hit.area) { f.area = hit.area; inputs.area.value = hit.area; used = true; }
      if (!f.payments.length) { f.payments = [...hit.payments]; groups.payments.paint(); used = true; }
      if (!f.open_days.length) { f.open_days = [...hit.open_days]; groups.days.paint(); used = true; }
      if (!f.open_time && !f.close_time) {
        f.open_time = hit.open_time; f.close_time = hit.close_time;
        inputs.open_time.value = hit.open_time; inputs.close_time.value = hit.close_time; used = true;
      }
      if (used) {
        ['area', 'payments', 'open_days', 'open_time', 'close_time'].forEach(clearErr);
        paintSummary();
        toast(t('autofilled'));
      }
    }

    /* --- step 1: where --- */
    const dlRestaurants = el('datalist', { id: 'dl-restaurants' }, restaurants.map((r) => el('option', { value: r.name })));
    const dlDishes = el('datalist', { id: 'dl-dishes' }, DISHES.map((d) => el('option', { value: d })));

    /* --- rating --- */
    const ratingText = el('span', { class: 'rating-text' });
    const ratingBtns = [1, 2, 3, 4, 5].map((n) => el('button', {
      type: 'button', role: 'radio', class: 'flame-btn', 'aria-checked': 'false', 'aria-label': `${n}/5 — ${t('rate.' + n)}`,
      onClick: () => { f.rating = n; clearErr('rating'); paintRating(); },
    }, icon('flame')));
    function paintRating() {
      ratingBtns.forEach((b, i) => { b.classList.toggle('on', i < f.rating); b.setAttribute('aria-checked', String(i + 1 === f.rating)); });
      ratingText.textContent = f.rating ? `${f.rating}/5 · ${t('rate.' + f.rating)}` : t('f.ratingPick');
    }
    const ratingBox = el('div', { class: 'rating-pick' }, el('div', { class: 'flame-row', role: 'radiogroup', 'aria-labelledby': 'lbl-rating' }, ratingBtns), ratingText);

    /* --- price chips --- */
    const priceQuick = el('div', { class: 'chips quick' }, [30, 40, 50, 60, 80, 100].map((n) => el('button', {
      type: 'button', class: 'chip chip-sm', onClick: () => { f.price_thb = String(n); inputs.price_thb.value = String(n); clearErr('price_thb'); },
    }, baht(n))));
    const priceInput = text('price_thb', { max: 5, digits: true, inputmode: 'numeric', placeholder: '60' });

    /* --- category (auto-guessed from the menu name until the poster picks one) --- */
    groups.category = chipGroup(CATS.map((k) => ({ key: k, icon: CAT_ICON[k], label: t('cat.' + k) })),
      (k) => f.category === k, (k) => { f.category = k; f.catTouched = true; clearErr('category'); }, 'chip-pay');
    function suggestCategory() {
      if (f.catTouched) return;
      f.category = guessCategory(f.menu_item);
      groups.category.paint();
    }

    /* --- spice, payments, days --- */
    groups.spice = chipGroup([0, 1, 2, 3].map((n) => ({ key: n, icon: n ? '🌶️'.repeat(n) : '🙂', label: t('spice.' + n) })),
      (k) => f.spice === k, (k) => { f.spice = k; }, 'chip-spice');

    groups.payments = chipGroup(['cash_only', 'qr', 'card'].map((k) => ({ key: k, icon: PAY_ICON[k], label: t('pay.' + k) })),
      (k) => f.payments.includes(k),
      (k) => {
        const s = new Set(f.payments);
        if (k === 'cash_only') { if (s.has(k)) s.delete(k); else { s.clear(); s.add(k); } }
        else { s.delete('cash_only'); if (s.has(k)) s.delete(k); else s.add(k); }
        f.payments = [...s];
        clearErr('payments');
      }, 'chip-pay');

    groups.days = chipGroup(DAYS.map((d) => ({ key: d, label: t('day.' + d) })),
      (k) => f.open_days.includes(k),
      (k) => {
        f.open_days = f.open_days.includes(k) ? f.open_days.filter((d) => d !== k) : DAYS.filter((d) => d === k || f.open_days.includes(d));
        clearErr('open_days');
        paintSummary();
      }, 'chip-day');

    const presets = [['preset.all', DAYS], ['preset.mon_sat', DAYS.slice(0, 6)], ['preset.mon_fri', DAYS.slice(0, 5)], ['preset.weekend', DAYS.slice(5)]];
    const presetRow = el('div', { class: 'presets' }, el('span', null, t('preset.label')), presets.map(([k, days]) => el('button', {
      type: 'button', class: 'link-btn', onClick: () => { f.open_days = [...days]; groups.days.paint(); clearErr('open_days'); paintSummary(); },
    }, t(k))));

    const timeInput = (name) => {
      const i = el('input', { class: 'input', id: 'in-' + name, name, type: 'time' });
      i.value = f[name] || '';
      i.addEventListener('input', () => { f[name] = i.value; clearErr('open_time'); clearErr('close_time'); paintSummary(); });
      inputs[name] = i;
      return i;
    };
    const summary = el('p', { class: 'summary', hidden: true });

    /* --- photo --- */
    const photoPreview = el('div', { class: 'photo-preview' });
    const photoFile = el('input', { type: 'file', accept: 'image/*', id: 'in-photo', class: 'sr-only' });
    const photoPick = el('label', { class: 'photo-pick', for: 'in-photo' }, icon('camera'), el('span', null, t('f.photoAdd')));
    function paintPhoto() {
      photoPreview.replaceChildren();
      photoPick.hidden = !!f.photoUrl;
      if (f.photoUrl) {
        photoPreview.append(
          el('img', { src: f.photoUrl, alt: t('f.photoAlt') }),
          el('button', {
            type: 'button', class: 'btn btn-ghost btn-sm',
            onClick: () => {
              if (f.photoUrl && f.photoUrl.startsWith('blob:')) URL.revokeObjectURL(f.photoUrl);
              f.photo = null; f.photoUrl = null; f.photoBlob = null; photoFile.value = ''; paintPhoto();
            },
          }, icon('trash'), t('f.photoRemove')));
      }
    }
    photoFile.addEventListener('change', async () => {
      const file = photoFile.files[0];
      if (!file) return;
      try {
        if (!file.type.startsWith('image/')) throw new Error('type');
        const blob = await shrinkImage(file);
        if (blob.size > 3 * 1024 * 1024) throw new Error('size');
        if (f.photoUrl && f.photoUrl.startsWith('blob:')) URL.revokeObjectURL(f.photoUrl);
        f.photoBlob = blob; f.photo = null; f.photoUrl = URL.createObjectURL(blob);
        clearErr('photo');
        paintPhoto();
      } catch (e) {
        setFieldError(form, 'photo', 'bad_image');
      }
    });

    /* --- body --- */
    const bodyArea = el('textarea', { class: 'input', id: 'in-body', name: 'body', rows: '5', maxlength: '1000', placeholder: t('f.bodyPlaceholder') });
    bodyArea.value = f.body;
    const bodyCount = el('span', { class: 'counter' }, `${f.body.length}/1000`);
    bodyArea.addEventListener('input', () => { f.body = bodyArea.value; bodyCount.textContent = `${f.body.length}/1000`; clearErr('body'); });

    /* --- assemble --- */
    const panel = (n, title, ...kids) => el('section', { class: 'panel' },
      el('h2', { class: 'panel-title' }, el('span', { class: 'step' }, String(n)), title), kids);

    const submitBtn = el('button', { class: 'btn btn-primary btn-lg', type: 'submit' }, t(editing ? 'f.save' : 'f.publish'));

    const form = el('form', { class: 'form', novalidate: true, onSubmit: (e) => { e.preventDefault(); submit(); } },
      dlRestaurants, dlDishes,
      el('div', { class: 'form-head' }, el('h1', null, t(editing ? 'form.editTitle' : 'form.newTitle')), el('p', { class: 'sub' }, t('form.sub'))),
      panel(1, t('form.s1'),
        field('restaurant_name', t('f.restaurant'), text('restaurant_name', { max: 80, list: 'dl-restaurants', placeholder: t('f.restaurantPh'), onInput: maybeAutofill, onChange: maybeAutofill }), { required: true, hint: editing ? null : t('f.restaurantHint') }),
        field('area', t('f.area'), text('area', { max: 80, placeholder: t('f.areaPh') }), { optional: true })),
      panel(2, t('form.s2'),
        field('menu_item', t('f.menu'), text('menu_item', { max: 80, list: 'dl-dishes', placeholder: t('f.menuPh'), onInput: suggestCategory, onChange: suggestCategory }), { required: true }),
        field('category', t('f.category'), groups.category.node, { required: true, group: true, hint: t('f.categoryHint') }),
        field('price_thb', t('f.price'), el('div', null, el('div', { class: 'affix' }, el('span', { class: 'affix-sym', 'aria-hidden': 'true' }, '฿'), priceInput), priceQuick), { required: true }),
        field('rating', t('f.rating'), ratingBox, { required: true, group: true }),
        field('spice', t('f.spice'), groups.spice.node, { group: true, optional: true }),
        field('photo', t('f.photo'), el('div', null, photoPick, photoFile, photoPreview), { group: true, optional: true, hint: t('f.photoHint') }),
        field('body', t('f.body'), el('div', null, bodyArea, bodyCount), { optional: true })),
      panel(3, t('form.s3'),
        field('payments', t('f.payments'), groups.payments.node, { required: true, group: true, hint: t('f.paymentsHint') })),
      panel(4, t('form.s4'),
        field('open_days', t('f.days'), el('div', null, groups.days.node, presetRow), { required: true, group: true }),
        el('div', { class: 'time-row' },
          field('open_time', t('f.opens'), timeInput('open_time'), { required: true }),
          field('close_time', t('f.closes'), timeInput('close_time'), { required: true })),
        el('p', { class: 'hint' }, t('f.closesHint')),
        summary),
      el('div', { class: 'submit-bar' },
        el('a', { class: 'btn btn-ghost', href: editing ? '#/review/' + editing.id : '#/' }, t('f.cancel')),
        submitBtn));

    function validate() {
      const e = {};
      const clean = (s) => s.trim().replace(/\s+/g, ' ');
      const name = clean(f.restaurant_name);
      if (!name) e.restaurant_name = 'required'; else if (name.length < 2) e.restaurant_name = 'too_short';
      const menu = clean(f.menu_item);
      if (!menu) e.menu_item = 'required'; else if (menu.length < 2) e.menu_item = 'too_short';
      const price = Number(f.price_thb);
      if (!f.price_thb) e.price_thb = 'required'; else if (!(price >= 1 && price <= 10000)) e.price_thb = 'invalid';
      if (!f.rating) e.rating = 'required';
      if (!f.payments.length) e.payments = 'pick_one';
      if (!f.open_days.length) e.open_days = 'pick_one';
      if (!f.open_time) e.open_time = 'required';
      if (!f.close_time) e.close_time = 'required';
      else if (f.open_time && f.open_time === f.close_time) e.close_time = 'same_time';
      return e;
    }

    function showErrors(errs) {
      const names = Object.keys(errs);
      names.forEach((n) => setFieldError(form, n, errs[n]));
      const first = names.map((n) => $(`[data-field="${n}"]`, form)).filter(Boolean)
        .sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1))[0];
      if (first) {
        first.scrollIntoView({ behavior: 'smooth', block: 'center' });
        const focusable = $('input, textarea, button', first);
        if (focusable) focusable.focus({ preventScroll: true });
      }
    }

    async function submit() {
      Object.keys({ restaurant_name: 1, area: 1, menu_item: 1, category: 1, price_thb: 1, rating: 1, spice: 1, photo: 1, body: 1, payments: 1, open_days: 1, open_time: 1, close_time: 1 })
        .forEach((n) => setFieldError(form, n, null));
      const errs = validate();
      if (Object.keys(errs).length) { showErrors(errs); toast(t('f.fixErrors')); return; }
      submitBtn.disabled = true;
      submitBtn.textContent = t('f.saving');
      try {
        if (f.photoBlob) {
          const up = await api('POST', '/api/upload', undefined, f.photoBlob);
          f.photo = up.photo; f.photoBlob = null;
        }
        const payload = {
          restaurant_name: f.restaurant_name, area: f.area, menu_item: f.menu_item, category: f.category, rating: f.rating,
          price_thb: Number(f.price_thb), spice: f.spice, payments: f.payments, open_days: f.open_days,
          open_time: f.open_time, close_time: f.close_time, body: f.body, photo: f.photo,
        };
        const res = editing
          ? await api('PUT', '/api/reviews/' + editing.id, payload)
          : await api('POST', '/api/reviews', payload);
        toast(t(editing ? 'saved' : 'published'));
        location.hash = '#/review/' + res.review.id;
      } catch (err) {
        if (err.code === 'validation') showErrors(err.fields);
        else handleErr(err);
        submitBtn.disabled = false;
        submitBtn.textContent = t(editing ? 'f.save' : 'f.publish');
      }
    }

    paintRating(); paintPhoto(); paintSummary();
    return el('div', { class: 'wrap form-wrap' }, form);
  }

  /* ------------------------------------------------------------------ */
  /* Router                                                              */
  /* ------------------------------------------------------------------ */
  const routes = [
    [/^\/?$/, viewHome],
    [/^\/review\/(\d+)$/, viewReview],
    [/^\/new$/, () => viewForm(null)],
    [/^\/edit\/(\d+)$/, (id) => viewForm(id)],
    [/^\/user\/([A-Za-z0-9_]+)$/, viewUser],
    [/^\/login$/, () => authView('login')],
    [/^\/signup$/, () => authView('signup')],
  ];
  let renderToken = 0;
  let firstRender = true;

  async function render() {
    const token = ++renderToken;
    const main = $('#main');
    const path = (location.hash.replace(/^#/, '') || '/').split('?')[0];
    document.body.dataset.route = /^\/(new|edit)/.test(path) ? 'form' : /^\/(login|signup)$/.test(path) ? 'auth' : 'page';
    renderTabbar();
    renderInstall();
    main.replaceChildren(loadingNode());
    let node;
    const hit = routes.find(([rx]) => rx.test(path));
    try {
      node = hit ? await hit[1](...(path.match(hit[0]).slice(1))) : errorBox({ code: 'not_found' });
    } catch (e) {
      node = errorBox(e, render);
    }
    if (token !== renderToken) return;
    main.replaceChildren(node);
    window.scrollTo(0, 0);
    if (!firstRender) main.focus({ preventScroll: true });
    firstRender = false;
  }

  async function init() {
    try { state.user = (await api('GET', '/api/me')).user; } catch (e) { /* offline: stay logged out */ }
    renderChrome();
    window.addEventListener('hashchange', render);
    PWA.onChange(renderChrome);
    window.addEventListener('offline', renderNet);
    window.addEventListener('online', () => {
      renderNet();
      toast(t('backOnline'));
      // refresh the page we are on, but never wipe a half-written review or login form
      if (!['form', 'auth'].includes(document.body.dataset.route)) render();
    });
    render();
  }

  init();
})();
