import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { loadApp, settle, advance, dashboard, mailbox, message, setState, closeAll } from './harness.mjs';

// jsdom keeps timers alive; without this the runner never exits.
after(closeAll);

const ME = { id: 1, public_id: 'A-ROOT', username: 'rootadmin', role: 'admin', is_active: true, can_generate_codes: true, created_via: 'admin' };

function boot(over = {}) {
  const mailboxes = over.mailboxes || [mailbox({ id: 1, address: 'alpha@example.com' })];
  const routes = {
    '/api/me': { body: { user: ME, csrf: 'csrf', referral: null, registration_open: false } },
    '/api/dashboard': { body: dashboard({ mailboxes, rules: over.rules || [], users: over.users || [] }) },
    '/api/messages': { body: { items: [], total: 0, page: 1, page_size: 30, engine: 'fts' } },
    ...(over.routes || {}),
  };
  return loadApp({ routes, me: ME });
}

/* ------------------------------------------------------------------ utils */

test('highlight escapes markup before inserting <mark>', async () => {
  const { window } = await boot();
  const h = window.highlight;
  assert.equal(h('Hello World', 'world'), 'Hello <mark>World</mark>');
  assert.equal(h('plain', 'plain'), '<mark>plain</mark>');
  // A search term that looks like markup must never become live markup.
  const out = h('<img src=x onerror=alert(1)>', '<img');
  const probe = window.document.createElement('div');
  probe.innerHTML = out;
  assert.equal(probe.querySelector('img'), null, 'no element may be created');
  assert.equal(probe.textContent, '<img src=x onerror=alert(1)>', 'text is preserved verbatim');
  assert.ok(out.includes('<mark>'));
  assert.ok(!h('<b>x</b>', '<b>').includes('<b>'));
  assert.equal(h('a b', ''), 'a b');
  assert.equal(h(null, 'x'), '');
});

test('highlight treats the query as a literal, not a pattern', async () => {
  const { window } = await boot();
  // Regex metacharacters in the query must not be interpreted.
  assert.equal(window.highlight('a.b', '.'), 'a<mark>.</mark>b');
  assert.equal(window.highlight('abc', '.*'), 'abc');
  assert.equal(window.highlight('a+b', 'a+'), '<mark>a+</mark>b');
});

test('highlight is case-insensitive and handles repeated matches', async () => {
  const { window } = await boot();
  assert.equal(window.highlight('Foo foo FOO', 'foo'),
    '<mark>Foo</mark> <mark>foo</mark> <mark>FOO</mark>');
});

test('highlight bounds the number of terms', async () => {
  const { window } = await boot();
  const out = window.highlight('aa bb cc dd ee ff gg hh ii jj', 'aa bb cc dd ee ff gg hh ii jj');
  assert.ok(out.split('<mark>').length - 1 <= 8);
});

test('esc neutralises every dangerous character', async () => {
  const { window } = await boot();
  assert.equal(window.esc(`<>&"'`), '&lt;&gt;&amp;&quot;&#39;');
  assert.equal(window.esc(null), '');
  assert.equal(window.esc(undefined), '');
});

/* ----------------------------------------------------------------- search */

test('sidebar search filters the mailbox list', async () => {
  const { window } = await boot({
    mailboxes: [
      mailbox({ id: 1, address: 'alpha@example.com' }),
      mailbox({ id: 2, address: 'beta@example.com' }),
      mailbox({ id: 3, address: 'alphabet@other.com' }),
    ],
  });
  setState(window, { view: 'inbox' });
  window.render();
  assert.equal(window.document.querySelectorAll('.mailbox-row').length, 3);

  window.onMailboxFilter('beta');
  const rows = [...window.document.querySelectorAll('.mailbox-row .address')];
  assert.equal(rows.length, 1);
  assert.ok(rows[0].textContent.includes('beta@example.com'));
  assert.ok(window.document.querySelector('.mailbox-row mark'), 'match should be highlighted');
});

test('sidebar search is case-insensitive and multi-term', async () => {
  const { window } = await boot({
    mailboxes: [
      mailbox({ id: 1, address: 'alpha@example.com' }),
      mailbox({ id: 2, address: 'beta@example.com' }),
      mailbox({ id: 3, address: 'gamma@other.com' }),
    ],
  });
  window.onMailboxFilter('ALPHA');
  assert.equal(window.document.querySelectorAll('.mailbox-row').length, 1);
  window.onMailboxFilter('   ');
  assert.equal(window.document.querySelectorAll('.mailbox-row').length, 3, 'blank query shows all');
  window.onMailboxFilter('example.com o');
  assert.equal(window.document.querySelectorAll('.mailbox-row').length, 2, 'terms are ANDed');
});

