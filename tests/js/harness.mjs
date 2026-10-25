/**
 * Loads static/app.js inside a jsdom window and gives the test a controllable
 * world: a stubbed fetch, a populated state object, and helpers for driving
 * the app the way a user would.
 */
import { JSDOM } from 'jsdom';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.resolve(HERE, '..', '..', 'static');

/** Every window opened by loadApp, so the runner can shut them all down. */
const open = new Set();

/**
 * Close every jsdom window created by loadApp.
 *
 * `pretendToBeVisual: true` starts a requestAnimationFrame loop and jsdom
 * keeps pending timers alive, so without this the test process never exits even
 * though all tests have finished. Call it from a global `after` hook.
 */
export function closeAll() {
  for (const dom of open) {
    try { dom.window.close(); } catch { /* already torn down */ }
  }
  open.clear();
}

export function loadApp({ routes = {}, me = defaultUser() } = {}) {
  const dom = new JSDOM(
    `<!doctype html><html><body>
       <div id="root"></div>
       <div id="toast"></div>
       <button id="theme-toggle" type="button"></button>
       <datalist id="user-list"></datalist>
     </body></html>`,
    // 'dangerously' is required so the app is loaded as a real classic script
    // and inline onclick handlers actually execute, exactly as in a browser.
    { runScripts: 'dangerously', url: 'http://localhost/', pretendToBeVisual: true },
  );

  const { window } = dom;
  const calls = [];
  const clock = { now: 0 };

  window.fetch = async (url, opts = {}) => {
    const pathOnly = String(url).split('?')[0];
    calls.push({ url: String(url), path: pathOnly, method: (opts.method || 'GET').toUpperCase(), opts });
    const handler = routes[pathOnly];
    if (!handler) {
      return jsonResponse(window, 404, { detail: 'no route: ' + pathOnly });
    }
    const result = typeof handler === 'function' ? await handler(String(url), opts) : handler;
    if (result && (result.__raw || typeof result.json === 'function')) return result.__raw || result;
    return jsonResponse(window, result?.status || 200, result?.body ?? {});
  };

  window.matchMedia = window.matchMedia || (() => ({
    matches: false, addEventListener() {}, removeEventListener() {},
  }));
  window.navigator.clipboard = { writeText: async () => {} };
  window.CSS = window.CSS || {};
  window.CSS.escape = window.CSS.escape || (s => String(s).replace(/[^\w-]/g, ch => '\\' + ch));
  // jsdom has no layout, so scrollTop is a plain property; that is enough to
  // prove the app writes the saved value back.
  window.Element.prototype.scrollTo = function () {};

  // Inject as classic <script>s so top-level `const`/`function` bindings land
  // in the window's global lexical scope (same as the browser), which inline
  // onclick handlers rely on. Load i18n + locales before the app.
  const inject = (rel) => {
    const el = window.document.createElement('script');
    el.textContent = fs.readFileSync(path.join(STATIC, rel), 'utf8');
    window.document.head.appendChild(el);
  };
  // Default tests to Vietnamese so assertions on Vietnamese UI copy stay valid;
  // i18n coverage flips locale explicitly in dedicated tests.
  try { window.localStorage.setItem('otp_locale', 'vi'); } catch { /* ignore */ }
  inject('i18n.js');
  inject('locales/en.js');
  inject('locales/vi.js');
  inject('locales/zh.js');
  inject('locales/ru.js');
  inject('app.js');
  open.add(dom);

  // Mirror the bindings onto the window object so tests can reach them by
  // name. In the browser `window.S` is undefined (const does not create a
  // window property) but bare `S` resolves everywhere; the mirror only exists
  // for the tests' convenience.
  const S = window.eval('S');
  window.S = S;
  for (const name of MIRRORED) {
    const value = window.eval(name);
    if (value !== undefined) window[name] = value;
  }

  // init() runs at load; let its microtasks settle before the test inspects.
  return settle().then(() => ({ dom, window, S, calls, clock, me }));
}

const MIRRORED = [
  'esc', 'escapeRegExp', 'highlight', 'fmt', 'debounce', 'toast', 'modal', 'closeModal',
  'render', 'renderAuth', 'renderLogin', 'renderSidebar', 'renderInbox', 'renderPageView',
  'renderMailboxesPage', 'renderReader', 'renderAccountPage', 'renderReferralsPage',
  'renderAdminReferralSection', 'renderUsersPage', 'renderRulesPage',
  'filteredMailboxes', 'mailboxRows', 'loadMessages', 'loadAuthConfig', 'refreshData',
  'onMailboxFilter', 'onMailboxTableFilter', 'clearMailboxFilter', 'clearMailboxTableFilter',
  'onMessageSearch', 'submitMessageSearch', 'clearMessageSearch',
  'showMoreMailboxes', 'showMoreMailboxRows',
  'applyTheme', 'toggleTheme', 'currentTheme', 'fillReader', 'logout', 'login', 'register',
  'showRegister', 'showLogin', 'goView', 'goAllMail', 'openMailbox', 'closeReader',
  'captureScroll', 'restoreScroll', 'captureFocus', 'restoreFocus',
  'setSelectedMailbox', 'toggleAllMailboxes', 'bulkMessageState', 'setUserCodePermission',
  'newRuleModal', 'editRuleModal', 'ruleTestModal', 'updateRuleTypeUI', 'setRulePreset',
  'toggleUnassignedFilter', 'bulkReturnToRules', 'returnOneToRules', 'testRules',
  'renderHeader',
];

function defaultUser() {
  return {
    id: 1, public_id: 'A-ROOT', username: 'rootadmin', role: 'admin',
    is_active: true, can_generate_codes: true, created_via: 'admin',
  };
}

function jsonResponse(window, status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

/** Let queued microtasks (and any zero-delay timers) run. */
export function settle(times = 6) {
  let p = Promise.resolve();
  for (let i = 0; i < times; i++) p = p.then(() => new Promise(r => setTimeout(r, 0)));
  return p;
}

export function advance(ms) {
  return new Promise(r => setTimeout(r, ms));
}

export function dashboard({ mailboxes = [], users = [], rules = [], notifications = [], audit = [] } = {}) {
  return {
    mailboxes, users, rules, notifications, audit,
    referral: { global_enabled: true, can_generate: true, permission_active: true, reason: null, code: null, totals: { codes: 0, capacity: 0, uses: 0 }, registrations: [] },
  };
}

export function mailbox(over = {}) {
  return {
    id: 1, address: 'a@example.com', owner_username: null, owner_user_id: null,
    exact_override: false, override_reason: null, is_owner: false, users: [],
    message_count: 0, unread_count: 0, latest: null, ...over,
  };
}

export function message(over = {}) {
  return {
    id: 1, mailbox_id: 1, mailbox_address: 'a@example.com',
    envelope_from: 'from@example.com', envelope_to: 'a@example.com',
    subject: 'Subject', received_at: '2024-01-01T00:00:00+00:00',
    otp: '', has_attachments: 0, is_read: 0, preview: 'preview text', ...over,
  };
}

export function setState(window, patch) {
  Object.assign(window.S, patch);
}

export function appPath(window, sel) {
  return window.document.querySelector(sel);
}