test('sidebar search with no match shows an empty state, not a blank list', async () => {
  const { window } = await boot();
  window.onMailboxFilter('zzzz');
  assert.equal(window.document.querySelectorAll('.mailbox-row').length, 0);
  const empty = window.document.querySelector('.mailboxes .empty');
  assert.ok(empty, 'expected an empty-state message');
  assert.ok(empty.textContent.includes('Không có mailbox'));
});

test('sidebar search keeps the input value and focus across re-render', async () => {
  const { window } = await boot();
  window.onMailboxFilter('alph');
  const input = window.document.querySelector('[data-focus-key="sidebar-q"]');
  assert.ok(input, 'search input should be present');
  assert.equal(input.value, 'alph');
});

test('sidebar shows a match count out of the total', async () => {
  const { window } = await boot({
    mailboxes: [
      mailbox({ id: 1, address: 'alpha@x.com' }),
      mailbox({ id: 2, address: 'alpha@y.com' }),
      mailbox({ id: 3, address: 'zeta@z.com' }),
    ],
  });
  window.onMailboxFilter('alpha');
  const badge = [...window.document.querySelectorAll('.section-head .tiny')]
    .find(el => el.textContent.includes('/'));
  assert.ok(badge, 'expected a "matches / total" counter');
  assert.equal(badge.textContent.trim(), '2 / 3');
});

test('sidebar is not silently capped at a fixed number of mailboxes', async () => {
  // The previous build rendered only the first 120 and dropped the rest.
  const many = Array.from({ length: 250 }, (_, i) => mailbox({ id: i + 1, address: `box${i}@example.com` }));
  const { window } = await boot({ mailboxes: many });
  assert.equal(window.document.querySelectorAll('.mailbox-row').length, 60, 'renders a first page');
  assert.ok(window.document.querySelector('.list-more'), 'offers to load more');
  window.S.mailboxLimit += 120;
  window.render();
  assert.equal(window.document.querySelectorAll('.mailbox-row').length, 180);
  window.S.mailboxLimit += 120;
  window.render();
  assert.equal(window.document.querySelectorAll('.mailbox-row').length, 250, 'all mailboxes reachable');
});

test('mailbox table search filters rows and counts matches', async () => {
  const { window } = await boot({
    mailboxes: [
      mailbox({ id: 1, address: 'alpha@example.com', owner_username: 'ann' }),
      mailbox({ id: 2, address: 'beta@example.com', owner_username: 'bob' }),
      mailbox({ id: 3, address: 'gamma@x.com', exact_override: true }),
    ],
  });
  window.S.view = 'mailboxes';
  window.render();
  assert.equal(window.document.querySelectorAll('tbody tr').length, 3);

  window.onMailboxTableFilter('bob');
  const rows = window.document.querySelectorAll('tbody tr');
  assert.equal(rows.length, 1);
  assert.ok(rows[0].textContent.includes('beta@example.com'));
  assert.ok(window.document.querySelector('tbody mark'), 'owner match is highlighted');

  // The filter reaches fields other than the address.
  window.onMailboxTableFilter('exact');
  assert.equal(window.document.querySelectorAll('tbody tr').length, 1);
  window.onMailboxTableFilter('nomatch');
  // With no matches the table keeps a single row holding the empty state, so
  // there must be no data rows left.
  assert.ok(window.document.querySelector('tbody .empty'));
  const dataRows = [...window.document.querySelectorAll('tbody tr')]
    .filter(tr => !tr.querySelector('.empty'));
  assert.equal(dataRows.length, 0);
});

test('mailbox table search survives a re-render', async () => {
  const { window } = await boot({
    mailboxes: [mailbox({ id: 1, address: 'alpha@x.com' }), mailbox({ id: 2, address: 'beta@x.com' })],
  });
  window.S.view = 'mailboxes';
  window.onMailboxTableFilter('alpha');
  window.render();               // e.g. after a background refresh
  assert.equal(window.document.querySelector('[data-focus-key="mailbox-q"]').value, 'alpha');
  assert.equal(window.document.querySelectorAll('tbody tr').length, 1);
});

test('clearing a search restores the full list', async () => {
  const { window } = await boot({
    mailboxes: [mailbox({ id: 1, address: 'alpha@x.com' }), mailbox({ id: 2, address: 'beta@x.com' })],
  });
  window.S.view = 'mailboxes';
  window.onMailboxTableFilter('alpha');
  assert.equal(window.document.querySelectorAll('tbody tr').length, 1);
  window.onMailboxTableFilter('');
  assert.equal(window.document.querySelectorAll('tbody tr').length, 2);
});

/* ----------------------------------------------------------------- scroll */

test('render preserves the scroll offset of every scroll container', async () => {
  const { window } = await boot({
    mailboxes: Array.from({ length: 40 }, (_, i) => mailbox({ id: i + 1, address: `box${i}@x.com` })),
  });
  const doc = window.document;
  const list = doc.querySelector('[data-scroll="list"]');
  const boxes = doc.querySelector('[data-scroll="mailboxes"]');
  assert.ok(list && boxes, 'inbox scroll containers must be tagged');
  // The page container only exists on the management views.
  assert.equal(doc.querySelector('[data-scroll="page"]'), null);

  list.scrollTop = 120;
  boxes.scrollTop = 45;
  window.render();
  assert.equal(doc.querySelector('[data-scroll="list"]').scrollTop, 120);
  assert.equal(doc.querySelector('[data-scroll="mailboxes"]').scrollTop, 45);

  window.S.view = 'mailboxes';
  window.render();
  const p2 = doc.querySelector('[data-scroll="page"]');
  assert.ok(p2, 'the page container must be tagged');
  p2.scrollTop = 80;
  window.render();
  assert.equal(doc.querySelector('[data-scroll="page"]').scrollTop, 80);
});

test('scroll position is not carried into a different container', async () => {
  const { window } = await boot();
  const doc = window.document;
  doc.querySelector('[data-scroll="list"]').scrollTop = 300;
  window.S.view = 'mailboxes';
  window.render();
  assert.equal(doc.querySelector('[data-scroll="page"]').scrollTop, 0);
});

test('re-rendering while typing does not lose the caret', async () => {
  const { window } = await boot();
  const doc = window.document;
  const input = doc.querySelector('[data-focus-key="msg-q"]');
  input.focus();

  // Type the way a browser does: set the value, then fire `input`, which is
  // what the app's oninput handler listens for.
  input.value = 'hello world';
  input.dispatchEvent(new window.Event('input', { bubbles: true }));
  input.setSelectionRange(6, 11);
  assert.equal(window.S.q, 'hello world', 'the handler should have captured the value');

  // A re-render lands mid-typing (background refresh, a checkbox toggle, ...).
  window.render();
  const after = doc.querySelector('[data-focus-key="msg-q"]');
  assert.equal(after.value, 'hello world', 'typed text must survive');
  assert.equal(doc.activeElement, after, 'focus must be restored');
  assert.equal(after.selectionStart, 6);
  assert.equal(after.selectionEnd, 11);
});

test('focus restoration is a no-op when the element is gone', async () => {
  const { window } = await boot();
  const doc = window.document;
  const input = doc.querySelector('[data-focus-key="msg-q"]');
  input.focus();
  window.S.selectedMessageId = null;
  window.S.view = 'mailboxes';
  assert.doesNotThrow(() => window.render());
});

/* -------------------------------------------------------- search requests */

test('message search is debounced and only fires one request', async () => {
  let requests = 0;
  const { window, calls } = await boot({
    routes: {
      '/api/messages': () => { requests++; return { body: { items: [], total: 0, page: 1, page_size: 30, engine: 'fts' } }; },
    },
  });
  const before = calls.filter(c => c.path === '/api/messages').length;
  window.onMessageSearch('a');
  window.onMessageSearch('ab');
  window.onMessageSearch('abc');
  assert.equal(calls.filter(c => c.path === '/api/messages').length, before, 'nothing sent while typing');
  await advance(350);
  await settle();
  assert.equal(requests, before + 1, 'exactly one request for the whole burst');
});

test('pressing Enter searches immediately, without waiting for the debounce', async () => {
  const { window, calls } = await boot();
  const before = calls.filter(c => c.path === '/api/messages').length;
  window.onMessageSearch('ab');
  window.submitMessageSearch();
  await settle();
  assert.equal(calls.filter(c => c.path === '/api/messages').length, before + 1);
});

test('clearing the search resets the term and refetches', async () => {
  const { window, calls } = await boot();
  window.S.q = 'noise';
  const before = calls.filter(c => c.path === '/api/messages').length;
  window.clearMessageSearch();
  assert.equal(window.S.q, '');
  assert.equal(window.S.page, 1);
  await settle();
  assert.equal(calls.filter(c => c.path === '/api/messages').length, before + 1);
});

test('a stale search response cannot overwrite a newer one', async () => {
  // The first request resolves slowly; the second resolves immediately.
  // Only the newer result may be rendered.
  const resolvers = [];
  const { window, calls } = await boot({
    routes: {
      '/api/messages': (url) => {
        const isStale = url.includes('q=old');
        if (isStale) {
          return new Promise(res => resolvers.push(() => res({
            ok: true, status: 200,
            json: async () => ({ items: [message({ id: 1, subject: 'OLD' })], total: 1, page: 1, page_size: 30 }),
          })));
        }
        return {
          ok: true, status: 200,
          json: async () => ({ items: [message({ id: 2, subject: 'NEW' })], total: 1, page: 1, page_size: 30 }),
        };
      },
    },
  });

  window.S.q = 'old';
  const stale = window.loadMessages();
  window.S.q = 'new';
  const fresh = window.loadMessages();
  await fresh;
  // Now let the slow one land.
  resolvers.forEach(fn => fn());
  await stale;
  await settle();

  assert.equal(window.S.messages.length, 1);
  assert.equal(window.S.messages[0].subject, 'NEW', 'stale response must be discarded');
});

test('a failing search does not wedge the loading flag', async () => {
  const { window } = await boot({
    routes: { '/api/messages': { status: 500, body: { detail: 'boom' } } },
  });
  await window.loadMessages();
  assert.equal(window.S.loading, false);
  assert.equal(window.S.messages.length, 0);
});

/* ------------------------------------------------------------- dark mode */

test('theme applies to documentElement, not body', async () => {
  const { window } = await boot();
  window.localStorage.setItem('otp-theme', 'dark');
  window.applyTheme();
  assert.equal(window.document.documentElement.classList.contains('dark'), true);
  assert.equal(window.document.body.classList.contains('dark'), false);
  assert.equal(window.document.getElementById('theme-toggle').textContent, '☀');
  assert.equal(window.document.getElementById('theme-toggle').getAttribute('aria-pressed'), 'true');
});

test('toggling the theme flips the class and the button', async () => {
  const { window } = await boot();
  window.localStorage.setItem('otp-theme', 'light');
  window.applyTheme();
  assert.equal(window.document.documentElement.classList.contains('dark'), false);
  window.toggleTheme();
  assert.equal(window.document.documentElement.classList.contains('dark'), true);
  assert.equal(window.localStorage.getItem('otp-theme'), 'dark');
  assert.equal(window.document.getElementById('theme-toggle').textContent, '☀');
  window.toggleTheme();
  assert.equal(window.document.documentElement.classList.contains('dark'), false);
  assert.equal(window.document.getElementById('theme-toggle').textContent, '☾');
});

test('theme defaults to the OS preference when nothing is stored', async () => {
  const { window } = await boot();
  window.localStorage.removeItem('otp-theme');
  window.applyTheme();
  assert.equal(window.document.documentElement.classList.contains('dark'), false);
});

test('the reader iframe is told which theme to render', async () => {
  const { window } = await boot({
    routes: {
      '/api/messages': { body: { items: [message({ id: 5, subject: 's' })], total: 1, page: 1, page_size: 30 } },
      '/api/messages/5': { body: { id: 5, body_text: 'plain', has_html: true, otp: '', attachments: [] } },
    },
  });
  setState(window, { selectedMessageId: 5, messages: [message({ id: 5, subject: 's' })] });
  window.localStorage.setItem('otp-theme', 'dark');
  window.applyTheme();
  window.render();
  await window.fillReader(5);
  await settle();
  const iframe = window.document.querySelector('.mail-html iframe');
  assert.ok(iframe, 'expected an html iframe');
  assert.match(iframe.getAttribute('src'), /theme=dark/);
});

/* ---------------------------------------------------------------- logout */

test('logout clears local state even if the request fails', async () => {
  const { window } = await boot();
  setState(window, {
    mailboxes: [mailbox()], messages: [message()], users: [{ public_id: 'U-1', username: 'x', role: 'user', is_active: true }],
    me: ME, csrf: 'csrf', checkedMailboxes: new Set([1]), checkedMessages: new Set([1]),
  });
  window.render();
  // Break the network for the logout call only.
  const original = window.fetch;
  window.fetch = async () => { throw new Error('network down'); };
  await window.logout();
  window.fetch = original;

  assert.equal(window.S.me, null, 'session must be dropped locally');
  assert.equal(window.S.csrf, null);
  assert.deepEqual([...window.S.mailboxes], []);
  assert.deepEqual([...window.S.messages], []);
  assert.deepEqual([...window.S.users], []);
  assert.equal(window.S.checkedMailboxes.size, 0);
  assert.equal(window.S.checkedMessages.size, 0);
  assert.ok(window.document.querySelector('[data-focus-key="login-user"]'), 'login screen shown');
});

test('a logout button exists and is reachable', async () => {
  const { window } = await boot();
  setState(window, { me: ME });
  window.render();
  const labels = [...window.document.querySelectorAll('.sidebar-bottom button')]
    .map(b => b.textContent.trim());
  assert.ok(labels.some(l => /thoát|đăng xuất/i.test(l)), `expected a logout control, saw ${labels}`);
  // And again on the account page, for discoverability.
  window.S.view = 'account';
  window.render();
  const page = [...window.document.querySelectorAll('.page-head button')]
    .map(b => b.textContent.trim());
  assert.ok(page.some(l => /đăng xuất/i.test(l)));
});

/* ---------------------------------------------------------- registration */

test('the register link only appears when registration is open', async () => {
  const closed = await boot({
    routes: {
      '/api/me': { status: 401, body: { detail: 'Authentication required' } },
      '/api/auth/config': { body: { registration_open: false, app: 'OTP' } },
    },
  });
  assert.ok(!closed.window.document.body.textContent.includes('Đăng ký bằng mã giới thiệu'));

  const open = await boot({
    routes: {
      '/api/me': { status: 401, body: { detail: 'Authentication required' } },
      '/api/auth/config': { body: { registration_open: true, app: 'OTP' } },
    },
  });
  assert.ok(open.window.document.body.textContent.includes('Đăng ký bằng mã giới thiệu'));
});

test('the register form posts username, password and code', async () => {
  const { window, calls } = await boot({
    routes: {
      '/api/me': { status: 401, body: { detail: 'Authentication required' } },
      '/api/auth/config': { body: { registration_open: true, app: 'OTP' } },
    },
  });
  window.showRegister();
  window.document.getElementById('reg-code').value = 'REF-ABCD-2345';
  window.document.getElementById('reg-user').value = 'newbie';
  window.document.getElementById('reg-pass').value = 'password-1234';
  window.register();
  await settle();
  const call = calls.find(c => c.path === '/api/register');
  assert.ok(call, 'expected a register request');
  const body = JSON.parse(call.opts.body);
  assert.equal(body.username, 'newbie');
  assert.equal(body.password, 'password-1234');
  assert.equal(body.referral_code, 'REF-ABCD-2345');
});

test('a registration error is shown in the form', async () => {
  const { window } = await boot({
    routes: {
      '/api/me': { status: 401, body: { detail: 'Authentication required' } },
      '/api/auth/config': { body: { registration_open: true, app: 'OTP' } },
      '/api/register': { status: 400, body: { detail: 'That referral code is not valid' } },
    },
  });
  window.showRegister();
  window.document.getElementById('reg-code').value = 'REF-XXXX-XXXX';
  window.document.getElementById('reg-user').value = 'newbie';
  window.document.getElementById('reg-pass').value = 'password-1234';
  await window.register();
  const msg = window.document.getElementById('auth-msg');
  assert.ok(msg, 'expected an inline error area');
  assert.match(msg.textContent, /not valid/);
});

test('a non-401 failure on /api/me is surfaced, not treated as logged out', async () => {
  const { window } = await boot({
    routes: {
      '/api/me': { status: 500, body: { detail: 'database locked' } },
      '/api/auth/config': { body: { registration_open: false, app: 'OTP' } },
    },
  });
  const toast = window.document.getElementById('toast');
  assert.match(toast.textContent, /database locked/);
});

/* ----------------------------------------------------------- routing rules */

test('admin sidebar includes Routing rules button and switches view', async () => {
  const { window } = await boot();
  const btns = Array.from(window.document.querySelectorAll('.nav button'));
  const rulesBtn = btns.find(b => /Routing rules/i.test(b.textContent));
  assert.ok(rulesBtn, 'expected a Routing rules nav button for admin');
  rulesBtn.click();
  assert.equal(window.S.view, 'rules');
  assert.match(window.document.getElementById('root').textContent, /Routing rules/);
});

test('renderRulesPage displays active/inactive rules with badges, patterns, and match counts', async () => {
  const rules = [
    { id: 1, rule_type: 'catch_all', pattern: '*', target_username: 'bob', target_public_id: 'U-BOB', match_mailboxes: 3, description: 'All unmatched', is_active: true },
    { id: 2, rule_type: 'domain', pattern: 'example.com', target_username: 'alice', target_public_id: 'U-ALICE', match_mailboxes: 5, description: 'Example domain', is_active: false },
    { id: 3, rule_type: 'prefix', pattern: 'otp-', target_username: 'charlie', target_public_id: 'U-CHARLIE', match_mailboxes: 2, description: 'OTP prefix', is_active: true },
    { id: 4, rule_type: 'regex', pattern: '^test-.*', target_username: 'dave', target_public_id: 'U-DAVE', match_mailboxes: 0, description: 'Test regex', is_active: true },
  ];
  const { window } = await boot({ rules });
  window.S.view = 'rules';
  window.render();
  const root = window.document.getElementById('root');
  assert.match(root.textContent, /CATCH_ALL/);
  assert.match(root.textContent, /DOMAIN/);
  assert.match(root.textContent, /PREFIX/);
  assert.match(root.textContent, /REGEX/);
  assert.match(root.textContent, /bob/);
  assert.match(root.textContent, /alice/);
  assert.match(root.textContent, /3 mailbox/);
  assert.match(root.textContent, /5 mailbox/);
});

test('newRuleModal opens with rule types and presets', async () => {
  const { window } = await boot();
  window.newRuleModal();
  const modal = window.document.querySelector('.modal');
  assert.ok(modal, 'expected modal dialog');
  const typeSelect = window.document.getElementById('rule-type');
  assert.ok(typeSelect, 'expected rule type select');
  assert.equal(typeSelect.value, 'catch_all');

  const patternInput = window.document.getElementById('rule-pattern');
  assert.equal(patternInput.value, '*');

  // Change preset using setRulePreset
  window.setRulePreset('*@domain.com');
  assert.equal(patternInput.value, '*@domain.com');

  // Change rule type and check updateRuleTypeUI
  typeSelect.value = 'domain';
  window.updateRuleTypeUI();
  const hint = window.document.getElementById('rule-hint');
  assert.match(hint.textContent, /tên miền/i);
});

test('ruleTestModal tests address against /api/admin/rules/test', async () => {
  const { window, calls } = await boot({
    routes: {
      '/api/admin/rules/test': {
        body: {
          address: 'hello@example.com',
          exact_override: false,
          owner: null,
          matches: [
            { id: 1, rule_type: 'domain', pattern: 'example.com', target_username: 'alice', target_public_id: 'U-ALICE', description: '' }
          ],
        },
      },
    },
  });
  window.ruleTestModal();
  const input = window.document.getElementById('test-email');
  assert.ok(input, 'expected test email input');
  input.value = 'hello@example.com';
  await window.testRules();
  const res = window.document.getElementById('test-result');
  assert.match(res.textContent, /alice/);
  assert.match(res.textContent, /domain/i);
  assert.ok(calls.some(c => c.path === '/api/admin/rules/test'));
});

test('mailbox table displays EXACT and RULES badges and user pills', async () => {
  const mailboxes = [
    mailbox({ id: 1, address: 'exact@example.com', exact_override: true, is_owner: true, users: [{ id: 2, username: 'alice', public_id: 'U-ALICE', source: 'manual' }] }),
    mailbox({ id: 2, address: 'rules@example.com', exact_override: false, is_owner: false, users: [{ id: 3, username: 'bob', public_id: 'U-BOB', source: 'rule' }] }),
  ];
  const { window } = await boot({ mailboxes });
  window.S.view = 'mailboxes';
  window.render();
  const root = window.document.getElementById('root');
  assert.match(root.textContent, /EXACT/);
  assert.match(root.textContent, /RULES/);
  assert.match(root.textContent, /• rule/);
});

test('unassigned catch-all mailbox alert appears and filter toggles', async () => {
  const mailboxes = [
    mailbox({ id: 1, address: 'orphan@example.com', exact_override: false, owner_user_id: null, users: [] }),
  ];
  const { window } = await boot({ mailboxes });
  window.S.view = 'mailboxes';
  window.render();
  const root = window.document.getElementById('root');
  assert.match(root.textContent, /Chưa gán \(Catch-All\)/i);

  // Toggle filter
  window.toggleUnassignedFilter();
  assert.equal(window.S.unassignedOnly, true);
  window.render();
  assert.match(window.document.getElementById('root').textContent, /Đang lọc/);
});

test('bulkReturnToRules sends request and clears selection', async () => {
  let bulkCalled = false;
  const mailboxes = [
    mailbox({ id: 1, address: 'exact1@example.com', exact_override: true, is_owner: true }),
    mailbox({ id: 2, address: 'exact2@example.com', exact_override: true, is_owner: true }),
  ];
  const { window } = await boot({
    mailboxes,
    routes: {
      '/api/mailboxes/bulk-return-to-rules': async (url, opts) => {
        bulkCalled = true;
        const body = JSON.parse(opts.body);
        assert.deepEqual(body.mailbox_ids, [1, 2]);
        return { body: { ok: true, notice: 'Reconciled 2 mailboxes' } };
      },
    },
  });
  window.S.view = 'mailboxes';
  window.S.checkedMailboxes = new Set([1, 2]);
  window.confirm = () => true;
  await window.bulkReturnToRules();
  assert.equal(bulkCalled, true);
  assert.equal(window.S.checkedMailboxes.size, 0);
});

test('branding displays APP_NAME in header and login', async () => {
  const { window } = await boot();
  const brandName = window.document.querySelector('.gmail-brand-name');
  assert.equal(brandName.textContent.trim(), 'OTP Inbox');
  assert.match(window.document.querySelector('.gmail-logo').getAttribute('title'), /OTP Inbox/);

  // Check login screen
  window.S.me = null;
  window.render();
  const authBrand = window.document.querySelector('.google-auth-brand-name');
  assert.match(authBrand.textContent, /OTP Inbox/);
});

test('desktop sidebar toggle collapses and expands sidebar', async () => {
  const { window } = await boot();
  window.innerWidth = 1200;
  assert.equal(window.S.sidebarCollapsed, false);
  const hamburger = window.document.querySelector('.hamburger-btn');
  assert.ok(hamburger, 'hamburger button must exist');

  // Toggle collapse
  window.toggleSidebarMenu();
  assert.equal(window.S.sidebarCollapsed, true);
  assert.ok(window.document.querySelector('.app').classList.contains('sidebar-collapsed'));
  assert.ok(window.document.getElementById('sidebar').classList.contains('collapsed'));

  // Toggle expand
  window.toggleSidebarMenu();
  assert.equal(window.S.sidebarCollapsed, false);
  assert.ok(!window.document.querySelector('.app').classList.contains('sidebar-collapsed'));
  assert.ok(!window.document.getElementById('sidebar').classList.contains('collapsed'));
});

test('mobile drawer toggle opens and closes drawer with backdrop', async () => {
  const { window } = await boot();
  window.innerWidth = 500;
  assert.equal(window.S.mobileSidebarOpen, false);

  // Toggle mobile open
  window.toggleSidebarMenu();
  assert.equal(window.S.mobileSidebarOpen, true);
  const sb = window.document.getElementById('sidebar');
  const backdrop = window.document.getElementById('sidebar-backdrop');
  assert.ok(sb.classList.contains('mobile-open'), 'sidebar has mobile-open class');
  assert.ok(backdrop.classList.contains('active'), 'backdrop has active class');

  // Close mobile drawer
  window.closeMobileSidebar();
  assert.equal(window.S.mobileSidebarOpen, false);
  assert.ok(!sb.classList.contains('mobile-open'), 'mobile-open class removed');
  assert.ok(!backdrop.classList.contains('active'), 'active class removed');
});

test('mobile reader shows back button and returns to inbox', async () => {
  const msg = message({ id: 99, subject: 'Verification Code', otp: '123456' });
  const { window } = await boot({
    routes: {
      '/api/messages': { body: { items: [msg], total: 1, page: 1, page_size: 30, engine: 'fts' } },
      '/api/messages/99': { body: msg },
      '/api/messages/99/read': { body: { ok: true } },
    },
  });
  await window.loadMessages();
  assert.equal(window.S.messages.length, 1);

  // Open message
  await window.openMessage(99);
  assert.equal(window.S.selectedMessageId, 99);

  // Find back button
  const backBtn = window.document.querySelector('.reader-back-btn');
  assert.ok(backBtn, 'reader-back-btn must exist');
  assert.match(backBtn.textContent, /Quay lại Hộp thư/);

  // Click back button
  backBtn.click();
  assert.equal(window.S.selectedMessageId, null);
  assert.ok(!window.document.querySelector('.reader').classList.contains('mobile-open'));
});

test('clicking star button toggles star state and does not display copied', async () => {
  const msg = message({ id: 101, subject: 'Welcome Email', otp: '654321' });
  const { window } = await boot({
    routes: {
      '/api/messages': { body: { items: [msg], total: 1, page: 1, page_size: 30, engine: 'fts' } },
    },
  });
  await window.loadMessages();
  assert.equal(window.S.starredMessages.has(101), false);
  const starBtn = window.document.querySelector('.gmail-star');
  assert.ok(starBtn, 'star button must exist');
  assert.ok(!starBtn.classList.contains('starred'));

  // Click star
  starBtn.click();
  assert.equal(window.S.starredMessages.has(101), true);
  assert.notEqual(starBtn.textContent.trim(), 'COPIED!', 'star must never show COPIED!');

  // Check updated DOM
  const updatedStarBtn = window.document.querySelector('.gmail-star');
  assert.ok(updatedStarBtn.classList.contains('starred'));
  assert.notEqual(updatedStarBtn.textContent.trim(), 'COPIED!');

  // Click star again to unstar
  updatedStarBtn.click();
  assert.equal(window.S.starredMessages.has(101), false);
  const unstarredBtn = window.document.querySelector('.gmail-star');
  assert.ok(!unstarredBtn.classList.contains('starred'));
});

test('sidebar navigation uses SVG vector icons instead of unicode emojis', async () => {
  const { window } = await boot();
  const navBtns = window.document.querySelectorAll('.nav button');
  assert.ok(navBtns.length > 0);
  navBtns.forEach(btn => {
    const svg = btn.querySelector('svg.app-icon');
    assert.ok(svg, `nav button ${btn.textContent.trim()} must contain an SVG app-icon`);
  });
});

test('header profile chip renders avatar with initial and background', async () => {
  const { window } = await boot();
  const avatar = window.document.querySelector('.gmail-profile-chip .avatar');
  assert.ok(avatar, 'avatar element must exist in header');
  assert.equal(avatar.textContent.trim(), 'R');
  assert.ok(avatar.getAttribute('style')?.includes('background:'), 'must have background style');
});

test('reader header renders sender avatar and avoids duplicate plain text card when HTML exists', async () => {
  const msg = message({
    id: 55,
    envelope_from: 'security@google.com',
    envelope_to: 'user@example.com',
    subject: 'Security Alert',
    has_html: true,
    body_text: 'Your security code is 123456',
  });
  const { window } = await boot({
    routes: {
      '/api/messages': { body: { items: [msg], total: 1, page: 1, page_size: 30, engine: 'fts' } },
      '/api/messages/55': { body: { ...msg, has_html: true, body_text: 'Your security code is 123456', otp: '123456' } },
      '/api/messages/55/read': { body: { ok: true } },
    },
  });
  await window.loadMessages();
  await window.openMessage(55);

  const senderAvatar = window.document.querySelector('.reader-sender-row .avatar');
  assert.ok(senderAvatar, 'reader sender avatar must exist');
  assert.equal(senderAvatar.textContent.trim(), 'S'); // Security

  const iframe = window.document.querySelector('.mail-html iframe');
  assert.ok(iframe, 'html iframe must exist');

  // Verify that the duplicate plain text card is not appended
  const plainTextCard = window.document.querySelector('.reader-body .plain-mail');
  assert.equal(plainTextCard, null, 'duplicate plain text card should not exist when HTML is rendered');
});


