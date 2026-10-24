/* ============================================================
   OTP Inbox — application script
   ------------------------------------------------------------
   Render model
   -----------
   The UI is re-rendered by replacing #root.innerHTML. That is simple and fast
   at this size, but it destroys two things that used to be lost on every
   interaction: the scroll offset of each scroll container, and keyboard focus
   inside inputs. `render()` therefore snapshots both, re-renders, then
   restores them. Every scroll container and every input that must survive a
   re-render carries an explicit data-scroll / data-focus-key attribute so the
   snapshot is keyed by identity rather than by DOM position.
   ============================================================ */

const S = {
  me: null,
  csrf: null,
  referral: null,
  registrationOpen: false,
  authMode: 'login',
  authConfig: { registration_open: false, app: 'OTP Inbox' },
  sidebarCollapsed: false,
  mobileSidebarOpen: false,
  mailboxes: [],
  users: [],
  rules: [],
  notifications: [],
  audit: [],
  domains: [],
  pendingRequestsCount: 0,
  myRequestsCount: 0,
  systemStats: null,
  mailboxRequests: [],
  requestStatusFilter: '',
  view: 'inbox',
  selectedMailboxId: 0,
  selectedMessageId: null,
  messages: [],
  total: 0,
  page: 1,
  pageSize: 30,
  q: '',
  scope: 'all',
  mailboxQ: '',
  mailboxLimit: 60,
  mailboxTableLimit: 150,
  unread: false,
  otpOnly: false,
  // Gmail-like: show remote images by default; localStorage may override.
  remote: (() => {
    try {
      const v = localStorage.getItem('otp_remote_images');
      if (v === '0') return false;
      if (v === '1') return true;
    } catch (_) { /* ignore */ }
    return true;
  })(),
  loading: false,
  engine: '',
  checkedMailboxes: new Set(),
  checkedMessages: new Set(),
  unassignedOnly: false,
  starredMessages: (() => {
    try {
      const raw = localStorage.getItem('otp_starred_msgs');
      if (raw) return new Set(JSON.parse(raw));
    } catch (_) {}
    return new Set();
  })(),
};

// Admin-only referral dashboard payload, loaded on demand.
let adminReferral = null;
let userAdminMailboxSelection = new Set();
let userAdminUserId = 0;

/* ------------------------------------------------------------------ utils */

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function escapeRegExp(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function fmt(s) {
  try {
    return new Date(s).toLocaleString();
  } catch {
    return s || '';
  }
}

function initials(name) {
  return String(name || '?').slice(0, 1).toUpperCase();
}

function avatarBg(name) {
  const colors = ['#1a73e8', '#d93025', '#188038', '#e37400', '#a142f4', '#12b5cb', '#e52592', '#00796b'];
  let hash = 0;
  const s = String(name || '');
  for (let i = 0; i < s.length; i++) hash = (hash * 31 + s.charCodeAt(i)) & 0xffffffff;
  return colors[Math.abs(hash) % colors.length];
}

function formatBytes(n) {
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0, x = Number(n || 0);
  while (x >= 1024 && i < units.length - 1) { x /= 1024; i++; }
  return `${x.toFixed(i ? 1 : 0)} ${units[i]}`;
}

/* ----------------------------------------------------------- SVG Icon System */

const ICONS = {
  inbox: '<path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H5v-3h3.56c.69 1.19 1.97 2 3.44 2s2.75-.81 3.44-2H19v3zm0-5h-4.99c0 1.1-.9 2-2.01 2s-2.01-.9-2.01-2H5V5h14v9z"/>',
  mailboxes: '<path d="M20 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z"/>',
  rules: '<path d="M14 6l-3.79 3.79 2.12 2.12L16 8.24V14h2V6h-8v2h4zm-4 12l3.79-3.79-2.12-2.12L8 15.76V10H6v8h8v-2z"/>',
  users: '<path d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z"/>',
  referrals: '<path d="M22 10V6c0-1.11-.9-2-2-2H4c-1.1 0-1.99.89-1.99 2v4c1.1 0 1.99.9 1.99 2s-.89 2-2 2v4c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2v-4c-1.1 0-2-.9-2-2s.9-2 2-2zm-2-1.54c-1.21.54-2 1.76-2 3.54s.79 3 2 3.54V18H4v-1.54c1.21-.54 2-1.76 2-3.54s-.79-3-2-3.54V6h16v2.46z"/>',
  notifications: '<path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.9 2 2 2zm6-6v-5c0-3.07-1.63-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.64 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2zm-2 1H8v-6c0-2.48 1.51-4.5 4-4.5s4 2.02 4 4.5v6z"/>',
  audit: '<path d="M19 3h-4.18C14.4 1.84 13.3 1 12 1c-1.3 0-2.4.84-2.82 2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-7 0c.55 0 1 .45 1 1s-.45 1-1 1-1-.45-1-1 .45-1 1-1zm2 14H7v-2h7v2zm3-4H7v-2h10v2zm0-4H7V7h10v2z"/>',
  account: '<path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/>',
  search: '<path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/>',
  star: '<path d="M22 9.24l-7.19-.62L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21 12 17.27 18.18 21l-1.63-7.03L22 9.24zM12 15.4l-3.76 2.27 1-4.28-3.32-2.88 4.38-.38L12 6.1l1.71 4.04 4.38.38-3.32 2.88 1 4.28L12 15.4z"/>',
  starFilled: '<path d="M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z"/>',
  key: '<path d="M12.65 10C11.83 7.67 9.61 6 7 6c-3.31 0-6 2.69-6 6s2.69 6 6 6c2.61 0 4.83-1.67 5.65-4H17v4h4v-4h2v-4H12.65zM7 14c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2z"/>',
  paperclip: '<path d="M16.5 6v11.5c0 2.21-1.79 4-4 4s-4-1.79-4-4V5a2.5 2.5 0 0 1 5 0v10.5c0 .83-.67 1.5-1.5 1.5s-1.5-.67-1.5-1.5V6H9v9.5a3 3 0 0 0 6 0V5c0-2.21-1.79-4-4-4S7 2.79 7 5v12.5c0 3.04 2.46 5.5 5.5 5.5s5.5-2.46 5.5-5.5V6h-1.5z"/>',
  tag: '<path d="M21.41 11.58l-9-9C12.05 2.22 11.55 2 11 2H4c-1.1 0-2 .9-2 2v7c0 .55.22 1.05.59 1.42l9 9c.36.36.86.58 1.41.58s1.05-.22 1.41-.59l7-7c.37-.36.59-.86.59-1.41s-.23-1.06-.59-1.42zM5.5 7C4.67 7 4 6.33 4 5.5S4.67 4 5.5 4 7 4.67 7 5.5 6.33 7 5.5 7z"/>',
  markRead: '<path d="M20 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z"/>',
  markUnread: '<path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm-1 2l-7 4.5L5 6h14zm-14 12V8.5l7 4.5 7-4.5V18H5z"/>',
  clear: '<path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/>',
  shield: '<path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm0 10.99h7c-.53 4.12-3.28 7.79-7 8.94V12H5V6.3l7-3.11v8.8z"/>',
  warning: '<path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z"/>',
  check: '<path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/>',
  chevronLeft: '<path d="M15.41 7.41L14 6l-6 6 6 6 1.41-1.41L10.83 12z"/>',
  chevronRight: '<path d="M10 6L8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z"/>',
  plus: '<path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/>',
  refresh: '<path d="M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/>',
  theme: '<path d="M12 3c-4.97 0-9 4.03-9 9s4.03 9 9 9 9-4.03 9-9c0-.46-.04-.92-.1-1.36-.98 1.37-2.58 2.26-4.4 2.26-2.98 0-5.4-2.42-5.4-5.4 0-1.81.89-3.42 2.26-4.4-.44-.06-.9-.1-1.36-.1z"/>',
  keyboard: '<path d="M20 5H4c-1.1 0-1.99.9-1.99 2L2 17c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm-9 3h2v2h-2V8zm0 3h2v2h-2v-2zM8 8h2v2H8V8zm0 3h2v2H8v-2zm-1 2H5v-2h2v2zm0-3H5V8h2v2zm8 7H9v-2h6v2zm1-4h-2v-2h2v2zm0-3h-2V8h2v2zm3 3h-2v-2h2v2zm0-3h-2V8h2v2z"/>',
  exit: '<path d="M10.09 15.59L11.5 17l5-5-5-5-1.41 1.41L12.67 11H3v2h9.67l-2.58 2.59zM19 3H5c-1.11 0-2 .9-2 2v4h2V5h14v14H5v-4H3v4c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2z"/>'
};

function icon(name, size = 18, cls = '') {
  const p = ICONS[name] || '';
  return `<svg class="app-icon ${cls}" viewBox="0 0 24 24" width="${size}" height="${size}" fill="currentColor" aria-hidden="true">${p}</svg>`;
}

function debounce(fn, ms) {
  let timer = null;
  const wrapped = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
  wrapped.cancel = () => clearTimeout(timer);
  wrapped.flush = (...args) => { clearTimeout(timer); fn(...args); };
  return wrapped;
}

/* Wrap every occurrence of a query term in <mark>, escaping each piece.
   Escaping happens per segment, so markup in the source value can never be
   injected through the highlight path. */
function highlight(value, query) {
  const raw = String(value ?? '');
  const terms = String(query || '').trim().toLowerCase().split(/\s+/)
    .filter(t => t.length >= 1).slice(0, 8);
  if (!terms.length) return esc(raw);
  const re = new RegExp(`(${terms.map(escapeRegExp).join('|')})`, 'ig');
  let out = '', last = 0, m;
  while ((m = re.exec(raw)) !== null) {
    out += esc(raw.slice(last, m.index)) + '<mark>' + esc(m[0]) + '</mark>';
    last = m.index + m[0].length;
    if (m[0].length === 0) re.lastIndex++;
  }
  return out + esc(raw.slice(last));
}

function toast(msg, ok = false, kind = '') {
  const host = document.getElementById('toast');
  if (!host) return;
  const cls = kind || (ok ? 'ok' : 'error');
  const ic = cls === 'ok' ? icon('check', 16) : (cls === 'error' ? icon('warning', 16) : icon('notifications', 16));
  const id = 't-' + Date.now();
  host.innerHTML = `<div class="toast-item ${cls}" id="${id}">
    <span class="toast-icon">${ic}</span>
    <div class="toast-msg">${esc(msg)}</div>
    <div class="toast-progress"></div>
  </div>`;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => {
    const el = document.getElementById(id);
    if (el) {
      el.classList.add('toast-fadeout');
      setTimeout(() => { if (host.firstChild === el) host.innerHTML = ''; }, 260);
    }
  }, 4200);
}

/* ------------------------------------------------------ scroll and focus */

/* Snapshot the scroll offset of every registered container. Keyed by the
   data-scroll value rather than by position, so filtering a list does not make
   the restore land on the wrong element. */
const SCROLL_SELECTORS = '[data-scroll]';

function captureScroll() {
  const out = new Map();
  document.querySelectorAll(SCROLL_SELECTORS).forEach(el => {
    out.set(el.dataset.scroll, el.scrollTop);
  });
  return out;
}

function restoreScroll(snapshot) {
  if (!snapshot) return;
  document.querySelectorAll(SCROLL_SELECTORS).forEach(el => {
    const saved = snapshot.get(el.dataset.scroll);
    if (saved != null) el.scrollTop = saved;
  });
}

function captureFocus() {
  const el = document.activeElement;
  if (!el || !el.tagName) return null;
  const key = el.dataset ? el.dataset.focusKey : '';
  if (!key) return null;
  const snap = { key };
  if (typeof el.selectionStart === 'number') {
    snap.start = el.selectionStart;
    snap.end = el.selectionEnd;
  }
  return snap;
}

function restoreFocus(snap) {
  if (!snap) return;
  const el = document.querySelector(
    `[data-focus-key="${CSS.escape(snap.key)}"], #${CSS.escape(snap.key)}`);
  if (!el) return;
  el.focus({ preventScroll: true });
  if (snap.start != null && typeof el.setSelectionRange === 'function') {
    try { el.setSelectionRange(snap.start, snap.end); } catch { /* not a text input */ }
  }
}

/* ------------------------------------------------------------------- http */

async function api(path, opts = {}) {
  const method = (opts.method || 'GET').toUpperCase();
  opts.headers = Object.assign({}, opts.headers || {});
  if (opts.body && !opts.headers['Content-Type']) opts.headers['Content-Type'] = 'application/json';
  if (S.csrf && !['GET', 'HEAD', 'OPTIONS'].includes(method)) opts.headers['X-CSRF-Token'] = S.csrf;
  const r = await fetch(path, opts);
  let b = null;
  try { b = await r.json(); } catch { /* empty or non-JSON body */ }
  if (!r.ok) {
    // Carry the status so callers can tell "session expired" from a real fault.
    const err = new Error(b?.detail || `HTTP ${r.status}`);
    err.status = r.status;
    throw err;
  }
  return b;
}

/* ------------------------------------------------------------------ modal */

function modal(title, body, customClass = '') {
  closeModal();
  const headHtml = title
    ? `<div class="modal-head"><span>${esc(title)}</span><button class="btn secondary small" onclick="closeModal()">Đóng</button></div>`
    : '';
  document.body.insertAdjacentHTML('beforeend',
    `<div class="modal" id="modal" role="dialog" aria-modal="true" aria-label="${esc(title || 'Hộp thoại')}" onclick="if(event.target===this)closeModal()">` +
    `<div class="modal-card ${customClass}">${headHtml}` +
    `<div style="${title ? 'margin-top:12px' : ''}">${body}</div></div></div>`);
}

function closeModal() {
  document.getElementById('modal')?.remove();
}

let cmdPaletteIndex = 0;
let cmdPaletteItems = [];

function openCommandPalette() {
  closeModal();
  cmdPaletteIndex = 0;

  const isAdmin = S.me?.role === 'admin';
  const getCommands = (q = '') => {
    const term = q.trim().toLowerCase();
    const actions = [
      { id: 'inbox', label: 'Hộp thư (Inbox)', sub: 'Xem toàn bộ email nhận được', tag: 'Điều hướng', icon: icon('inbox', 18), shortcut: 'G I', run: () => { S.view = 'inbox'; render(); } },
      { id: 'mailboxes', label: isAdmin ? 'Mailbox & phân quyền' : 'Mailbox của tôi', sub: 'Quản lý danh sách hộp thư và gán quyền', tag: 'Điều hướng', icon: icon('mailboxes', 18), shortcut: 'G M', run: () => { S.view = 'mailboxes'; render(); } },
      { id: 'requests', label: 'Yêu cầu tạo Mailbox', sub: isAdmin ? 'Duyệt đơn xin cấp email của thành viên' : 'Xem tiến độ đơn xin cấp email', tag: 'Điều hướng', icon: icon('rules', 18), run: () => goView('requests') },
      ...(isAdmin ? [
        { id: 'domains', label: 'Quản lý Tên miền', sub: 'Cấu hình Public, VIP, Yêu cầu duyệt và Quota', tag: 'Admin', icon: icon('rules', 18), shortcut: 'G D', run: () => goView('domains') },
        { id: 'rules', label: 'Routing Rules', sub: 'Cấu hình Catch-all, Domain, Prefix, Regex', tag: 'Admin', icon: icon('rules', 18), shortcut: 'G R', run: () => { S.view = 'rules'; render(); } },
        { id: 'users', label: 'Quản lý Người dùng', sub: 'Danh sách user, API key, phân quyền', tag: 'Admin', icon: icon('users', 18), shortcut: 'G U', run: () => { S.view = 'users'; render(); } },
        { id: 'system', label: 'Hệ thống & Lưu trữ', sub: 'Giám sát Koyeb Disk, RAM, VACUUM, Sao lưu DB', tag: 'Admin', icon: icon('refresh', 18), shortcut: 'G S', run: () => goView('system') },
      ] : []),
      { id: 'referrals', label: 'Mã giới thiệu', sub: 'Xem và tạo mã mời thành viên', tag: 'Điều hướng', icon: icon('referrals', 18), run: () => { S.view = 'referrals'; render(); } },
      { id: 'notifications', label: 'Thông báo', sub: 'Xem các thông báo mới từ hệ thống', tag: 'Điều hướng', icon: icon('notifications', 18), run: () => { S.view = 'notifications'; render(); } },
      ...(isAdmin ? [
        { id: 'audit', label: 'Audit Log', sub: 'Nhật ký bảo mật và thao tác hệ thống', tag: 'Admin', icon: icon('audit', 18), run: () => { S.view = 'audit'; render(); } },
      ] : []),
      { id: 'account', label: 'Cài đặt tài khoản', sub: 'Đổi mật khẩu, API token cá nhân', tag: 'Cài đặt', icon: icon('account', 18), run: () => { S.view = 'account'; render(); } },
      
      // Quick Actions
      { id: 'new-mailbox', label: 'Tạo 1 Mailbox mới', sub: 'Tạo địa chỉ cụ thể', tag: 'Hành động', icon: icon('plus', 18), run: () => newMailboxModal() },
      { id: 'bulk-mailboxes', label: 'Tạo nhiều Mailbox tự sinh', sub: 'Tự sinh hàng loạt địa chỉ với tiền tố', tag: 'Hành động', icon: icon('plus', 18), run: () => bulkCreateOwnModal() },
      ...(isAdmin ? [
        { id: 'import-mailboxes', label: 'Import danh sách Mailbox', sub: 'Nhập danh sách từ file hoặc văn bản', tag: 'Admin', icon: icon('inbox', 18), run: () => bulkImportModal() },
        { id: 'new-rule', label: 'Tạo Routing Rule mới', sub: 'Domain, Catch-All, Prefix hoặc Regex', tag: 'Admin', icon: icon('rules', 18), run: () => newRuleModal() },
        { id: 'test-rule', label: 'Test Routing Rule', sub: 'Kiểm tra rule khớp với email nào', tag: 'Admin', icon: icon('rules', 18), run: () => ruleTestModal() },
        { id: 'new-user', label: 'Tạo User mới', sub: 'Thêm tài khoản người dùng', tag: 'Admin', icon: icon('users', 18), run: () => newUserModal() },
      ] : []),
      ...((isAdmin || S.referral?.permission_active) ? [
        { id: 'new-ref', label: 'Tạo mã giới thiệu mới', sub: 'Sinh mã giới thiệu người dùng mới', tag: 'Hành động', icon: icon('referrals', 18), run: () => newReferralModal() },
      ] : []),
      { id: 'refresh', label: 'Làm mới dữ liệu (Refresh)', sub: 'Tải lại toàn bộ mail và trạng thái', tag: 'Hệ thống', icon: icon('refresh', 18), shortcut: 'R', run: async () => { await refreshData(); render(); toast('Dữ liệu đã được làm mới', true); } },
      { id: 'theme', label: 'Đổi giao diện Sáng / Tối', sub: 'Chuyển đổi Dark / Light mode', tag: 'Giao diện', icon: icon('theme', 18), run: () => toggleTheme() },
      { id: 'shortcuts', label: 'Bảng phím tắt', sub: 'Xem danh sách phím tắt tiện ích', tag: 'Trợ giúp', icon: icon('keyboard', 18), shortcut: '?', run: () => shortcutsModal() },
      { id: 'logout', label: 'Đăng xuất', sub: 'Thoát phiên làm việc', tag: 'Tài khoản', icon: icon('exit', 18), run: () => logout() },
    ];

    let mailboxMatches = [];
    if (term) {
      mailboxMatches = S.mailboxes
        .filter(m => m.address.toLowerCase().includes(term))
        .slice(0, 6)
        .map(m => ({
          id: `mb-${m.id}`,
          label: m.address,
          sub: `${m.message_count} thư · ${m.unread_count} chưa đọc`,
          tag: 'Mailbox',
          icon: icon('mailboxes', 18),
          run: () => openMailbox(m.id)
        }));
    }

    const filteredActions = actions.filter(it =>
      !term ||
      it.label.toLowerCase().includes(term) ||
      it.sub.toLowerCase().includes(term) ||
      it.tag.toLowerCase().includes(term)
    );

    return [...mailboxMatches, ...filteredActions];
  };

  const renderCmdItems = () => {
    const listEl = document.getElementById('cmd-list');
    if (!listEl) return;
    if (!cmdPaletteItems.length) {
      listEl.innerHTML = '<div class="empty" style="padding:28px 0">Không tìm thấy lệnh hoặc mailbox nào</div>';
      return;
    }
    listEl.innerHTML = cmdPaletteItems.map((it, idx) => `
      <div class="cmd-item ${idx === cmdPaletteIndex ? 'active' : ''}" data-idx="${idx}" onclick="window.__execCmd(${idx})">
        <div class="cmd-item-label">
          <span style="font-size:16px">${it.icon}</span>
          <div>
            <div style="font-weight:600;font-size:13.5px">${esc(it.label)}</div>
            <div class="tiny muted">${esc(it.sub)}</div>
          </div>
        </div>
        <div style="display:flex;align-items:center;gap:6px">
          ${it.shortcut ? `<span class="kbd">${esc(it.shortcut)}</span>` : ''}
          <span class="cmd-badge">${esc(it.tag)}</span>
        </div>
      </div>
    `).join('');

    const activeEl = listEl.querySelector('.cmd-item.active');
    if (activeEl) activeEl.scrollIntoView({ block: 'nearest' });
  };

  window.__execCmd = (idx) => {
    const item = cmdPaletteItems[idx];
    if (item && item.run) {
      closeModal();
      item.run();
    }
  };

  cmdPaletteItems = getCommands('');

  modal('', `
    <div class="cmd-palette">
      <div class="cmd-input-wrap">
        <span style="display:inline-flex;color:var(--text-muted)">${icon('search', 18)}</span>
        <input id="cmd-input" class="cmd-input" placeholder="Tìm kiếm lệnh, mailbox hoặc hành động... (Esc để đóng)" autocomplete="off">
        <span class="kbd">Esc</span>
      </div>
      <div id="cmd-list" class="cmd-list"></div>
    </div>
  `, 'cmd-modal');

  renderCmdItems();

  const inputEl = document.getElementById('cmd-input');
  if (inputEl) {
    inputEl.focus();
    inputEl.oninput = (e) => {
      cmdPaletteIndex = 0;
      cmdPaletteItems = getCommands(e.target.value);
      renderCmdItems();
    };
    inputEl.onkeydown = (e) => {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (cmdPaletteItems.length > 0) {
          cmdPaletteIndex = (cmdPaletteIndex + 1) % cmdPaletteItems.length;
          renderCmdItems();
        }
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (cmdPaletteItems.length > 0) {
          cmdPaletteIndex = (cmdPaletteIndex - 1 + cmdPaletteItems.length) % cmdPaletteItems.length;
          renderCmdItems();
        }
      } else if (e.key === 'Enter') {
        e.preventDefault();
        window.__execCmd(cmdPaletteIndex);
      }
    };
  }
}

function shortcutsModal() {
  modal('Bảng phím tắt', `
    <div class="shortcuts-grid">
      <div class="shortcut-item"><span>Mở Command Palette</span><span class="kbd">Ctrl + K</span></div>
      <div class="shortcut-item"><span>Xem bảng phím tắt</span><span class="kbd">?</span></div>
      <div class="shortcut-item"><span>Email tiếp theo</span><span class="kbd">J</span></div>
      <div class="shortcut-item"><span>Email trước đó</span><span class="kbd">K</span></div>
      <div class="shortcut-item"><span>Copy mã OTP nhanh</span><span class="kbd">C</span></div>
      <div class="shortcut-item"><span>Làm mới hộp thư</span><span class="kbd">R</span></div>
      <div class="shortcut-item"><span>Đóng cửa sổ / Modal</span><span class="kbd">Esc</span></div>
    </div>
  `);
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    closeModal();
    return;
  }

  const tag = (e.target.tagName || '').toLowerCase();
  const isInput = tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable;

  // Ctrl+K or Cmd+K
  if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
    e.preventDefault();
    openCommandPalette();
    return;
  }

  if (isInput) return;

  // '?' key
  if (e.key === '?' || (e.shiftKey && e.key === '/')) {
    e.preventDefault();
    shortcutsModal();
    return;
  }

  // 'r' key
  if ((e.key === 'r' || e.key === 'R') && !e.ctrlKey && !e.metaKey) {
    e.preventDefault();
    refreshData().then(() => { render(); toast('Dữ liệu đã được làm mới', true); });
    return;
  }

  // Inbox navigation
  if (S.view === 'inbox' && S.messages && S.messages.length > 0) {
    if (e.key === 'j' || e.key === 'J') {
      e.preventDefault();
      const curIdx = S.messages.findIndex(m => m.id === S.selectedMessageId);
      const nextIdx = (curIdx < S.messages.length - 1) ? curIdx + 1 : 0;
      openMessage(S.messages[nextIdx].id);
      return;
    }
    if (e.key === 'k' || e.key === 'K') {
      e.preventDefault();
      const curIdx = S.messages.findIndex(m => m.id === S.selectedMessageId);
      const prevIdx = (curIdx > 0) ? curIdx - 1 : S.messages.length - 1;
      openMessage(S.messages[prevIdx].id);
      return;
    }
    if (e.key === 'c' || e.key === 'C') {
      const cur = S.messages.find(m => m.id === S.selectedMessageId);
      const target = (cur && cur.otp_code) ? cur : S.messages.find(m => m.otp_code);
      if (target && target.otp_code) {
        e.preventDefault();
        copyText(target.otp_code, `Đã copy OTP: ${target.otp_code}`);
        return;
      }
    }
  }
});

/* ========================================================================
   Theme
   ===================================================================== */

function currentTheme() {
  return document.documentElement.classList.contains('dark') ? 'dark' : 'light';
}

function applyTheme() {
  let stored = null;
  try { stored = localStorage.getItem('otp-theme'); } catch { /* private mode */ }
  if (stored !== 'dark' && stored !== 'light') {
    // No explicit choice yet: follow the OS so a dark-mode user never sees a
    // white flash on first load.
    stored = (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)
      ? 'dark' : 'light';
  }
  document.documentElement.classList.toggle('dark', stored === 'dark');
  const button = document.getElementById('theme-toggle');
  if (button) {
    button.textContent = stored === 'dark' ? '☀' : '☾';
    button.setAttribute('aria-pressed', stored === 'dark' ? 'true' : 'false');
  }
}

function toggleTheme() {
  const next = currentTheme() === 'dark' ? 'light' : 'dark';
  try { localStorage.setItem('otp-theme', next); } catch { /* private mode */ }
  applyTheme();
  // The reader embeds the message body in a sandboxed iframe whose colours
  // are chosen server-side, so a toggle has to reload it.
  if (S.selectedMessageId) fillReader(S.selectedMessageId);
}

if (window.matchMedia) {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => {
    let stored = null;
    try { stored = localStorage.getItem('otp-theme'); } catch { /* ignore */ }
    if (stored !== 'dark' && stored !== 'light') {
      document.documentElement.classList.toggle('dark', e.matches);
    }
  });
}

/* ========================================================================
   Auth
   ===================================================================== */

let autoPollTimer = null;
let lastKnownMailboxCount = null;
let lastKnownMessageCount = null;

function startAutoPolling() {
  if (autoPollTimer) clearInterval(autoPollTimer);
  // In test harnesses (like jsdom), don't start background intervals that prevent process exit
  if (typeof navigator !== 'undefined' && navigator.userAgent && /jsdom/i.test(navigator.userAgent)) return;
  autoPollTimer = setInterval(async () => {
    if (!S.me || document.visibilityState === 'hidden') return;
    try {
      const d = await api('/api/dashboard');
      const newMailboxes = d.mailboxes || [];
      const totalMsgs = newMailboxes.reduce((a, x) => a + x.message_count, 0);

      const hadPrevious = lastKnownMessageCount !== null;
      const countIncreased = hadPrevious && totalMsgs > lastKnownMessageCount;
      const mailboxIncreased = hadPrevious && newMailboxes.length > (lastKnownMailboxCount || 0);

      lastKnownMailboxCount = newMailboxes.length;
      lastKnownMessageCount = totalMsgs;

      S.mailboxes = newMailboxes;
      S.users = d.users || [];
      S.notifications = d.notifications || [];
      S.rules = d.rules || [];
      S.audit = d.audit || [];
      if (d.referral) S.referral = d.referral;

      if (countIncreased) {
        toast('📬 Có email mới nhận!', true);
        if (S.view === 'inbox') {
          await loadMessages(true);
        } else {
          render();
        }
      } else if (mailboxIncreased) {
        toast('✨ Mailbox mới đã được tạo!', true);
        render();
      }
    } catch {
      // Background poll silently catches errors
    }
  }, 5000);
  if (autoPollTimer && typeof autoPollTimer.unref === 'function') {
    autoPollTimer.unref();
  }
}

function stopAutoPolling() {
  if (autoPollTimer) {
    clearInterval(autoPollTimer);
    autoPollTimer = null;
  }
}

function resetState() {
  stopAutoPolling();
  lastKnownMailboxCount = null;
  lastKnownMessageCount = null;
  S.me = null;
  S.csrf = null;
  S.referral = null;
  S.messages = [];
  S.total = 0;
  S.mailboxes = [];
  S.users = [];
  S.rules = [];
  S.notifications = [];
  S.audit = [];
  S.selectedMailboxId = 0;
  S.selectedMessageId = null;
  S.q = '';
  S.mailboxQ = '';
  S.page = 1;
  S.checkedMailboxes.clear();
  S.checkedMessages.clear();
  adminReferral = null;
}

async function init() {
  applyTheme();
  try {
    const m = await api('/api/me');
    S.me = m.user;
    S.csrf = m.csrf;
    S.referral = m.referral || null;
    S.registrationOpen = !!m.registration_open;
    S.authConfig.registration_open = !!m.registration_open;
    await refreshData();
    render();
    startAutoPolling();
  } catch (e) {
    // Only a 401 means "not signed in". Anything else is a real failure and is
    // surfaced, instead of silently masquerading as a logged-out user.
    if (e.status && e.status !== 401) toast(`Không tải được dữ liệu: ${e.message}`);
    await loadAuthConfig();
    renderAuth('login');
  }
}

async function loadAuthConfig() {
  try {
    S.authConfig = await api('/api/auth/config');
    S.registrationOpen = !!S.authConfig.registration_open;
    if (S.authConfig.app) document.title = S.authConfig.app;
  } catch {
    S.registrationOpen = false;
  }
}

function renderAuth(mode) {
  S.authMode = mode === 'register' ? 'register' : 'login';
  const root = document.getElementById('root');
  const canRegister = S.registrationOpen;
  const name = appBrand();
  const brand = `<div class="google-auth-header">
    <div class="google-auth-logo">
      <svg viewBox="0 0 48 48" width="40" height="40" aria-hidden="true">
        <path fill="#4285F4" d="M4 12v24c0 2.2 1.8 4 4 4h4V20L4 12z"/>
        <path fill="#34A853" d="M36 40h4c2.2 0 4-1.8 4-4V12l-8 8v20z"/>
        <path fill="#EA4335" d="M4 12l20 15 20-15-4-3-16 12L8 9 4 12z"/>
        <path fill="#FBBC05" d="M12 20v20h24V20L24 29 12 20z"/>
      </svg>
      <div class="google-auth-brand-name">${esc(name)} <span class="gmail-brand-tag">OTP</span></div>
    </div>
    <div class="google-auth-title">${S.authMode === 'register' ? 'Tạo tài khoản' : 'Đăng nhập'}</div>
    <div class="google-auth-sub">${S.authMode === 'register' ? `Đăng ký tài khoản ${esc(name)}` : `Sử dụng tài khoản ${esc(name)} của bạn`}</div>
  </div>`;

  if (S.authMode === 'register') {
    root.innerHTML = `<div class="login-wrap"><div class="card login">
      ${brand}
      <div id="auth-msg"></div>
      <div class="field"><label>Mã giới thiệu</label>
        <input id="reg-code" data-focus-key="reg-code" autocomplete="off" autocapitalize="characters"
               spellcheck="false" placeholder="REF-XXXX-XXXX"></div>
      <div class="field"><label>Tài khoản</label>
        <input id="reg-user" data-focus-key="reg-user" autocomplete="username" placeholder="3-40 ký tự"></div>
      <div class="field"><label>Mật khẩu</label>
        <input id="reg-pass" type="password" data-focus-key="reg-pass" autocomplete="new-password"
               placeholder="Tối thiểu 10 ký tự"
               onkeydown="if(event.key==='Enter')register()"></div>
      <div class="hint">Tài khoản mới không tự có quyền xem mailbox nào. Admin hoặc routing rule sẽ cấp quyền sau.</div>
      <button class="btn primary" style="width:100%;margin-top:16px;border-radius:20px;height:42px" onclick="register()">Đăng ký</button>
      <div class="auth-switch">Đã có tài khoản?
        <button onclick="showLogin()">Đăng nhập</button></div>
    </div></div>`;
    return;
  }

  root.innerHTML = `<div class="login-wrap"><div class="card login">
    ${brand}
    <div id="auth-msg"></div>
    <div class="field"><label>Tài khoản</label>
      <input id="login-user" data-focus-key="login-user" autocomplete="username"></div>
    <div class="field"><label>Mật khẩu</label>
      <input id="login-pass" type="password" data-focus-key="login-pass" autocomplete="current-password"
             onkeydown="if(event.key==='Enter')login()"></div>
    <button class="btn primary" style="width:100%;margin-top:16px;border-radius:20px;height:42px" onclick="login()">Đăng nhập</button>
    ${canRegister ? `<div class="auth-switch">Chưa có tài khoản?
      <button onclick="showRegister()">Đăng ký bằng mã giới thiệu</button></div>` : ''}
  </div></div>`;
}

function renderLogin() {
  renderAuth('login');
}

function authError(message) {
  const box = document.getElementById('auth-msg');
  if (box) box.innerHTML = `<div class="notice error">${esc(message)}</div>`;
}

function showRegister() {
  renderAuth('register');
  document.getElementById('reg-code')?.focus();
  loadAuthConfig().then(() => {
    if (!S.registrationOpen) {
      toast('Đăng ký tự do đang đóng. Bạn cần mã giới thiệu hợp lệ từ quản trị viên.');
    }
  });
}

function showLogin() {
  renderAuth('login');
  document.getElementById('login-user')?.focus();
}

async function login() {
  try {
    const d = await api('/api/login', {
      method: 'POST',
      body: JSON.stringify({
        username: document.getElementById('login-user').value,
        password: document.getElementById('login-pass').value,
      }),
    });
    S.me = d.user;
    S.csrf = d.csrf;
    await loadAuthConfig();
    await refreshData();
    render();
    startAutoPolling();
  } catch (e) {
    authError(e.message);
  }
}

async function register() {
  try {
    const d = await api('/api/register', {
      method: 'POST',
      body: JSON.stringify({
        username: document.getElementById('reg-user').value,
        password: document.getElementById('reg-pass').value,
        referral_code: document.getElementById('reg-code').value,
      }),
    });
    S.me = d.user;
    S.csrf = d.csrf;
    S.referral = d.referral || null;
    await refreshData();
    render();
    startAutoPolling();
    toast('Đăng ký thành công. Chưa có mailbox nào được gán — hãy liên hệ admin.', true, 'info');
  } catch (e) {
    authError(e.message);
  }
}

/* Clears local state and shows the login screen even if the network call
   fails, so the user is never stuck on a session they asked to end. */
async function logout() {
  try {
    await api('/api/logout', { method: 'POST' });
  } catch {
    /* fall through: the local session is cleared regardless */
  }
  resetState();
  try { sessionStorage.clear(); } catch { /* ignore */ }
  await loadAuthConfig();
  renderAuth('login');
  toast('Đã đăng xuất.', true, 'info');
}

/* ========================================================================
   Data
   ===================================================================== */

async function refreshData() {
  const d = await api('/api/dashboard');
  S.mailboxes = d.mailboxes || [];
  S.users = d.users || [];
  S.notifications = d.notifications || [];
  S.rules = d.rules || [];
  S.audit = d.audit || [];
  S.referral = d.referral || S.referral;
  S.domains = d.domains || [];
  S.pendingRequestsCount = d.pending_requests_count || 0;
  S.myRequestsCount = d.my_requests_count || 0;
  if (S.registrationOpen !== undefined) S.registrationOpen = true;
  if (S.selectedMailboxId !== 0 && !S.mailboxes.some(x => x.id === S.selectedMailboxId)) {
    S.selectedMailboxId = 0;
  }
  await loadMessages(false);
}

/* Sequence guard: without it, two overlapping searches can resolve out of
   order and paint the older result set over the newer one. */
let messageRequestSeq = 0;

async function loadMessages(shouldRender = true) {
  if (S.view !== 'inbox') {
    if (shouldRender) render();
    return;
  }
  const seq = ++messageRequestSeq;
  S.loading = true;
  const p = new URLSearchParams({
    mailbox_id: S.selectedMailboxId,
    page: S.page,
    page_size: S.pageSize,
    q: S.q,
    unread: S.unread ? '1' : '0',
    otp_only: S.otpOnly ? '1' : '0',
    scope: S.scope,
  });
  try {
    const d = await api('/api/messages?' + p.toString());
    if (seq !== messageRequestSeq) return;  // superseded by a newer request
    S.messages = d.items || [];
    S.total = d.total || 0;
    S.engine = d.engine || '';
  } catch (e) {
    if (seq !== messageRequestSeq) return;
    toast(e.message);
    S.messages = [];
    S.total = 0;
  } finally {
    if (seq === messageRequestSeq) {
      S.loading = false;
      if (shouldRender) render();
    }
  }
}

/* Live search: fires shortly after typing stops, and immediately on Enter so
   the user never waits on a keystroke they have already finished. */
const runMessageSearch = debounce(() => { S.page = 1; loadMessages(); }, 250);

function onMessageSearch(value) {
  S.q = value;
  runMessageSearch();
}

function submitMessageSearch() {
  runMessageSearch.cancel();
  S.page = 1;
  loadMessages();
}

function clearMessageSearch() {
  S.q = '';
  S.page = 1;
  loadMessages();
}

function onMailboxFilter(value) {
  S.mailboxQ = value;
  S.mailboxLimit = 60;
  S.mailboxTableLimit = 150;
  render();
}

/* ========================================================================
   Shell
   ===================================================================== */

function render() {
  if (!S.me) return renderLogin();
  const scroll = captureScroll();
  const focus = captureFocus();
  const management = S.view !== 'inbox';
  document.getElementById('root').innerHTML =
    `<div class="app ${management ? 'management' : ''} ${S.sidebarCollapsed ? 'sidebar-collapsed' : ''}">` +
      renderHeader() +
      `<div class="gmail-body">` +
        `<div class="sidebar-backdrop ${S.mobileSidebarOpen ? 'active' : ''}" id="sidebar-backdrop" onclick="closeMobileSidebar()"></div>` +
        `<aside class="sidebar ${S.sidebarCollapsed ? 'collapsed' : ''} ${S.mobileSidebarOpen ? 'mobile-open' : ''}" id="sidebar">${renderSidebar()}</aside>` +
        `<section class="list-pane ${S.selectedMessageId ? 'mobile-hidden' : ''}">` +
          `${S.view === 'inbox' ? renderInbox() : renderPageView()}</section>` +
        `<section class="reader ${S.selectedMessageId ? 'mobile-open' : ''}">` +
          `${S.view === 'inbox' ? renderReader() : renderNoReader()}</section>` +
      `</div>` +
    `</div>`;
  const dl = document.getElementById('user-list');
  if (dl) {
    dl.innerHTML = S.users
      .filter(u => u.role === 'user' && u.is_active)
      .map(u => `<option value="${esc(u.public_id)}">${esc(u.username)}</option>`)
      .join('');
  }
  restoreScroll(scroll);
  restoreFocus(focus);
}

function renderCurrent() {
  render();
}

function appBrand() {
  return (S.authConfig && S.authConfig.app) || 'OTP Inbox';
}

function renderHeader() {
  const notif = S.notifications.filter(x => !x.read_at).length;
  const isInbox = S.view === 'inbox';
  const brand = appBrand();

  return `<header class="gmail-header">
    <div class="gmail-header-left">
      <button class="gmail-icon-btn hamburger-btn" type="button" onclick="toggleSidebarMenu()" title="Trình đơn chính" aria-label="Trình đơn chính">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor"><path d="M3 18h18v-2H3v2zm0-5h18v-2H3v2zm0-7v2h18V6H3z"/></svg>
      </button>
      <div class="gmail-logo" onclick="goAllMail()" title="${esc(brand)} — OTP Inbox" role="button" tabindex="0">
        <svg class="gmail-logo-icon" viewBox="0 0 48 48" width="28" height="28" aria-hidden="true">
          <path fill="#4285F4" d="M4 12v24c0 2.2 1.8 4 4 4h4V20L4 12z"/>
          <path fill="#34A853" d="M36 40h4c2.2 0 4-1.8 4-4V12l-8 8v20z"/>
          <path fill="#EA4335" d="M4 12l20 15 20-15-4-3-16 12L8 9 4 12z"/>
          <path fill="#FBBC05" d="M12 20v20h24V20L24 29 12 20z"/>
        </svg>
        <span class="gmail-brand-name">${esc(brand)}</span>
        <span class="gmail-brand-tag">OTP</span>
      </div>
    </div>
    <div class="gmail-header-center">
      <div class="gmail-search-bar ${S.q ? 'has-query' : ''}">
        <span class="gmail-search-icon" aria-hidden="true">${icon('search', 18)}</span>
        ${isInbox
          ? `<input data-focus-key="msg-q" value="${esc(S.q)}" autocomplete="off"
                    placeholder="Tìm kiếm trong thư…"
                    aria-label="Tìm kiếm trong thư"
                    oninput="onMessageSearch(this.value)"
                    onkeydown="if(event.key==='Enter')submitMessageSearch()">`
          : `<input placeholder="Tìm kiếm trong ${esc(getViewTitle())}…"
                    aria-label="Tìm kiếm"
                    onclick="openCommandPalette()" readonly style="cursor:pointer">`
        }
        ${isInbox && S.q ? `<button class="search-clear gmail-search-clear" type="button" onclick="clearMessageSearch()" title="Xoá tìm kiếm" aria-label="Xoá tìm kiếm">${icon('clear', 14)}</button>` : ''}
        <button class="gmail-search-opts-btn" type="button" onclick="openCommandPalette()" title="Tìm kiếm nâng cao & lệnh (Ctrl+K)" aria-label="Bộ lọc & lệnh">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M3 17v2h6v-2H3zM3 5v2h10V5H3zm10 16v-2h8v-2h-8v-2h-2v6h2zM7 9v2H3v2h4v2h2V9H7zm14 4v-2H11v2h10zm-6-4h2V7h4V5h-4V3h-2v6z"/></svg>
        </button>
      </div>
    </div>
    <div class="gmail-header-right">
      <button class="gmail-icon-btn" type="button" onclick="refreshData().then(render)" title="Làm mới (↻)" aria-label="Làm mới">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/></svg>
      </button>
      <button class="gmail-icon-btn notif-btn ${notif ? 'has-unread' : ''}" type="button" onclick="goView('notifications')" title="Thông báo" aria-label="Thông báo">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.9 2 2 2zm6-6v-5c0-3.07-1.63-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.64 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2zm-2 1H8v-6c0-2.48 1.51-4.5 4-4.5s4 2.02 4 4.5v6z"/></svg>
        ${notif ? `<span class="gmail-notif-badge">${notif > 9 ? '9+' : notif}</span>` : ''}
      </button>
      <div class="gmail-profile-chip" onclick="goView('account')" title="Tài khoản: ${esc(S.me.username)} (${esc(S.me.public_id)})" role="button" tabindex="0">
        <div class="avatar" style="background:${avatarBg(S.me.username)}">${initials(S.me.username)}</div>
      </div>
    </div>
  </header>`;
}

function getViewTitle() {
  if (S.view === 'mailboxes') return 'Mailbox';
  if (S.view === 'rules') return 'Routing rules';
  if (S.view === 'users') return 'Người dùng';
  if (S.view === 'domains') return 'Quản lý Tên miền';
  if (S.view === 'requests') return 'Yêu cầu tạo Mailbox';
  if (S.view === 'system') return 'Hệ thống & Lưu trữ';
  if (S.view === 'referrals') return 'Mã giới thiệu';
  if (S.view === 'notifications') return 'Thông báo';
  if (S.view === 'audit') return 'Audit log';
  if (S.view === 'account') return 'Tài khoản';
  return 'Thư';
}

function toggleSidebarMenu() {
  if (window.innerWidth <= 900) {
    S.mobileSidebarOpen = !S.mobileSidebarOpen;
    const sb = document.getElementById('sidebar');
    const bd = document.getElementById('sidebar-backdrop');
    if (sb) sb.classList.toggle('mobile-open', S.mobileSidebarOpen);
    if (bd) bd.classList.toggle('active', S.mobileSidebarOpen);
  } else {
    S.sidebarCollapsed = !S.sidebarCollapsed;
    const app = document.querySelector('.app');
    const sb = document.getElementById('sidebar');
    if (app) app.classList.toggle('sidebar-collapsed', S.sidebarCollapsed);
    if (sb) sb.classList.toggle('collapsed', S.sidebarCollapsed);
  }
}

function closeMobileSidebar() {
  if (S.mobileSidebarOpen) {
    S.mobileSidebarOpen = false;
    const sb = document.getElementById('sidebar');
    const bd = document.getElementById('sidebar-backdrop');
    if (sb) sb.classList.remove('mobile-open');
    if (bd) bd.classList.remove('active');
  }
}

function navBtn(view, label, count = '', iconName = '') {
  const ic = typeof iconName === 'string' && ICONS[iconName] ? icon(iconName, 18) : (iconName || '');
  return `<button class="${S.view === view ? 'active' : ''}" onclick="goView('${view}')">` +
    `<span class="nav-label-wrap">${ic ? `<span class="nav-icon">${ic}</span>` : ''}<span>${esc(label)}</span></span>` +
    `${count ? `<span class="count">${esc(count)}</span>` : ''}</button>`;
}

function goView(view) {
  S.view = view;
  S.selectedMessageId = null;
  S.page = 1;
  closeMobileSidebar();
  if (view === 'referrals' && S.me?.role === 'admin' && !adminReferral) {
    loadAdminReferral();
    return;
  }
  if (view === 'system' && S.me?.role === 'admin') {
    loadSystemStats();
    return;
  }
  if (view === 'domains' && S.me?.role === 'admin') {
    loadDomains();
    return;
  }
  if (view === 'requests') {
    loadMailboxRequests();
    return;
  }
  loadMessages(false).then(render);
}

function goAllMail() {
  S.view = 'inbox';
  S.selectedMailboxId = 0;
  S.selectedMessageId = null;
  S.page = 1;
  closeMobileSidebar();
  loadMessages();
}

/* -------------------------------------------------------------- sidebar */

function filteredMailboxes() {
  const q = S.mailboxQ.trim().toLowerCase();
  if (!q) return S.mailboxes;
  const terms = q.split(/\s+/).filter(Boolean);
  return S.mailboxes.filter(m => {
    const hay = `${m.address} ${m.owner_username || ''}`.toLowerCase();
    return terms.every(t => hay.includes(t));
  });
}

function renderSidebar() {
  const unread = S.mailboxes.reduce((a, x) => a + x.unread_count, 0);
  const notif = S.notifications.filter(x => !x.read_at).length;
  const list = filteredMailboxes();
  const shown = list.slice(0, S.mailboxLimit);
  const remaining = list.length - shown.length;
  const isAdmin = S.me.role === 'admin';
  const reqBadge = isAdmin ? (S.pendingRequestsCount ? String(S.pendingRequestsCount) : '') : (S.myRequestsCount ? String(S.myRequestsCount) : '');

  const brand = appBrand();
  return `
  <div class="mobile-drawer-header mobile-only">
    <div class="mobile-drawer-brand">
      <svg class="gmail-logo-icon" viewBox="0 0 48 48" width="24" height="24" aria-hidden="true">
        <path fill="#4285F4" d="M4 12v24c0 2.2 1.8 4 4 4h4V20L4 12z"/>
        <path fill="#34A853" d="M36 40h4c2.2 0 4-1.8 4-4V12l-8 8v20z"/>
        <path fill="#EA4335" d="M4 12l20 15 20-15-4-3-16 12L8 9 4 12z"/>
        <path fill="#FBBC05" d="M12 20v20h24V20L24 29 12 20z"/>
      </svg>
      <span class="gmail-brand-name">${esc(brand)}</span>
      <span class="gmail-brand-tag">OTP</span>
    </div>
    <button class="gmail-icon-btn mobile-drawer-close-btn" type="button" onclick="closeMobileSidebar()" title="Đóng menu" aria-label="Đóng menu">✕</button>
  </div>
  <div class="gmail-compose-wrap">
    <button class="gmail-compose-btn btn primary" type="button" onclick="newMailboxModal()" title="Tạo địa chỉ Mailbox mới">
      <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg>
      <span>Tạo Mailbox</span>
    </button>
  </div>
  <nav class="nav">
    <div class="nav-section-label">WORKSPACE</div>
    ${navBtn('inbox', 'Hộp thư', unread ? String(unread) : '', 'inbox')}
    ${navBtn('mailboxes', isAdmin ? 'Mailbox & phân quyền' : 'Mailbox của tôi', '', 'mailboxes')}
    ${navBtn('requests', 'Yêu cầu tạo email', reqBadge, 'rules')}
    ${navBtn('referrals', 'Mã giới thiệu', S.referral?.code ? '1' : '', 'referrals')}
    ${navBtn('notifications', 'Thông báo', notif ? String(notif) : '', 'notifications')}
    ${navBtn('account', 'Tài khoản', '', 'account')}
    ${isAdmin ? `
      <div class="nav-section-label">QUẢN TRỊ ADMIN</div>
      ${navBtn('domains', 'Tên miền & Truy cập', S.domains ? String(S.domains.length) : '', 'rules')}
      ${navBtn('rules', 'Routing rules', S.rules ? String(S.rules.length) : '', 'rules')}
      ${navBtn('users', 'Người dùng', S.users ? String(S.users.length) : '', 'users')}
      ${navBtn('system', 'Hệ thống & Lưu trữ', '', 'refresh')}
      ${navBtn('audit', 'Audit log', '', 'audit')}
    ` : ''}
  </nav>
  <div class="sidebar-section">
    <div class="section-head">
      <span class="section-title">Nhãn & Mailbox</span>
      <span class="tiny muted">${list.length}${list.length !== S.mailboxes.length ? ` / ${S.mailboxes.length}` : ''}</span>
    </div>
    <div class="search-row" style="margin:2px 4px 6px;flex-wrap:nowrap">
      <input data-scroll-search="sidebar-q" data-focus-key="sidebar-q" value="${esc(S.mailboxQ)}"
             placeholder="Tìm mailbox…" aria-label="Tìm mailbox"
             oninput="onMailboxFilter(this.value)"
             onkeydown="if(event.key==='Enter'){this.blur()}">
      ${S.mailboxQ ? `<button class="search-clear" onclick="clearMailboxFilter()" title="Xoá tìm kiếm" aria-label="Xoá tìm kiếm">${icon('clear', 14)}</button>` : ''}
    </div>
    <div class="mailboxes" data-scroll="mailboxes" role="list">
      ${shown.map(m => `<div class="mailbox-row ${S.selectedMailboxId === m.id && S.view === 'inbox' ? 'active' : ''}" role="listitem">
        <input type="checkbox" ${S.checkedMailboxes.has(m.id) ? 'checked' : ''}
               aria-label="Chọn ${esc(m.address)}"
               onclick="setSelectedMailbox(${m.id},this.checked);event.stopPropagation()">
        <button class="address" title="${esc(m.address)}" onclick="openMailbox(${m.id})">
          <span class="mailbox-icon">${icon('tag', 15)}</span>
          <span class="mailbox-addr-text">${highlight(m.address, S.mailboxQ)}</span>
        </button>
        ${m.exact_override ? '<span class="status-dot" title="Exact override"></span>' : ''}
        ${m.unread_count ? `<span class="unread">${m.unread_count > 99 ? '99+' : m.unread_count}</span>` : ''}
      </div>`).join('') || `<div class="empty">${S.mailboxQ ? 'Không có mailbox khớp tìm kiếm.' : 'Chưa có mailbox.'}</div>`}
      ${remaining > 0 ? `<button class="btn secondary small list-more" onclick="showMoreMailboxes()">Tải thêm ${Math.min(remaining, 120)} mailbox</button>` : ''}
    </div>
  </div>
  <div class="sidebar-bottom">
    <div class="stack">
      <button class="btn secondary small" onclick="goView('account')">Tài khoản</button>
      <button class="btn small" onclick="logout()" title="Đăng xuất">Thoát</button>
    </div>
  </div>`;
}

function openMailbox(id) {
  S.view = 'inbox';
  S.selectedMailboxId = id;
  S.selectedMessageId = null;
  S.page = 1;
  closeMobileSidebar();
  loadMessages();
}

/* ---------------------------------------------------------------- inbox */

function renderInbox() {
  const m = S.mailboxes.find(x => x.id === S.selectedMailboxId);
  const pages = Math.max(1, Math.ceil(S.total / S.pageSize));
  const selected = S.checkedMailboxes.size;
  const allSelectedOwned = selected > 0 &&
    [...S.checkedMailboxes].every(id => S.mailboxes.find(x => x.id === id)?.is_owner);
  const isAdmin = S.me.role === 'admin';
  const scopeLabel = S.scope === 'headers' ? 'Tiêu đề' : 'Toàn bộ';

  return `<div class="toolbar">
    <div class="gmail-action-bar">
      <div class="gmail-action-left">
        <label class="gmail-select-check" title="Chọn tất cả thư trang này">
          <input type="checkbox" ${S.messages.length && S.checkedMessages.size === S.messages.length ? 'checked' : ''} onchange="toggleAllMessages(this.checked)">
        </label>
        <button class="gmail-tool-btn" type="button" onclick="refreshData().then(render)" title="Làm mới danh sách">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/></svg>
        </button>
        ${S.checkedMessages.size ? `
          <button class="gmail-tool-btn" type="button" onclick="bulkMessageState('read')" title="Đánh dấu đã đọc">${icon('markRead', 16)} <span>Đã đọc</span></button>
          <button class="gmail-tool-btn" type="button" onclick="bulkMessageState('unread')" title="Đánh dấu chưa đọc">${icon('markUnread', 16)} <span>Chưa đọc</span></button>
          <button class="gmail-tool-btn" type="button" onclick="clearMessageSelection()" title="Bỏ chọn thư">${icon('clear', 16)} <span>Bỏ chọn</span></button>
        ` : ''}
        ${(isAdmin || allSelectedOwned) ? `
          <button class="gmail-tool-btn" type="button" onclick="bulkAccessModal('share')" title="Chia sẻ mailbox">Chia sẻ</button>
          <button class="gmail-tool-btn" type="button" onclick="bulkAccessModal('transfer')" title="Chuyển quyền">Chuyển quyền</button>
        ` : ''}
        ${selected && isAdmin ? `
          <button class="gmail-tool-btn" type="button" onclick="bulkAssignModal()">Gán User</button>
          <button class="gmail-tool-btn" type="button" onclick="bulkReturnToRules()">Đưa về Rules</button>
          <button class="gmail-tool-btn danger-tool" type="button" onclick="bulkRevokeModal()">Thu hồi</button>
        ` : ''}
      </div>
      <div class="gmail-action-right">
        <span class="gmail-pagination-text">${S.total ? `${(S.page - 1) * S.pageSize + 1}–${Math.min(S.page * S.pageSize, S.total)} trong ${S.total}` : '0 thư'}</span>
        <button class="gmail-tool-btn" ${S.page <= 1 ? 'disabled' : ''} onclick="goPage(-1)" title="Trang trước">${icon('chevronLeft', 16)}</button>
        <button class="gmail-tool-btn" ${S.page >= pages ? 'disabled' : ''} onclick="goPage(1)" title="Trang sau">${icon('chevronRight', 16)}</button>
      </div>
    </div>
    <div class="gmail-chips-row filter-row">
      <div class="toolbar-mailbox-name">
        <b>${S.selectedMailboxId === 0 ? 'Tất cả thư' : esc(m?.address || 'Mailbox')}</b>
        ${m ? `<span class="tiny muted" style="margin-left:6px">${m.exact_override ? 'Exact override' : 'Rules'} · ${m.message_count} thư</span>` : ''}
      </div>
      <div class="gmail-chips-group">
        <label class="gmail-chip check ${S.unread ? 'active' : ''}">
          <input type="checkbox" ${S.unread ? 'checked' : ''} onchange="S.unread=this.checked;S.page=1;loadMessages()"> Chưa đọc
        </label>
        <label class="gmail-chip check ${S.otpOnly ? 'active' : ''}">
          <input type="checkbox" ${S.otpOnly ? 'checked' : ''} onchange="S.otpOnly=this.checked;S.page=1;loadMessages()"> Có OTP
        </label>
        <label class="gmail-chip check ${S.scope === 'headers' ? 'active' : ''}" title="Giới hạn tìm kiếm vào tiêu đề / người gửi / OTP">
          <input type="checkbox" ${S.scope === 'headers' ? 'checked' : ''} onchange="S.scope=this.checked?'headers':'all';S.page=1;loadMessages()"> Chỉ tiêu đề
        </label>
        ${S.q ? `<span class="badge">${esc(scopeLabel)}${S.engine ? ` · ${S.engine}` : ''}</span>` : ''}
      </div>
    </div>
  </div>
  <div class="list-scroll" data-scroll="list" role="list">
    ${S.messages.map(renderMessageRow).join('') || `<div class="empty">${S.loading ? 'Đang tải…' : (S.q ? 'Không tìm thấy thư nào khớp.' : 'Chưa có thư nào trong hộp thư.')}</div>`}
  </div>
  <div class="pagination mobile-only">
    <button class="btn secondary small" ${S.page <= 1 ? 'disabled' : ''} onclick="goPage(-1)">←</button>
    <span class="tiny muted">Trang ${S.page}/${pages}</span>
    <button class="btn secondary small" ${S.page >= pages ? 'disabled' : ''} onclick="goPage(1)">→</button>
  </div>`;
}

function goPage(delta) {
  S.page = Math.max(1, S.page + delta);
  loadMessages();
}

function formatSenderName(from) {
  if (!from) return '(unknown)';
  const m = String(from).match(/^"?([^"<]+)"?\s*<[^>]+>$/);
  if (m && m[1].trim()) return m[1].trim();
  const atIdx = from.indexOf('@');
  if (atIdx > 0 && !from.includes(' ')) return from.slice(0, atIdx);
  return from;
}

function fmtGmailDate(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return fmt(iso);
    const now = new Date();
    if (d.toDateString() === now.toDateString()) {
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    }
    if (d.getFullYear() === now.getFullYear()) {
      return `${d.getDate()} thg ${d.getMonth() + 1}`;
    }
    return `${d.getDate()}/${d.getMonth() + 1}/${d.getFullYear()}`;
  } catch {
    return fmt(iso);
  }
}

function toggleStarMessage(id, event) {
  if (event) event.stopPropagation();
  if (S.starredMessages.has(id)) {
    S.starredMessages.delete(id);
  } else {
    S.starredMessages.add(id);
  }
  try {
    localStorage.setItem('otp_starred_msgs', JSON.stringify([...S.starredMessages]));
  } catch (_) {}
  render();
}

function copyOtpPill(el, code, event) {
  if (event) event.stopPropagation();
  copyText(code, `Đã sao chép OTP: ${code}`);
  if (el) {
    el.classList.add('copied');
    const origHtml = el.innerHTML;
    el.innerHTML = `<span class="otp-icon">${icon('check', 14)}</span><span class="otp-text">Đã chép</span>`;
    setTimeout(() => {
      el.innerHTML = origHtml;
      el.classList.remove('copied');
    }, 1500);
  }
}

function renderMessageRow(msg) {
  const isStarred = S.starredMessages.has(msg.id);
  return `<div class="message-row ${S.selectedMessageId === msg.id ? 'active' : ''} ${msg.is_read ? 'is-read' : 'unread-row'}"
               role="listitem" tabindex="0" onclick="openMessage(${msg.id})"
               onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openMessage(${msg.id})}">
    <div class="message-grid">
      <div class="msg-col-check">
        <input type="checkbox" ${S.checkedMessages.has(msg.id) ? 'checked' : ''} aria-label="Chọn thư"
               onclick="event.stopPropagation();toggleMessageCheck(${msg.id},this.checked)">
        <button class="gmail-star ${isStarred ? 'starred' : ''}" type="button"
                onclick="toggleStarMessage(${msg.id}, event)"
                title="${isStarred ? 'Bỏ đánh dấu sao' : 'Đánh dấu sao'}"
                aria-label="${isStarred ? 'Bỏ đánh dấu sao' : 'Đánh dấu sao'}">
          ${icon(isStarred ? 'starFilled' : 'star', 18)}
        </button>
      </div>
      <div class="msg-main">
        <div class="from ${msg.is_read ? '' : 'bold'}">${highlight(formatSenderName(msg.envelope_from), S.q)}</div>
        <div class="msg-body-preview">
          <span class="subject ${msg.is_read ? '' : 'bold'}">${highlight(msg.subject || '(không có tiêu đề)', S.q)}</span>
          <span class="preview-sep"> — </span>
          <span class="preview">${highlight(msg.preview || '', S.q)}</span>
        </div>
      </div>
      <div class="msg-col-meta">
        ${msg.otp ? `<span class="otp-pill" data-otp="${esc(msg.otp)}" title="Sao chép mã OTP"
              onclick="copyOtpPill(this,'${esc(msg.otp)}',event)"><span class="otp-icon">${icon('key', 14)}</span><span class="otp-text">${esc(msg.otp)}</span></span>` : ''}
        ${msg.has_attachments ? `<span class="paperclip" title="Có tệp đính kèm">${icon('paperclip', 16)}</span>` : ''}
        <span class="msg-meta ${msg.is_read ? '' : 'bold'}">${fmtGmailDate(msg.received_at)}</span>
      </div>
    </div>
  </div>`;
}

async function toggleReadOne(id, currentRead) {
  const p = currentRead ? 'unread' : 'read';
  try {
    await api(`/api/messages/${id}/${p}`, { method: 'POST' });
    const m = S.messages.find(x => x.id === id);
    if (m) m.is_read = !currentRead;
    render();
  } catch (e) {
    toast(e.message);
  }
}

function toggleMessageCheck(id, checked) {
  if (checked) S.checkedMessages.add(id); else S.checkedMessages.delete(id);
  render();
}

function toggleAllMessages(checked) {
  S.messages.forEach(x => checked ? S.checkedMessages.add(x.id) : S.checkedMessages.delete(x.id));
  render();
}

function clearMessageSelection() {
  S.checkedMessages.clear();
  render();
}

function clearMailboxSelection() {
  S.checkedMailboxes.clear();
  render();
}

function setSelectedMailbox(id, checked) {
  if (checked) S.checkedMailboxes.add(Number(id));
  else S.checkedMailboxes.delete(Number(id));
  render();
}

function toggleAllMailboxes(checked) {
  S.mailboxes.forEach(x => checked ? S.checkedMailboxes.add(x.id) : S.checkedMailboxes.delete(x.id));
  render();
}

function selectedMailboxIds() {
  return [...S.checkedMailboxes];
}

function selectedMessageIds() {
  return [...S.checkedMessages];
}

async function bulkMessageState(mode) {
  const ids = selectedMessageIds();
  if (!ids.length) return;
  try {
    const d = await api(`/api/messages/bulk-${mode}`, { method: 'POST', body: JSON.stringify({ message_ids: ids }) });
    toast(`Đã cập nhật ${d.count || 0} thư.`, true);
    S.checkedMessages.clear();
    await loadMessages();
  } catch (e) {
    toast(e.message);
  }
}

/* --------------------------------------------------------------- reader */

async function openMessage(id) {
  S.selectedMessageId = id;
  render();
  try {
    await api(`/api/messages/${id}/read`, { method: 'POST' });
    const m = S.messages.find(x => x.id === id);
    if (m) m.is_read = true;
    await fillReader(id);
  } catch (e) {
    toast(e.message);
  }
}

function renderReader() {
  if (!S.selectedMessageId) {
    return `<div class="empty" style="margin:auto">Chọn một email để xem nội dung</div>`;
  }
  const m = S.messages.find(x => x.id === S.selectedMessageId);
  if (!m) return `<div class="empty" style="margin:auto">Email không còn khả dụng.</div>`;
  const senderDisplayName = formatSenderName(m.envelope_from);
  return `<div class="reader-header">
      <div class="mobile-reader-bar mobile-only">
        <button class="btn secondary small reader-back-btn" type="button" onclick="closeReader()" title="Quay lại danh sách thư">
          ← Quay lại Hộp thư
        </button>
      </div>
      <div class="reader-subject">${esc(m.subject || '(no subject)')}</div>
      <div class="reader-sender-row">
        <div class="avatar reader-avatar" style="background:${avatarBg(m.envelope_from)}">${initials(senderDisplayName)}</div>
        <div class="reader-sender-meta">
          <div class="reader-sender-title">
            <span class="reader-sender-name">${esc(senderDisplayName)}</span>
            <span class="reader-sender-email">&lt;${esc(m.envelope_from)}&gt;</span>
          </div>
          <div class="reader-recipient-line">
            <span>tới ${esc(m.envelope_to)}</span>
            <span>·</span>
            <span>${fmt(m.received_at)}</span>
          </div>
        </div>
      </div>
      <div class="reader-actions">
        <button class="btn secondary small mobile-only" onclick="closeReader()">← Hộp thư</button>
        <button class="btn secondary small" onclick="toggleReadState()">${m.is_read ? 'Đánh dấu chưa đọc' : 'Đánh dấu đã đọc'}</button>
        <button class="btn secondary small" onclick="rawModal()">Raw .eml</button>
        <button class="btn secondary small" onclick="textModal()">Text</button>
        <button class="btn secondary small" onclick="remoteToggle()">${S.remote ? 'Chặn ảnh ngoài' : 'Tải ảnh ngoài'}</button>
        <button class="btn ghost small" onclick="closeReader()" title="Đóng" aria-label="Đóng">×</button>
      </div>
    </div>
    <div class="reader-body" data-scroll="reader" id="reader-body"><div class="empty">Đang tải...</div></div>`;
}

function renderNoReader() {
  return `<div class="empty" style="margin:auto">${S.view === 'notifications' ? 'Thông báo' : 'Mở mục này để quản trị hệ thống.'}</div>`;
}

/* The reader body is written directly rather than through render(), so a
   background refresh or theme toggle cannot yank the pane mid-read. */
async function fillReader(id) {
  const el = document.getElementById('reader-body');
  if (!el) return;
  try {
    const b = await api(`/api/messages/${id}`);
    // A slower response for a message the user already navigated away from
    // must not overwrite the pane.
    if (S.selectedMessageId !== id) return;
    const theme = currentTheme();
    let html = `<div class="meta-row"><span class="kbd">Message #${b.id}</span>${b.message_id ? `<span class="kbd">${esc(b.message_id)}</span>` : ''}</div>`;
    if (b.otp) {
      html += `<div class="otp-card gmail-otp-hero" id="reader-otp-card">
        <div class="gmail-otp-header">
          <div class="gmail-otp-icon">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none"><path d="M12 2L4 5v6.09c0 5.05 3.41 9.76 8 10.91 4.59-1.15 8-5.86 8-10.91V5l-8-3z" fill="#0b57d0"/><path d="M10 15.5l-3.5-3.5 1.41-1.41L10 12.67l6.09-6.09L17.5 8 10 15.5z" fill="#fff"/></svg>
          </div>
          <div style="flex:1;min-width:0">
            <div class="gmail-otp-title">Mã xác minh bảo mật (OTP)</div>
            <div class="gmail-otp-subtitle">Mã dùng cho xác thực đăng nhập hoặc kích hoạt tài khoản</div>
          </div>
          <button class="btn ghost small" onclick="toggleOtpCollapse()" id="otp-collapse-btn" title="Thu gọn / Hiện đầy đủ">Thu gọn</button>
        </div>
        <div class="gmail-otp-code-wrap" id="reader-otp-code-wrap">
          <div class="gmail-otp-code otp-code">${esc(b.otp)}</div>
          <button class="gmail-otp-copy-btn btn success small" data-otp="${esc(b.otp)}" onclick="copyText(this.dataset.otp);this.textContent='✓ Đã sao chép';setTimeout(()=>this.textContent='Copy OTP',2000)">Copy OTP</button>
        </div>
        <div class="gmail-otp-footer" id="reader-otp-footer">
          <span>${icon('shield', 14)} Parser ưu tiên mã gần các từ khóa xác thực. Không chia sẻ mã với bất kỳ ai để bảo vệ tài khoản.</span>
        </div>
      </div>`;
    }
    if (b.has_html) {
      html += `<div class="card mail-card-wrap" id="mail-card-wrap" style="padding:0;margin-bottom:12px">
        <div class="mail-html-toolbar">
          <div class="mail-html-toolbar-title">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M20 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 14H4V8l8 5 8-5v10zm-8-7L4 6h16l-8 5z"/></svg>
            <span>Nội dung email</span>
          </div>
          <div class="mail-html-tools">
            <a class="btn secondary small" href="/api/messages/${id}/html?remote=${S.remote ? '1' : '0'}&amp;theme=${theme}" target="_blank" rel="noopener">Mở tab mới ↗</a>
            <button class="btn secondary small" onclick="toggleMailExpand()" id="mail-expand-btn">Phóng to</button>
          </div>
        </div>
        <div class="mail-html" id="mail-html-container">
          <iframe id="mail-iframe" title="Nội dung email"
            sandbox="allow-popups allow-popups-to-escape-sandbox allow-same-origin allow-scripts"
            src="/api/messages/${id}/html?remote=${S.remote ? '1' : '0'}&amp;theme=${theme}"
            onload="setupMailIframe(this)"></iframe>
        </div>
      </div>`;
    } else {
      html += `<div class="card"><div style="font-weight:800;margin-bottom:8px">Nội dung</div>
        <div class="plain-mail">${esc(b.body_text || '(empty)')}</div></div>`;
    }
    if (b.attachments?.length) {
      html += `<div class="card"><div style="font-weight:800;margin-bottom:6px">Tệp đính kèm (${b.attachments.length})</div>` +
        b.attachments.map(a => `<div class="attachment">
          <div class="attachment-name"><b>${esc(a.filename || '(unnamed)')}</b>
            <div class="tiny muted">${esc(a.content_type)} · ${formatBytes(a.size_bytes)}</div></div>
          <a class="btn secondary small" href="/api/attachments/${a.id}" target="_blank" rel="noopener">Mở</a>
        </div>`).join('') + `</div>`;
    }
    el.innerHTML = html;
  } catch (e) {
    if (S.selectedMessageId === id) {
      el.innerHTML = `<div class="notice error">${esc(e.message)}</div>`;
    }
  }
}

function closeReader() {
  S.selectedMessageId = null;
  render();
}

async function toggleReadState() {
  const m = S.messages.find(x => x.id === S.selectedMessageId);
  const p = m?.is_read ? 'unread' : 'read';
  try {
    await api(`/api/messages/${S.selectedMessageId}/${p}`, { method: 'POST' });
    if (m) m.is_read = !m.is_read;
    render();
    fillReader(S.selectedMessageId);
  } catch (e) {
    toast(e.message);
  }
}

function remoteToggle() {
  S.remote = !S.remote;
  try { localStorage.setItem('otp_remote_images', S.remote ? '1' : '0'); } catch (_) { /* ignore */ }
  render();
  fillReader(S.selectedMessageId);
}

function setupMailIframe(iframe) {
  if (!iframe) return;
  const updateHeight = () => {
    try {
      const doc = iframe.contentDocument || iframe.contentWindow?.document;
      if (doc) {
        const body = doc.body;
        const html = doc.documentElement;
        const h = Math.max(
          body ? body.scrollHeight : 0,
          body ? body.offsetHeight : 0,
          html ? html.clientHeight : 0,
          html ? html.scrollHeight : 0,
          html ? html.offsetHeight : 0
        );
        if (h > 40) {
          iframe.style.height = (h + 30) + 'px';
        }
      }
    } catch (_) {}
  };

  updateHeight();
  try {
    const doc = iframe.contentDocument || iframe.contentWindow?.document;
    if (doc && doc.body && window.ResizeObserver) {
      const ro = new ResizeObserver(updateHeight);
      ro.observe(doc.body);
    }
  } catch (_) {}
  setTimeout(updateHeight, 200);
  setTimeout(updateHeight, 800);
  setTimeout(updateHeight, 2000);
}

function toggleOtpCollapse() {
  const card = document.getElementById('reader-otp-card');
  const btn = document.getElementById('otp-collapse-btn');
  if (!card) return;
  const isCollapsed = card.classList.toggle('collapsed');
  if (btn) btn.textContent = isCollapsed ? 'Hiện OTP' : 'Thu gọn';
}

function toggleMailExpand() {
  const wrap = document.getElementById('mail-card-wrap');
  const btn = document.getElementById('mail-expand-btn');
  if (!wrap) return;
  const isExpanded = wrap.classList.toggle('expanded-view');
  if (btn) btn.textContent = isExpanded ? 'Thu nhỏ' : 'Phóng to';
}

if (typeof window !== 'undefined') {
  window.addEventListener('message', (ev) => {
    if (ev?.data?.type === 'otp_mail_resize' && ev.data.height) {
      const iframe = document.getElementById('mail-iframe');
      if (iframe) {
        iframe.style.height = Math.max(ev.data.height, 200) + 'px';
      }
    }
  });
}

async function copyText(v, successMsg = 'Đã copy vào clipboard!') {
  try {
    await navigator.clipboard.writeText(v);
    toast(successMsg, true);
  } catch {
    const el = document.createElement('textarea');
    el.value = v;
    document.body.appendChild(el);
    el.select();
    try {
      document.execCommand('copy');
      toast(successMsg, true);
    } catch {
      toast('Không thể copy: ' + v);
    }
    document.body.removeChild(el);
  }
}

function copyOtpElement(el, code) {
  copyOtpPill(el, code);
}

function rawModal() {
  api(`/api/messages/${S.selectedMessageId}/raw-text`)
    .then(x => modal('Raw .eml', `<pre class="raw">${esc(x.raw)}</pre>`))
    .catch(e => toast(e.message));
}

function textModal() {
  api(`/api/messages/${S.selectedMessageId}`)
    .then(x => modal('Plain text', `<div class="plain-mail">${esc(x.body_text || '')}</div>`))
    .catch(e => toast(e.message));
}

/* ========================================================================
   Management pages
   ===================================================================== */

function renderPageView() {
  if (S.view === 'mailboxes') return renderMailboxesPage();
  if (S.view === 'rules') return renderRulesPage();
  if (S.view === 'users') return renderUsersPage();
  if (S.view === 'domains') return renderDomainsPage();
  if (S.view === 'requests') return renderRequestsPage();
  if (S.view === 'system') return renderSystemPage();
  if (S.view === 'referrals') return renderReferralsPage();
  if (S.view === 'notifications') return renderNotificationsPage();
  if (S.view === 'audit') return renderAuditPage();
  if (S.view === 'account') return renderAccountPage();
  return renderNoReader();
}

function pageShell(title, subtitle, actions, body) {
  return `<div class="page" data-scroll="page">
    <div class="page-head">
      <div><div class="page-title">${esc(title)}</div><div class="muted">${esc(subtitle)}</div></div>
      <div class="stack wrap">${actions || ''}</div>
    </div>
    ${body}
  </div>`;
}

/* ------------------------------------------------------------ mailboxes */

function mailboxRows() {
  let list = S.mailboxes;
  if (S.unassignedOnly) {
    list = list.filter(m => !m.owner_user_id && (!m.users || m.users.length === 0));
  }
  const terms = S.mailboxQ.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return list;
  return list.filter(m => {
    const hay = [
      m.address, m.owner_username || '', m.override_reason || '',
      m.exact_override ? 'exact' : 'rules',
      ...(m.users || []).map(u => u.username),
    ].join(' ').toLowerCase();
    return terms.every(t => hay.includes(t));
  });
}

function toggleUnassignedFilter() {
  S.unassignedOnly = !S.unassignedOnly;
  render();
}

function renderMailboxesPage() {
  const isAdmin = S.me.role === 'admin';
  const total = S.mailboxes.reduce((a, x) => a + x.message_count, 0);
  const unread = S.mailboxes.reduce((a, x) => a + x.unread_count, 0);
  const exact = S.mailboxes.filter(x => x.exact_override).length;
  const unassigned = S.mailboxes.filter(m => !m.owner_user_id && (!m.users || m.users.length === 0));
  const selected = selectedMailboxIds();
  const canBulk = selected.length > 0 &&
    (isAdmin || selected.every(id => S.mailboxes.find(x => x.id === id)?.is_owner));
  const matches = mailboxRows();
  const shown = matches.slice(0, S.mailboxTableLimit);
  const remaining = matches.length - shown.length;

  return pageShell(
    isAdmin ? 'Mailbox & phân quyền' : 'Mailbox của tôi',
    'Tạo địa chỉ exact, import danh sách, owner/access và routing. Exact luôn ưu tiên prefix/regex.',
    `${isAdmin ? '<button class="btn secondary" onclick="bulkImportModal()">Import list</button>' : ''}
     <button class="btn primary" onclick="newMailboxModal()">＋ Tạo 1 địa chỉ</button>
     <button class="btn secondary" onclick="bulkCreateOwnModal()">＋ Tạo nhiều</button>`,
    `<div class="stat-grid">
      <div class="stat-card" style="cursor:pointer" onclick="if(S.unassignedOnly){S.unassignedOnly=false;render();}"><div class="stat-num">${S.mailboxes.length}</div><div class="stat-label">Mailbox có quyền xem</div></div>
      <div class="stat-card"><div class="stat-num">${total}</div><div class="stat-label">Tổng email</div></div>
      <div class="stat-card"><div class="stat-num">${unread}</div><div class="stat-label">Chưa đọc</div></div>
      <div class="stat-card"><div class="stat-num">${exact}</div><div class="stat-label">Exact override</div></div>
      ${isAdmin && unassigned.length ? `<div class="stat-card" style="border-color:var(--warn-line);background:var(--warn-soft);cursor:pointer" onclick="toggleUnassignedFilter()" title="Click để lọc mailbox chưa gán"><div class="stat-num" style="color:var(--warn)">${unassigned.length}</div><div class="stat-label">${icon('warning', 14)} Chưa gán (Catch-All)</div></div>` : ''}
    </div>
    ${isAdmin && unassigned.length && S.unassignedOnly ? `<div style="height:10px"></div><div class="notice" style="display:flex;justify-content:space-between;align-items:center"><span>Đang lọc <b>${unassigned.length}</b> mailbox mồ côi (tạo bởi catch-all nhưng chưa có rule khớp). Bạn có thể tick chọn để gán user.</span><button class="btn secondary small" onclick="toggleUnassignedFilter()">Hiện tất cả</button></div>` : ''}
    <div style="height:12px"></div>
    <div class="card soft">
      <div class="between">
        <div><b>Bulk actions</b><div class="hint">Tick mailbox để Shared / Transfer / Gán / Thu hồi. User chỉ thao tác mailbox mình là owner.</div></div>
        <span class="badge">${selected.length} đã chọn</span>
      </div>
      <div class="filter-row">
        <button class="btn secondary small" onclick="toggleAllMailboxes(true)">Chọn tất cả</button>
        <button class="btn ghost small" onclick="clearMailboxSelection()">Bỏ chọn</button>
        ${canBulk ? `<button class="btn success small" onclick="bulkAccessModal(this.dataset.mode)" data-mode="share">Shared</button>
          <button class="btn danger small" onclick="bulkAccessModal(this.dataset.mode)" data-mode="transfer">Transfer</button>` : ''}
        ${isAdmin && selected.length ? `<button class="btn secondary small" onclick="bulkAssignModal()">Gán User</button>
          <button class="btn secondary small" onclick="bulkReturnToRules()">Đưa về Rules</button>
          <button class="btn danger small" onclick="bulkRevokeModal()">Thu hồi</button>` : ''}
      </div>
    </div>
    <div style="height:12px"></div>
    <div class="card" style="padding:14px 14px 0">
      <div class="between">
        <b>Tìm trong danh sách</b>
        <span class="badge">${matches.length}${matches.length !== S.mailboxes.length ? ` / ${S.mailboxes.length}` : ''} khớp</span>
      </div>
      <div class="search-row" style="flex-wrap:nowrap">
        <input data-focus-key="mailbox-q" value="${esc(S.mailboxQ)}" autocomplete="off"
               placeholder="Tìm theo địa chỉ, owner, loại routing, user có quyền…"
               aria-label="Tìm mailbox"
               oninput="onMailboxTableFilter(this.value)"
               onkeydown="if(event.key==='Enter')this.blur()">
        ${S.mailboxQ ? `<button class="search-clear" onclick="clearMailboxTableFilter()" aria-label="Xoá tìm kiếm">${icon('clear', 14)}</button>` : ''}
      </div>
      <div class="hint" style="padding:0 0 12px">Tìm kiếm chạy cục bộ trên toàn bộ mailbox bạn có quyền, nên kết quả luôn đầy đủ và tức thì.</div>
    </div>
    <div class="card" style="padding:0;border-top-left-radius:0;border-top-right-radius:0">
      <div class="table-wrap" style="border:0">
        <table class="table">
          <thead><tr>
            <th><input type="checkbox" aria-label="Chọn tất cả mailbox"
              ${selected.length === S.mailboxes.length && S.mailboxes.length ? 'checked' : ''}
              onchange="toggleAllMailboxes(this.checked)"></th>
            <th>Địa chỉ</th><th>Owner</th><th>Routing</th><th>Access</th><th>Mail</th><th>Hành động</th>
          </tr></thead>
          <tbody>
          ${shown.map(m => `<tr>
            <td><input type="checkbox" ${selected.includes(m.id) ? 'checked' : ''} aria-label="Chọn ${esc(m.address)}" onchange="setSelectedMailbox(${m.id},this.checked)"></td>
            <td><b>${highlight(m.address, S.mailboxQ)}</b>
              <div class="tiny muted">#${m.id}${m.latest ? ` · ${esc(m.latest.envelope_from || '')} · ${fmt(m.latest.received_at)}` : ''}</div></td>
            <td>${m.owner_username ? highlight(m.owner_username, S.mailboxQ) : '—'}
              ${m.owner_user_id && S.users.length ? `<div class="tiny muted">${esc(S.users.find(u => u.id === m.owner_user_id)?.public_id || '')}</div>` : ''}</td>
            <td>${m.exact_override ? '<span class="badge green">EXACT</span>' : '<span class="badge">RULES</span>'}
              <div class="tiny muted">${esc(m.override_reason || (m.exact_override ? 'Manual override' : 'Prefix/Regex'))}</div></td>
            <td>${(m.users || []).map(u => `<span class="pill ${u.source === 'rule' ? 'rule' : 'manual'}">${esc(u.username)}${u.source === 'rule' ? ' • rule' : ''}</span>`).join(' ') || '—'}</td>
            <td><b>${m.message_count}</b>
              <div class="tiny muted">${m.unread_count} unread${m.latest?.otp ? ` · OTP ${esc(m.latest.otp)}` : ''}</div></td>
            <td><div class="actions">
              <button class="btn secondary small" onclick="mailboxDetailModal(${m.id})">Chi tiết</button>
              ${isAdmin ? `<button class="btn secondary small" onclick="assignOneModal(${m.id})">Gán</button>` : ''}
              ${(isAdmin || m.is_owner) ? `<button class="btn secondary small" onclick="oneAccessModal(${m.id},'share')">Shared</button>
                <button class="btn danger small" onclick="oneAccessModal(${m.id},'transfer')">Transfer</button>` : ''}
              ${m.exact_override && (isAdmin || m.is_owner) ? `<button class="btn ghost small" onclick="returnOneToRules(${m.id})">Rules</button>` : ''}
            </div></td>
          </tr>`).join('') || `<tr><td colspan="7"><div class="empty">${S.mailboxQ ? 'Không có mailbox khớp tìm kiếm.' : 'Chưa có mailbox.'}</div></td></tr>`}
          ${remaining > 0 ? `<tr><td colspan="7" style="text-align:center">
            <button class="btn secondary small" onclick="showMoreMailboxRows()">Tải thêm ${Math.min(remaining, 200)} mailbox</button>
          </td></tr>` : ''}
          </tbody>
        </table>
      </div>
    </div>`);
}

function clearMailboxFilter() {
  onMailboxFilter('');
}

function clearMailboxTableFilter() {
  onMailboxTableFilter('');
}

function showMoreMailboxes() {
  S.mailboxLimit += 120;
  render();
}

function showMoreMailboxRows() {
  S.mailboxTableLimit += 200;
  render();
}

function onMailboxTableFilter(value) {
  S.mailboxQ = value;
  S.mailboxTableLimit = 150;
  render();
}

/* --------------------------------------------------------------- rules */

function renderRulesPage() {
  const typeBadge = t => {
    if (t === 'catch_all') return '<span class="badge purple">CATCH_ALL</span>';
    if (t === 'domain') return '<span class="badge blue">DOMAIN</span>';
    if (t === 'prefix') return '<span class="badge green">PREFIX</span>';
    return '<span class="badge">REGEX</span>';
  };

  return pageShell('Routing rules (Catch-all, Domain, Prefix, Regex)',
    'Tự động phân quyền và cấp mailbox khi email đến theo thứ tự ưu tiên: Regex > Prefix > Domain > Catch-all.',
    `<button class="btn primary" onclick="newRuleModal()">＋ Tạo rule</button>
     <button class="btn secondary" onclick="ruleTestModal()">Test địa chỉ</button>`,
    `<div class="notice"><b>Thứ tự ưu tiên xử lý email đến:</b>
      <div style="margin-top:6px;display:flex;gap:16px;flex-wrap:wrap">
        <span>1. <b>Exact Override:</b> Gán cố định người sở hữu</span>
        <span>2. <b>Regex:</b> Khớp biểu thức chính quy</span>
        <span>3. <b>Prefix:</b> Khớp tiền tố đầu địa chỉ</span>
        <span>4. <b>Domain:</b> Khớp toàn bộ tên miền</span>
        <span>5. <b>Catch-All:</b> Toàn bộ email còn lại</span>
      </div>
    </div>
    <div class="card" style="padding:0">
      <div class="table-wrap" style="border:0">
        <table class="table">
          <thead><tr><th>Trạng thái</th><th>Loại</th><th>Pattern</th><th>User</th><th>Đang khớp</th><th>Mô tả</th><th>Thao tác</th></tr></thead>
          <tbody>${S.rules.map(r => `<tr>
            <td><span class="status-dot ${r.is_active ? '' : 'off'}"></span> ${r.is_active ? 'ON' : 'OFF'}</td>
            <td>${typeBadge(r.rule_type)}</td>
            <td><div class="rule-pattern">${esc(r.pattern)}</div></td>
            <td><b>${esc(r.target_username)}</b><div class="tiny muted">${esc(r.target_public_id)}</div></td>
            <td><span class="badge green">${r.match_mailboxes || 0}</span> mailbox</td>
            <td>${esc(r.description || '—')}</td>
            <td><div class="actions">
              <button class="btn secondary small" onclick="editRuleModal(${r.id})">Sửa</button>
              <button class="btn secondary small" onclick="toggleRule(${r.id},${r.is_active ? 'false' : 'true'})">${r.is_active ? 'Tắt' : 'Bật'}</button>
              <button class="btn danger small" onclick="deleteRule(${r.id})">Xóa</button>
            </div></td>
          </tr>`).join('') || '<tr><td colspan="7"><div class="empty">Chưa có routing rule.</div></td></tr>'}</tbody>
        </table>
      </div>
    </div>`);
}

/* --------------------------------------------------------------- users */

function renderUsersPage() {
  return pageShell('Người dùng',
    'Tạo user, User ID, API key và quản lý toàn bộ mailbox/rule của từng user. Quyền tạo mã giới thiệu mặc định là TẮT.',
    `<button class="btn primary" onclick="newUserModal()">＋ Tạo user</button>`,
    `<div class="notice ok"><b>Luồng chuẩn:</b> tạo user → tạo/import địa chỉ → gán access, hoặc tạo Prefix/Regex rule để tự động cấp mailbox. Exact assignment luôn ưu tiên.</div>
    <div class="notice info"><b>Referral:</b> user mới không có quyền tạo mã giới thiệu. Bật quyền đó trong cột <i>Mã giới thiệu</i> hoặc tại trang <b>Mã giới thiệu</b>.</div>
    <div style="height:12px"></div>
    <div class="card" style="padding:0">
      <div class="table-wrap" style="border:0">
        <table class="table">
          <thead><tr><th>User</th><th>User ID</th><th>Role</th><th>Trạng thái</th><th>Mã giới thiệu</th><th>Đăng ký qua</th><th>Mailbox</th><th>Rule</th><th>Thao tác</th></tr></thead>
          <tbody>${S.users.map(u => `<tr>
            <td><div class="user-summary">
              <div class="avatar" style="background:${avatarBg(u.username)}">${initials(u.username)}</div>
              <div><b>${esc(u.username)}</b><div class="tiny muted">${esc(u.api_key_prefix)}…</div></div>
            </div></td>
            <td><span class="kbd">${esc(u.public_id)}</span></td>
            <td><span class="badge">${esc(u.role)}</span></td>
            <td>${u.is_active ? '<span class="badge green">ACTIVE</span>' : '<span class="badge red">DISABLED</span>'}</td>
            <td>${u.role === 'admin' ? '<span class="muted tiny">—</span>' :
              `<label class="switch" title="${u.can_generate_codes ? 'Đang cho phép tạo mã' : 'Đang chặn tạo mã'}">
                 <input type="checkbox" ${u.can_generate_codes ? 'checked' : ''} aria-label="Cho phép ${esc(u.username)} tạo mã giới thiệu"
                        onchange="setUserCodePermission(${u.id},this.checked)">
                 <span class="track"></span>
               </label>
               <div class="tiny muted">${u.can_generate_codes ? 'Cho phép' : 'Đang tắt'}</div>`}</td>
            <td><span class="badge">${esc(u.created_via === 'referral' ? 'Referral' : 'Admin')}</span></td>
            <td>${S.mailboxes.filter(m => (m.users || []).some(x => x.public_id === u.public_id)).length}</td>
            <td>${u.role === 'user' ? S.rules.filter(r => r.target_public_id === u.public_id).length : '—'}</td>
            <td><div class="actions">${u.role === 'user' ? `
              <button class="btn primary small" onclick="userDetailModal(${u.id})">Quản lý</button>
              <button class="btn secondary small" onclick="newRuleModal(null,'${esc(u.public_id)}')">＋ Rule</button>
              <button class="btn secondary small" onclick="toggleUser(${u.id},${u.is_active ? 'false' : 'true'})">${u.is_active ? 'Disable' : 'Enable'}</button>` : '—'}</div></td>
          </tr>`).join('')}</tbody>
        </table>
      </div>
    </div>`);
}

async function setUserCodePermission(userId, enabled) {
  const previous = S.users.find(u => u.id === userId)?.can_generate_codes;
  try {
    const d = await api(`/api/admin/users/${userId}/code-permission`, {
      method: 'POST', body: JSON.stringify({ can_generate_codes: enabled }),
    });
    S.users = S.users.map(u => (u.id === userId ? { ...u, can_generate_codes: d.user.can_generate_codes } : u));
    const retired = d.retired_codes ? ` ${d.retired_codes} mã cũ đã bị vô hiệu hoá.` : '';
    toast(enabled
      ? `Đã cho phép ${d.user.username} tạo mã giới thiệu.${retired}`
      : `Đã thu hồi quyền tạo mã của ${d.user.username}.${retired}`, true);
  } catch (e) {
    // Roll the switch back so the UI never claims a permission that was refused.
    S.users = S.users.map(u => (u.id === userId ? { ...u, can_generate_codes: previous } : u));
    toast(e.message);
    render();
  }
}

/* ------------------------------------------------------------ referrals */

function renderReferralsPage() {
  const r = S.referral || {};
  const isAdmin = S.me.role === 'admin';
  const mine = `
    <div class="grid">
      <div class="col-6">
        <div class="card">
          <div class="between">
            <h3 style="margin:0">Mã giới thiệu của tôi</h3>
            <span class="badge ${r.permission_active ? 'green' : 'red'}">${r.permission_active ? 'Được phép tạo' : 'Đang tắt'}</span>
          </div>
          ${!r.can_generate ? `<div class="notice" style="margin-top:12px">${esc(r.reason || 'Bạn chưa được cấp quyền tạo mã giới thiệu.')}</div>` : ''}
          ${r.code ? `<div class="stack wrap" style="margin-top:12px">
              <span class="badge ${r.code.status === 'active' ? 'green' : 'yellow'}">${esc(r.code.status)}</span>
              <span class="badge">${r.code.remaining}/${r.code.max_uses} lượt còn lại</span>
              ${r.code.expires_at ? `<span class="badge">Hết hạn ${fmt(r.code.expires_at)}</span>` : ''}
            </div>
            <div class="hint" style="margin-top:10px">Mã được lưu dưới dạng hash nên không thể hiển thị lại. Nếu bạn làm mất, hãy tạo mã mới — mã cũ sẽ tự động bị vô hiệu hoá.</div>`
          : `<div class="hint" style="margin-top:10px">Bạn chưa có mã nào. Bấm nút bên cạnh để tạo.</div>`}
          <div class="stack wrap" style="margin-top:14px">
            <button class="btn primary" ${r.can_generate ? '' : 'disabled'} onclick="generateReferralCode()">
              ${r.code ? 'Tạo mã mới (vô hiệu mã cũ)' : 'Tạo mã giới thiệu'}
            </button>
            <button class="btn secondary" onclick="showCodeHistory()">Lịch sử mã</button>
          </div>
          <div id="referral-code-slot"></div>
        </div>
      </div>
      <div class="col-6">
        <div class="card">
          <h3 style="margin-top:0">Người đã đăng ký</h3>
          <div class="stat-grid" style="margin-bottom:12px">
            <div class="stat-card"><div class="stat-num">${r.totals?.uses || 0}</div><div class="stat-label">Lượt đã dùng</div></div>
            <div class="stat-card"><div class="stat-num">${(r.registrations || []).length}</div><div class="stat-label">Tài khoản</div></div>
            <div class="stat-card"><div class="stat-num">${r.totals?.capacity || 0}</div><div class="stat-label">Tổng lượt</div></div>
          </div>
          ${(r.registrations || []).length ? r.registrations.map(x => `<div class="attachment">
              <div class="attachment-name"><b>${esc(x.username)}</b>
                <div class="tiny muted">${esc(x.public_id)} · ${fmt(x.created_at)}</div></div>
              <span class="badge ${x.is_active ? 'green' : 'red'}">${x.is_active ? 'ACTIVE' : 'DISABLED'}</span>
            </div>`).join('') : '<div class="empty">Chưa có ai dùng mã của bạn.</div>'}
        </div>
      </div>
    </div>
    <div style="height:12px"></div>
    <div class="card soft">
      <b>Cách hoạt động</b>
      <div class="hint" style="margin-top:6px">
        Mỗi mã có số lượt dùng và thời hạn riêng. Tài khoản đăng ký qua mã sẽ có
        <b>quyền tạo mã = TẮT</b> và không có mailbox nào cho tới khi admin hoặc routing rule cấp quyền.
        Toàn bộ thay đổi được ghi vào audit log.
      </div>
    </div>`;

  return pageShell('Mã giới thiệu',
    'Tạo mã đăng ký cho người mới và theo dõi ai đã dùng.',
    `<button class="btn secondary" onclick="loadAdminReferral()">↻</button>`,
    mine + (isAdmin ? renderAdminReferralSection() : ''));
}

function renderAdminReferralSection() {
  if (!adminReferral) {
    return `<div class="card" style="margin-top:12px"><div class="empty">Đang tải dữ liệu quản trị…</div></div>`;
  }
  const s = adminReferral.settings;
  const st = adminReferral.stats;
  const toggle = (key, on, label, hint) => `<div class="toggle-row">
      <div><div class="label">${esc(label)}</div><div class="hint">${esc(hint)}</div></div>
      <label class="switch">
        <input type="checkbox" ${on ? 'checked' : ''} aria-label="${esc(label)}" onchange="setReferralSetting('${key}',this.checked)">
        <span class="track"></span>
      </label>
    </div>`;

  return `<div style="height:12px"></div>
    <div class="grid">
      <div class="col-6">
        <div class="card">
          <h3 style="margin-top:0">Công tắc toàn cục</h3>
          ${toggle('registration_enabled', s.registration_enabled,
            'Cho phép đăng ký tự do',
            'Bật để mở form đăng ký. Tắt để đóng ngay, không cần deploy lại. Mã giới thiệu vẫn là bắt buộc.')}
          ${toggle('referral_code_generation', s.referral_code_generation,
            'Cho phép tạo mã giới thiệu',
            'Công tắc chung. Tắt sẽ chặn mọi user tạo mã, kể cả user đã được cấp quyền. Mã đã cấp vẫn dùng được.')}
          <div class="stat-grid" style="margin-top:12px">
            <div class="stat-card"><div class="stat-num">${st.codes}</div><div class="stat-label">Tổng mã</div></div>
            <div class="stat-card"><div class="stat-num">${st.registrations}</div><div class="stat-label">Đăng ký</div></div>
            <div class="stat-card"><div class="stat-num">${st.uses}</div><div class="stat-label">Lượt dùng</div></div>
            <div class="stat-card"><div class="stat-num">${st.personal_codes}</div><div class="stat-label">Mã tự tạo</div></div>
          </div>
        </div>
      </div>
      <div class="col-6">
        <div class="card">
          <h3 style="margin-top:0">Tạo mã thủ công</h3>
          <div class="hint">Dùng khi bạn muốn cấp quyền đăng ký mà không cần user tự tạo. Mã chỉ hiện đúng một lần.</div>
          <div class="field"><label>Số lượt dùng</label>
            <input id="ar-uses" type="number" min="1" max="1000" value="1"></div>
          <div class="field"><label>Hết hạn sau (ngày, 0 = không)</label>
            <input id="ar-ttl" type="number" min="0" max="3650" value="0"></div>
          <div class="field"><label>Ghi chú</label><input id="ar-note" placeholder="Ví dụ: vòng mời đợt 1"></div>
          <div class="field"><label>Cấp cho User ID (để trống = mã chung)</label>
            <input id="ar-owner" list="user-list" placeholder="U-XXXXXXXXXX"></div>
          <button class="btn primary" onclick="createAdminReferral()">Tạo mã</button>
        </div>
      </div>
    </div>
    <div style="height:12px"></div>
    <div class="card" style="padding:0">
      <div class="page-head" style="padding:14px 16px 0;margin:0">
        <div><div class="page-title" style="font-size:18px">Danh sách mã</div>
          <div class="muted tiny">Mật mã chỉ hiện 8 ký tự đầu của hash để đối chiếu, không hiện mã gốc.</div></div>
      </div>
      <div class="table-wrap" style="border:0">
        <table class="table">
          <thead><tr><th>#</th><th>Fingerprint</th><th>Chủ sở hữu</th><th>Trạng thái</th><th>Đã dùng</th><th>Hết hạn</th><th>Ghi chú</th><th>Thao tác</th></tr></thead>
          <tbody>${adminReferral.codes.map(c => `<tr>
            <td>${c.id}</td>
            <td><span class="kbd">${esc(c.fingerprint)}</span></td>
            <td>${esc(c.owner_username || (c.owner_user_id ? `#${c.owner_user_id}` : 'Mã chung'))}</td>
            <td><span class="badge ${c.status === 'active' ? 'green' : (c.status === 'disabled' ? 'red' : 'yellow')}">${esc(c.status)}</span></td>
            <td>${c.uses}/${c.max_uses}</td>
            <td class="tiny">${c.expires_at ? fmt(c.expires_at) : '—'}</td>
            <td class="tiny">${esc(c.note || '—')}</td>
            <td><div class="actions">
              <button class="btn secondary small" onclick="setReferralCodeActive(${c.id},${c.is_active ? 'false' : 'true'})">${c.is_active ? 'Tắt' : 'Bật'}</button>
              <button class="btn secondary small" onclick="bumpReferralUses(${c.id},${c.max_uses})">Sửa lượt</button>
              <button class="btn danger small" onclick="deleteReferralCode(${c.id})">Xóa</button>
            </div></td>
          </tr>`).join('') || '<tr><td colspan="8"><div class="empty">Chưa có mã nào.</div></td></tr>'}</tbody>
        </table>
      </div>
    </div>
    <div style="height:12px"></div>
    <div class="card" style="padding:0">
      <div class="page-head" style="padding:14px 16px 0;margin:0">
        <div><div class="page-title" style="font-size:18px">Lượt đăng ký</div>
          <div class="muted tiny">Mỗi tài khoản đều đã bị tạo với quyền tạo mã = TẮT.</div></div>
      </div>
      <div class="table-wrap" style="border:0">
        <table class="table" style="min-width:560px">
          <thead><tr><th>Thời gian</th><th>Tài khoản</th><th>User ID</th><th>Trạng thái</th><th>Mã</th><th>Giới thiệu bởi</th><th>IP</th></tr></thead>
          <tbody>${adminReferral.registrations.map(g => `<tr>
            <td class="tiny">${fmt(g.created_at)}</td>
            <td><b>${esc(g.username)}</b></td>
            <td><span class="kbd">${esc(g.public_id)}</span></td>
            <td>${g.is_active ? '<span class="badge green">ACTIVE</span>' : '<span class="badge red">DISABLED</span>'}</td>
            <td class="tiny">${g.code_id ? `#${g.code_id} · ${esc(g.code_fingerprint || '')}` : '—'}</td>
            <td class="tiny">${esc(g.referrer_username || '—')}</td>
            <td class="tiny mono">${esc(g.ip || '—')}</td>
          </tr>`).join('') || '<tr><td colspan="7"><div class="empty">Chưa có lượt đăng ký nào.</div></td></tr>'}</tbody>
        </table>
      </div>
    </div>`;
}

async function generateReferralCode() {
  try {
    const d = await api('/api/referrals/code', { method: 'POST', body: JSON.stringify({}) });
    const slot = document.getElementById('referral-code-slot');
    if (slot) {
      slot.innerHTML = `<div class="notice ok" style="margin-top:12px">
        <b>Mã chỉ hiện một lần. Hãy copy ngay.</b>
        <div class="code-box" style="margin-top:8px" data-otp="${esc(d.code)}" id="new-referral-code">${esc(d.code)}</div>
        <div class="stack" style="margin-top:8px">
          <button class="btn success small" onclick="copyText(document.getElementById('new-referral-code').dataset.otp)">Copy mã</button>
        </div>
      </div>`;
    }
    const m = await api('/api/referrals/me');
    S.referral = m;
    if (S.me.role === 'admin') loadAdminReferral();
    toast('Đã tạo mã giới thiệu.', true);
  } catch (e) {
    toast(e.message);
  }
}

async function showCodeHistory() {
  try {
    const d = await api('/api/me/referrals/code-history');
    modal('Lịch sử mã giới thiệu', d.items.length
      ? `<div class="table-wrap"><table class="table" style="min-width:420px">
          <thead><tr><th>#</th><th>Fingerprint</th><th>Trạng thái</th><th>Đã dùng</th><th>Ngày tạo</th></tr></thead>
          <tbody>${d.items.map(i => `<tr>
            <td>${i.id}</td>
            <td><span class="kbd">${esc(i.fingerprint)}</span></td>
            <td><span class="badge ${i.status === 'active' ? 'green' : 'yellow'}">${esc(i.status)}</span></td>
            <td>${i.uses}/${i.max_uses}</td>
            <td class="tiny">${fmt(i.created_at)}</td>
          </tr>`).join('')}</tbody>
        </table></div>`
      : '<div class="empty">Chưa có mã nào.</div>');
  } catch (e) {
    toast(e.message);
  }
}

let adminRetention = null;

async function loadAdminRetention() {
  try {
    adminRetention = await api('/api/admin/retention/settings');
    if (S.view === 'referrals') render();
  } catch (_) {}
}

async function setRetentionSetting(key, val) {
  try {
    const res = await api('/api/admin/retention/settings', {
      method: 'PATCH',
      body: JSON.stringify({ [key]: val }),
    });
    if (adminRetention) {
      adminRetention.auto_delete_enabled = res.auto_delete_enabled;
      adminRetention.auto_delete_days = res.auto_delete_days;
    }
    if (adminSystem) {
      adminSystem.auto_delete_enabled = res.auto_delete_enabled;
      adminSystem.auto_delete_days = res.auto_delete_days;
    }
    toast('Đã cập nhật cài đặt lưu trữ.', true);
    render();
  } catch (e) {
    toast(e.message);
    render();
  }
}

async function runManualCleanup() {
  const days = adminSystem?.auto_delete_days || adminRetention?.auto_delete_days || 7;
  if (!confirm(`Bạn có chắc muốn xoá toàn bộ thư cũ hơn ${days} ngày để giải phóng dung lượng đĩa?`)) return;
  try {
    const res = await api('/api/admin/retention/cleanup', {
      method: 'POST',
      body: JSON.stringify({ days }),
    });
    toast(`Đã dọn dẹp thành công ${res.deleted_count} thư cũ!`, true);
    if (S.view === 'system') await loadSystemStats();
    await refreshData();
    await loadMessages(false);
    render();
  } catch (e) {
    toast(e.message);
  }
}

async function loadAdminReferral() {
  try {
    adminReferral = await api('/api/admin/referrals?limit=200');
    if (S.view === 'referrals') render();
  } catch (e) {
    toast(e.message);
  }
}

function loadAdminReferralAndRender() {
  loadAdminReferral();
}

async function setReferralSetting(key, enabled) {
  try {
    const d = await api('/api/admin/referrals/settings', {
      method: 'PATCH', body: JSON.stringify({ [key]: enabled }),
    });
    adminReferral.settings = d.settings;
    S.referral = await api('/api/referrals/me');
    toast('Đã cập nhật công tắc.', true);
  } catch (e) {
    toast(e.message);
    render();
  }
}

async function createAdminReferral() {
  try {
    const d = await api('/api/admin/referrals', {
      method: 'POST',
      body: JSON.stringify({
        max_uses: Number(document.getElementById('ar-uses').value || 1),
        ttl_days: Number(document.getElementById('ar-ttl').value || 0),
        note: document.getElementById('ar-note').value,
        owner_public_id: document.getElementById('ar-owner').value.trim().toUpperCase(),
      }),
    });
    modal('Mã đã tạo', `<div class="notice ok"><b>Mã chỉ hiện một lần. Hãy copy ngay.</b></div>
      <div class="code-box" id="admin-new-code">${esc(d.code)}</div>
      <div class="stack" style="margin-top:10px">
        <button class="btn success" onclick="copyText(document.getElementById('admin-new-code').textContent)">Copy mã</button>
        <button class="btn secondary" onclick="closeModal();loadAdminReferral()">Đóng</button>
      </div>`);
    loadAdminReferral();
  } catch (e) {
    toast(e.message);
  }
}

async function setReferralCodeActive(id, active) {
  try {
    await api(`/api/admin/referrals/${id}`, { method: 'PATCH', body: JSON.stringify({ is_active: active }) });
    toast(active ? 'Đã bật mã.' : 'Đã tắt mã.', true);
    loadAdminReferral();
  } catch (e) {
    toast(e.message);
  }
}

async function bumpReferralUses(id, currentMax) {
  const raw = prompt('Số lượt dùng cho phép (không thể nhỏ hơn số đã dùng):', String(currentMax));
  if (raw === null) return;
  const value = Number(raw);
  if (!Number.isFinite(value)) return toast('Số lượt không hợp lệ.');
  try {
    await api(`/api/admin/referrals/${id}`, { method: 'PATCH', body: JSON.stringify({ max_uses: value }) });
    toast('Đã cập nhật số lượt.', true);
    loadAdminReferral();
  } catch (e) {
    toast(e.message);
  }
}

async function deleteReferralCode(id) {
  if (!confirm('Xóa mã giới thiệu này? Mã sẽ không dùng được nữa. Lịch sử đăng ký vẫn được giữ.')) return;
  try {
    await api(`/api/admin/referrals/${id}`, { method: 'DELETE' });
    toast('Đã xóa mã.', true);
    loadAdminReferral();
  } catch (e) {
    toast(e.message);
  }
}

/* ----------------------------------------------------- notifications etc */

function renderNotificationsPage() {
  return pageShell('Thông báo', 'Cảnh báo về Shared, Transfer, Revoke, referral và routing.',
    `<button class="btn secondary" onclick="markAllNotificationsRead()">Đánh dấu đã đọc</button>`,
    `<div class="card">${S.notifications.map(n => `<div class="attachment">
        <div style="flex:1;min-width:0">
          <div class="between"><b>${esc(n.title)}</b><span class="tiny muted">${fmt(n.created_at)}</span></div>
          <div class="tiny" style="margin-top:3px">${esc(n.body)}</div>
          ${n.read_at ? '' : '<span class="badge yellow" style="margin-top:6px">Chưa đọc</span>'}
        </div>
      </div>`).join('') || '<div class="empty">Chưa có thông báo.</div>'}</div>`);
}

function renderAuditPage() {
  return pageShell('Audit log', 'Theo dõi mọi thay đổi quyền mailbox, routing rule và referral.',
    `<button class="btn secondary" onclick="refreshData().then(render)">Refresh</button>`,
    `<div class="card" style="padding:0">
      <div class="table-wrap" style="border:0">
        <table class="table">
          <thead><tr><th>Thời gian</th><th>Actor</th><th>Action</th><th>Mailbox</th><th>Target</th><th>Chi tiết</th></tr></thead>
          <tbody>${S.audit.map(a => `<tr>
            <td class="tiny">${fmt(a.created_at)}</td>
            <td>${esc(a.actor_username || 'system')}</td>
            <td><span class="badge">${esc(a.action)}</span></td>
            <td>${esc(a.mailbox_address || '—')}</td>
            <td>${esc(a.target_username || '—')}</td>
            <td>${esc(a.details)}</td>
          </tr>`).join('') || '<tr><td colspan="6"><div class="empty">Chưa có log.</div></td></tr>'}</tbody>
        </table>
      </div>
    </div>`);
}

function renderAccountPage() {
  return pageShell('Tài khoản', 'Thông tin đăng nhập, mật khẩu và API key của bạn.',
    `<button class="btn danger" onclick="logout()">Đăng xuất</button>`,
    `<div class="grid">
      <div class="col-6">
        <div class="card">
          <h3 style="margin-top:0">Thông tin</h3>
          <div class="field"><label>Username</label><input disabled value="${esc(S.me.username)}"></div>
          <div class="field"><label>User ID</label><input class="mono" disabled value="${esc(S.me.public_id)}"></div>
          <div class="field"><label>API key prefix</label><input class="mono" disabled value="${esc(S.me.api_key_prefix)}…"></div>
          <div class="field"><label>Quyền tạo mã giới thiệu</label>
            <input disabled value="${S.me.can_generate_codes ? 'Được phép' : 'Đang tắt (mặc định)'}"></div>
        </div>
      </div>
      <div class="col-6">
        <div class="card">
          <h3 style="margin-top:0">Đổi mật khẩu</h3>
          <div class="field"><label>Mật khẩu hiện tại</label><input id="pw-old" type="password" autocomplete="current-password"></div>
          <div class="field"><label>Mật khẩu mới</label><input id="pw-new" type="password" autocomplete="new-password" placeholder="Tối thiểu 10 ký tự"></div>
          <button class="btn primary" onclick="changePassword()">Đổi mật khẩu</button>
        </div>
        <div class="card" style="margin-top:12px">
          <h3 style="margin-top:0">API key</h3>
          <div class="hint">Regenerate sẽ vô hiệu key cũ ngay lập tức.</div>
          <button class="btn secondary" style="margin-top:8px" onclick="regenKey()">Regenerate API key</button>
        </div>
      </div>
      <div class="col-12">
        <div class="card">
          <h3 style="margin-top:0">Developer API</h3>
          <div class="hint">Bearer API key dùng cho <b>toàn bộ</b> API đọc/ghi (không cần cookie/CSRF). Web UI chỉ là giao diện phụ.</div>
          <pre class="mono" style="white-space:pre-wrap;font-size:12px;margin-top:10px"># Dashboard / mailbox / messages — cùng một key
curl -s "${location.origin}/api/dashboard" \\
  -H "Authorization: Bearer sk-otp-YOUR_KEY"

curl -s -X POST "${location.origin}/api/mailboxes/create" \\
  -H "Authorization: Bearer sk-otp-YOUR_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"address":"otp-test@example.com"}'

# Poll OTP (shortcut /api/v1)
SINCE="2026-01-01T00:00:00+00:00"
curl -s "${location.origin}/api/v1/inbox?address=otp@example.com&amp;otp_only=1&amp;limit=10&amp;since=$SINCE" \\
  -H "Authorization: Bearer sk-otp-YOUR_KEY"</pre>
        </div>
      </div>
    </div>`);
}

async function changePassword() {
  const oldp = document.getElementById('pw-old')?.value || '';
  const newp = document.getElementById('pw-new')?.value || '';
  if (!oldp) return toast('Nhập mật khẩu hiện tại.');
  try {
    await api('/api/me/password', {
      method: 'POST', body: JSON.stringify({ old_password: oldp, new_password: newp }),
    });
    toast('Đổi mật khẩu thành công. Phiên đăng nhập cũ đã bị vô hiệu.', true);
    // Every session for this account is now invalid, so re-authenticate.
    setTimeout(() => location.reload(), 900);
  } catch (e) {
    toast(e.message);
  }
}

async function regenKey() {
  try {
    const d = await api('/api/me/regenerate-key', { method: 'POST' });
    modal('API key mới', `<div class="notice ok">API key chỉ hiển thị lần này. Hãy lưu ngay.</div>
      <div class="api-key-box" id="new-api-key">${esc(d.api_key)}</div>`);
    S.me.api_key_prefix = d.api_key.slice(0, 16);
  } catch (e) {
    toast(e.message);
  }
}

async function markAllNotificationsRead() {
  try {
    await api('/api/notifications/read', { method: 'POST' });
    S.notifications = S.notifications.map(x => ({ ...x, read_at: x.read_at || new Date().toISOString() }));
    render();
  } catch (e) {
    toast(e.message);
  }
}

/* ========================================================================
   Admin — users
   ===================================================================== */

function newUserModal() {
  modal('Tạo user', `<div class="field"><label>Username</label><input id="nu-name" autocomplete="off"></div>
    <div class="field"><label>Password</label><input id="nu-pass" type="password" autocomplete="new-password" placeholder="Tối thiểu 10 ký tự"></div>
    <div class="notice info">User mới bắt đầu với quyền tạo mã giới thiệu <b>TẮT</b>. Bạn có thể bật sau trong trang Người dùng.</div>
    <button class="btn primary" onclick="createUser()">Tạo user</button>`);
}

async function createUser() {
  try {
    const d = await api('/api/admin/users', {
      method: 'POST',
      body: JSON.stringify({
        username: document.getElementById('nu-name').value,
        password: document.getElementById('nu-pass').value,
      }),
    });
    modal('User đã tạo', `<div class="notice ok"><b>${esc(d.user.username)}</b> · ${esc(d.user.public_id)}</div>
      <div class="field"><label>API key — chỉ hiển thị lần này</label><div class="api-key-box" id="new-user-key">${esc(d.api_key)}</div></div>
      <button class="btn primary" onclick="closeModal()">Đã lưu</button>`);
    await refreshData();
    render();
  } catch (e) {
    toast(e.message);
  }
}

async function toggleUser(id, next) {
  try {
    await api(`/api/admin/users/${id}/status`, { method: 'POST', body: JSON.stringify({ is_active: next }) });
    toast(next ? 'User đã enable.' : 'User đã disable.', true);
    await refreshData();
    render();
  } catch (e) {
    toast(e.message);
  }
}

function renderUserMailboxResults(items) {
  const box = document.getElementById('user-mailbox-results');
  if (!box) return;
  box.innerHTML = items.map(m => `<label class="assignment-row">
      <input type="checkbox" ${userAdminMailboxSelection.has(m.id) ? 'checked' : ''} onchange="toggleUserMailbox(${m.id},this.checked)">
      <span class="address"><b>${esc(m.address)}</b>
        <span class="tiny muted">${m.exact_override ? ' · EXACT' : ''}${m.assigned ? ' · Đã có access' : ''}</span></span>
      <span class="badge ${m.assigned ? 'green' : ''}">${m.assigned ? 'Đã gán' : 'Chưa gán'}</span>
    </label>`).join('') || '<div class="empty">Nhập ít nhất 2 ký tự để tìm mailbox.</div>';
}

function toggleUserMailbox(id, checked) {
  if (checked) userAdminMailboxSelection.add(Number(id));
  else userAdminMailboxSelection.delete(Number(id));
  const count = document.getElementById('user-mailbox-count');
  if (count) count.textContent = `${userAdminMailboxSelection.size} đã chọn`;
}

async function searchUserMailboxes() {
  const query = document.getElementById('user-mailbox-search')?.value.trim();
  if (!query) {
    renderUserMailboxResults([]);
    return;
  }
  try {
    const d = await api(`/api/admin/mailboxes/search?q=${encodeURIComponent(query)}&user_id=${userAdminUserId}`);
    renderUserMailboxResults(d.items || []);
  } catch (e) {
    toast(e.message);
  }
}

async function assignUserMailboxes() {
  if (!userAdminMailboxSelection.size) return toast('Chọn ít nhất một mailbox.');
  try {
    const d = await api('/api/admin/assign', {
      method: 'POST',
      body: JSON.stringify({
        mailbox_ids: [...userAdminMailboxSelection],
        target_public_ids: [S.users.find(x => x.id === userAdminUserId)?.public_id],
      }),
    });
    toast(d.notice, true);
    userAdminMailboxSelection.clear();
    await refreshData();
    userDetailModal(userAdminUserId);
  } catch (e) {
    toast(e.message);
  }
}

async function saveUserAdmin() {
  const id = userAdminUserId;
  const username = document.getElementById('ua-username')?.value.trim();
  const role = document.getElementById('ua-role')?.value;
  const password = document.getElementById('ua-password')?.value || '';
  try {
    await api(`/api/admin/users/${id}`, {
      method: 'PATCH', body: JSON.stringify({ username, role, new_password: password }),
    });
    toast('Đã cập nhật thông tin user.', true);
    await refreshData();
    userDetailModal(id);
  } catch (e) {
    toast(e.message);
  }
}

async function userDetailModal(id) {
  try {
    const d = await api(`/api/admin/users/${id}/detail`);
    const u = d.user;
    userAdminUserId = id;
    userAdminMailboxSelection = new Set();
    modal(`Quản lý user · ${u.username}`,
      `<div class="user-admin-grid">
        <div class="card">
          <div class="between">
            <div><h3 style="margin:0">Thông tin tài khoản</h3>
              <div class="hint">Cập nhật role, username hoặc reset password.</div></div>
            <span class="badge ${u.is_active ? 'green' : 'red'}">${u.is_active ? 'ACTIVE' : 'DISABLED'}</span>
          </div>
          <div class="field"><label>Username</label><input id="ua-username" value="${esc(u.username)}" autocomplete="off"></div>
          <div class="field"><label>Role</label><select id="ua-role">
            <option value="user" ${u.role === 'user' ? 'selected' : ''}>User</option>
            <option value="admin" ${u.role === 'admin' ? 'selected' : ''}>Admin</option>
          </select></div>
          <div class="field"><label>User ID</label><input class="mono" disabled value="${esc(u.public_id)}"></div>
          <div class="field"><label>Reset password <span class="muted">(để trống nếu không đổi)</span></label>
            <input id="ua-password" type="password" autocomplete="new-password" placeholder="Tối thiểu 10 ký tự"></div>
          <div class="toggle-row" style="margin-top:6px">
            <div><div class="label">Quyền tạo mã giới thiệu</div>
              <div class="hint">Mặc định TẮT. Bật để user này tự tạo mã đăng ký cho người khác.</div></div>
            <label class="switch">
              <input type="checkbox" ${u.can_generate_codes ? 'checked' : ''}
                     aria-label="Cho phép tạo mã giới thiệu" onchange="setUserCodePermission(${u.id},this.checked)">
              <span class="track"></span>
            </label>
          </div>
          <div class="stack wrap" style="margin-top:12px">
            <button class="btn primary" onclick="saveUserAdmin()">Lưu thay đổi</button>
            <button class="btn secondary" onclick="closeModal();toggleUser(${id},${u.is_active ? 'false' : 'true'})">${u.is_active ? 'Disable' : 'Enable'}</button>
          </div>
          <div class="notice" style="margin-top:14px"><b>${d.mailboxes.length}</b> mailbox đang có quyền · <b>${d.rules.length}</b> routing rule</div>
        </div>
        <div class="card">
          <div class="between">
            <div><h3 style="margin:0">Gán mailbox nhanh</h3>
              <div class="hint">Tìm theo toàn bộ hoặc một phần địa chỉ email, chọn nhiều rồi gán exact.</div></div>
            <span id="user-mailbox-count" class="badge">0 đã chọn</span>
          </div>
          <div class="search-row" style="margin-top:14px;flex-wrap:nowrap">
            <input id="user-mailbox-search" placeholder="Tìm email, ví dụ abc001@example.com"
                   oninput="debounceSearchUserMailboxes()" onkeydown="if(event.key==='Enter')searchUserMailboxes()">
            <button class="btn primary" onclick="searchUserMailboxes()">Tìm</button>
          </div>
          <div id="user-mailbox-results" class="assignment-results" style="margin-top:10px" data-scroll="assignment">
            <div class="empty">Nhập ít nhất 2 ký tự để tìm mailbox.</div>
          </div>
          <div class="stack" style="margin-top:12px">
            <button class="btn success" onclick="assignUserMailboxes()">Gán ${esc(u.username)}</button>
            <button class="btn ghost" onclick="closeModal()">Đóng</button>
          </div>
        </div>
        <div class="card">
          <div class="between"><b>Routing rules</b>
            <button class="btn secondary small" onclick="closeModal();newRuleModal(null,'${esc(u.public_id)}')">＋ Rule</button></div>
          ${d.rules.map(r => `<div class="attachment"><div style="flex:1">
            <b>${esc(r.rule_type.toUpperCase())}</b> <span class="status-dot ${r.is_active ? '' : 'off'}"></span>
            <div class="tiny mono">${esc(r.pattern)}</div>
            <div class="tiny muted">${esc(r.description || '')}</div>
          </div></div>`).join('') || '<div class="empty">Chưa có rule.</div>'}
        </div>
        <div class="card">
          <div class="between"><b>Mailbox access hiện tại</b>
            <button class="btn secondary small" onclick="closeModal();goView('mailboxes')">Mở toàn bộ mailbox</button></div>
          <div class="assignment-results" style="margin-top:10px;max-height:300px">${d.mailboxes.map(m => `<div class="assignment-row">
            <span class="address"><b>${esc(m.address)}</b>
              <span class="tiny muted">${m.source === 'rule' ? ` · Rule #${m.rule_id}` : ' · Manual'}</span></span>
            <span class="badge ${m.exact_override ? 'green' : ''}">${m.owner_user_id === u.id ? 'OWNER' : m.exact_override ? 'EXACT' : 'RULES'}</span>
          </div>`).join('') || '<div class="empty">Chưa có mailbox.</div>'}</div>
        </div>
      </div>`);
  } catch (e) {
    toast(e.message);
  }
}

const debounceSearchUserMailboxes = debounce(() => searchUserMailboxes(), 300);

/* ========================================================================
   Admin — mailboxes
   ===================================================================== */

const RANDOM_WORDS_ADJ = ['fast', 'blue', 'cool', 'smart', 'swift', 'lucky', 'hyper', 'iron', 'gold', 'neon', 'brave', 'sharp', 'safe', 'clean', 'prime'];
const RANDOM_WORDS_NOUN = ['tiger', 'falcon', 'hawk', 'fox', 'wolf', 'otter', 'panda', 'bear', 'lion', 'eagle', 'byte', 'star', 'rocket', 'shield', 'box'];

function generateRandomAlias() {
  const mode = Math.random() > 0.35 ? 'words' : 'code';
  let alias = '';
  if (mode === 'words') {
    const adj = RANDOM_WORDS_ADJ[Math.floor(Math.random() * RANDOM_WORDS_ADJ.length)];
    const noun = RANDOM_WORDS_NOUN[Math.floor(Math.random() * RANDOM_WORDS_NOUN.length)];
    const num = Math.floor(10 + Math.random() * 89);
    alias = `${adj}-${noun}-${num}`;
  } else {
    alias = 'otp-' + Math.random().toString(36).substring(2, 8);
  }
  const el = document.getElementById('mb-prefix');
  if (el) {
    el.value = alias;
    updateMailboxPreview();
  }
}

function updateMailboxPreview() {
  const prefix = (document.getElementById('mb-prefix')?.value || '').trim();
  const domain = document.getElementById('mb-domain')?.value || (S.domains[0]?.name || 'example.com');
  const previewEl = document.getElementById('mb-preview');
  const btnEl = document.getElementById('mb-submit-btn');
  const reasonWrap = document.getElementById('mb-reason-wrap');
  
  const selectedDomainObj = S.domains.find(d => d.name === domain);
  const isApproval = selectedDomainObj?.access_level === 'approval_only' && S.me.role !== 'admin';
  
  if (reasonWrap) reasonWrap.style.display = isApproval ? 'block' : 'none';
  if (btnEl) btnEl.textContent = isApproval ? 'Gửi yêu cầu phê duyệt' : 'Tạo Mailbox';
  
  if (previewEl) {
    previewEl.textContent = prefix ? `${prefix}@${domain}` : `(nhập tên)@${domain}`;
  }
}

function toggleCustomMailboxMode() {
  const smartWrap = document.getElementById('mb-smart-wrap');
  const manualWrap = document.getElementById('mb-manual-wrap');
  const toggleBtn = document.getElementById('mb-toggle-mode-btn');
  if (manualWrap.style.display === 'none') {
    manualWrap.style.display = 'block';
    smartWrap.style.display = 'none';
    toggleBtn.textContent = '← Quay lại bộ chọn Domain';
  } else {
    manualWrap.style.display = 'none';
    smartWrap.style.display = 'block';
    toggleBtn.textContent = 'Hoặc nhập địa chỉ email đầy đủ thủ công →';
    updateMailboxPreview();
  }
}

function newMailboxModal() {
  const isAdmin = S.me.role === 'admin';
  const domains = S.domains.length ? S.domains : [{ name: 'example.com', access_level: 'public' }];
  
  modal('Tạo địa chỉ Mailbox mới', `
    <div id="mb-smart-wrap">
      <div class="field">
        <label>Chọn Tên miền (Domain)</label>
        <select id="mb-domain" onchange="updateMailboxPreview()">
          ${domains.map(d => {
            const tag = d.access_level === 'approval_only' ? ' [🔒 Cần duyệt]' : (d.access_level === 'vip' ? ' [⭐ VIP]' : (d.access_level === 'disabled' ? ' [⛔ Khóa]' : ' [✓ Public]'));
            return `<option value="${esc(d.name)}" ${d.is_active === 0 ? 'disabled' : ''}>@${esc(d.name)}${tag}</option>`;
          }).join('')}
        </select>
      </div>
      <div class="field">
        <label>Tên hộp thư (Username / Prefix)</label>
        <div style="display:flex;gap:8px">
          <input id="mb-prefix" placeholder="vd: fast-buyer hoặc shopee01" style="flex:1" oninput="updateMailboxPreview()">
          <button type="button" class="btn secondary" onclick="generateRandomAlias()" title="Sinh tên ngẫu nhiên">🎲 Random</button>
        </div>
      </div>
      <div style="margin:10px 0;padding:10px 14px;background:var(--surface-2);border:1px solid var(--line);border-radius:8px;display:flex;align-items:center;justify-content:space-between">
        <span class="tiny muted">Địa chỉ sẽ tạo:</span>
        <b id="mb-preview" class="mono" style="color:var(--primary);font-size:14px">—</b>
      </div>
      <div id="mb-reason-wrap" style="display:none;margin-top:10px">
        <div class="notice">Tên miền này yêu cầu gửi đơn phê duyệt. Hãy nêu rõ mục đích sử dụng.</div>
        <div class="field"><label>Mục đích sử dụng / Ghi chú</label><input id="mb-request-reason" placeholder="Ví dụ: Nhận OTP đăng ký tài khoản Shopee"></div>
      </div>
    </div>
    <div id="mb-manual-wrap" style="display:none">
      <div class="field">
        <label>Địa chỉ email đầy đủ</label>
        <input id="mb-address-manual" placeholder="abcxyz@example.com">
      </div>
    </div>
    <div style="margin:8px 0 12px">
      <a href="javascript:void(0)" id="mb-toggle-mode-btn" class="tiny muted" onclick="toggleCustomMailboxMode()">Hoặc nhập địa chỉ email đầy đủ thủ công →</a>
    </div>
    ${isAdmin ? `
      <div class="field"><label>Owner User ID (admin có thể để trống)</label>
        <input id="mb-owner" list="user-list" placeholder="U-XXXXXXXXXX"></div>
      <div class="field"><label>Shared access User ID(s)</label>
        <input id="mb-users" placeholder="U-AAAA, U-BBBB"></div>
    ` : ''}
    <div class="stack" style="margin-top:14px">
      <button class="btn primary" id="mb-submit-btn" onclick="executeCreateOrRequestMailbox()">Tạo Mailbox</button>
      <button class="btn secondary" onclick="closeModal()">Hủy</button>
    </div>
  `);
  setTimeout(updateMailboxPreview, 50);
}

async function executeCreateOrRequestMailbox() {
  const isAdmin = S.me.role === 'admin';
  const isManual = document.getElementById('mb-manual-wrap')?.style.display !== 'none';
  let address = '';
  if (isManual) {
    address = (document.getElementById('mb-address-manual')?.value || '').trim();
  } else {
    const prefix = (document.getElementById('mb-prefix')?.value || '').trim();
    const domain = document.getElementById('mb-domain')?.value || '';
    if (!prefix) return toast('Vui lòng nhập tên hộp thư hoặc bấm 🎲 Random.');
    address = `${prefix}@${domain}`;
  }
  
  if (!address.includes('@')) return toast('Địa chỉ email không hợp lệ.');
  
  const domainPart = address.split('@')[1]?.toLowerCase();
  const domainObj = S.domains.find(d => d.name === domainPart);
  const isApproval = domainObj?.access_level === 'approval_only' && !isAdmin;
  
  if (isApproval) {
    const reason = document.getElementById('mb-request-reason')?.value || '';
    try {
      const res = await api('/api/mailbox-requests', {
        method: 'POST',
        body: JSON.stringify({ desired_address: address, reason }),
      });
      toast(res.notice, true);
      closeModal();
      await refreshData();
      goView('requests');
    } catch (e) {
      toast(e.message);
    }
    return;
  }
  
  try {
    const ownerPid = isAdmin ? (document.getElementById('mb-owner')?.value.trim().toUpperCase() || '') : S.me.public_id;
    const targetPids = isAdmin
      ? (document.getElementById('mb-users')?.value || '').split(',').map(x => x.trim().toUpperCase()).filter(Boolean)
      : [];
    const d = await api('/api/mailboxes/create', {
      method: 'POST',
      body: JSON.stringify({
        address,
        owner_public_id: ownerPid,
        target_public_ids: targetPids,
      }),
    });
    toast(d.notice, true);
    closeModal();
    S.view = 'mailboxes';
    await refreshData();
    render();
  } catch (e) {
    toast(e.message);
  }
}

/* ------------------------------------------------------------- system page */

let adminSystem = null;

async function loadSystemStats() {
  try {
    adminSystem = await api('/api/admin/system/stats');
    if (S.view === 'system') render();
  } catch (e) {
    toast(e.message);
  }
}

async function runSystemVacuum() {
  if (!confirm('Chạy VACUUM để tối ưu hóa và giải phóng dung lượng SQLite về cho hệ điều hành?')) return;
  try {
    toast('Đang chạy VACUUM tối ưu database...', true);
    const res = await api('/api/admin/system/vacuum', { method: 'POST' });
    toast(res.notice, true);
    await loadSystemStats();
  } catch (e) {
    toast(e.message);
  }
}

function renderSystemPage() {
  if (S.me.role !== 'admin') return renderNoReader();
  if (!adminSystem) {
    loadSystemStats();
  }
  
  const diskMb = adminSystem ? Math.round(adminSystem.db_size_bytes / (1024 * 1024)) : 0;
  const diskLimitMb = adminSystem?.koyeb_disk_limit_mb || 2000;
  const diskPercent = Math.min(100, Math.round((diskMb / diskLimitMb) * 100));
  const barColor = diskPercent > 85 ? 'red' : (diskPercent > 70 ? 'yellow' : 'green');
  
  return pageShell('Hệ thống, Giám sát & Quản trị Lưu trữ',
    'Theo dõi tài nguyên server Koyeb (512MB RAM, 2000MB Disk), dọn dẹp thư cũ và sao lưu dữ liệu.',
    `<button class="btn secondary" onclick="loadSystemStats();toast('Đã cập nhật chỉ số', true)">Refresh</button>
     <button class="btn primary" onclick="runSystemVacuum()">⚡ Chạy VACUUM</button>`,
    `
    <div class="grid">
      <div class="col-6">
        <div class="card">
          <div class="between">
            <h3 style="margin:0">Ổ đĩa Koyeb (Giới hạn ${diskLimitMb} MB)</h3>
            <span class="badge ${barColor}">${diskPercent}% Sử dụng</span>
          </div>
          <div class="progress-bar-wrap">
            <div class="progress-bar-fill ${barColor}" style="width:${Math.max(diskPercent, 2)}%"></div>
          </div>
          <div class="between tiny muted">
            <span>Dung lượng DB: <b>${formatBytes(adminSystem?.db_size_bytes || 0)}</b></span>
            <span>Hạn mức đĩa: <b>${diskLimitMb} MB</b></span>
          </div>
          <div class="stat-grid" style="margin-top:14px">
            <div class="stat-card">
              <div class="stat-num">${adminSystem ? adminSystem.db_engine : '—'}</div>
              <div class="stat-label">Engine CSDL</div>
            </div>
            <div class="stat-card">
              <div class="stat-num">${adminSystem ? formatBytes(adminSystem.total_att_bytes) : '—'}</div>
              <div class="stat-label">File đính kèm (${adminSystem ? adminSystem.total_attachments : 0})</div>
            </div>
          </div>
        </div>
      </div>
      
      <div class="col-6">
        <div class="card">
          <h3 style="margin:0">Chỉ số Dữ liệu & Người dùng</h3>
          <div class="stat-grid" style="margin-top:12px">
            <div class="stat-card">
              <div class="stat-num">${adminSystem ? adminSystem.total_messages : '—'}</div>
              <div class="stat-label">Tổng thư đã nhận</div>
            </div>
            <div class="stat-card">
              <div class="stat-num" style="color:var(--warning)">${adminSystem ? adminSystem.old_messages : '—'}</div>
              <div class="stat-label">Thư cũ quá hạn</div>
            </div>
            <div class="stat-card">
              <div class="stat-num">${adminSystem ? adminSystem.total_mailboxes : '—'}</div>
              <div class="stat-label">Tổng Mailbox</div>
            </div>
            <div class="stat-card">
              <div class="stat-num">${adminSystem ? adminSystem.total_users : '—'}</div>
              <div class="stat-label">Người dùng</div>
            </div>
          </div>
        </div>
      </div>
    </div>
    
    <div style="height:12px"></div>
    <div class="card">
      <div class="between" style="margin-bottom:8px">
        <h3 style="margin:0">Tự động dọn dẹp thư cũ (Retention Policy)</h3>
        <button class="btn secondary small" onclick="runManualCleanup()">Dọn dẹp thư cũ ngay</button>
      </div>
      <div class="hint" style="margin-bottom:12px">
        Tự động xoá thư quá hạn định kỳ và giải phóng dung lượng đĩa cho Koyeb.
      </div>
      <div class="grid">
        <div class="col-6">
          <div class="toggle-row">
            <div>
              <div class="label">Bật tự động xoá thư cũ</div>
              <div class="hint">Quét dọn thư quá hạn định kỳ mỗi 1 giờ.</div>
            </div>
            <label class="switch">
              <input type="checkbox" ${adminSystem?.auto_delete_enabled ? 'checked' : ''} onchange="setRetentionSetting('auto_delete_enabled', this.checked);if(adminSystem)adminSystem.auto_delete_enabled=this.checked">
              <span class="track"></span>
            </label>
          </div>
        </div>
        <div class="col-6">
          <div class="field" style="margin:0">
            <label>Thời gian lưu trữ tối đa (ngày)</label>
            <div style="display:flex;gap:8px">
              <input id="retention-days-input" type="number" min="1" max="365" value="${adminSystem?.auto_delete_days || 7}" style="max-width:140px">
              <button class="btn primary small" onclick="setRetentionSetting('auto_delete_days', Number(document.getElementById('retention-days-input').value))">Lưu ngày</button>
            </div>
          </div>
        </div>
      </div>
    </div>
    
    <div style="height:12px"></div>
    <div class="card">
      <h3 style="margin-top:0">Sao lưu & Xuất dữ liệu (Data & Backup Hub)</h3>
      <div class="hint" style="margin-bottom:12px">
        Xuất dữ liệu an toàn để lưu trữ dự phòng, chuyển máy chủ hoặc phục vụ báo cáo.
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:10px">
        <a class="btn primary" href="/api/admin/backup/sqlite" download>📦 Tải file SQLite (.db)</a>
        <a class="btn secondary" href="/api/admin/backup/json" download>📄 Xuất toàn bộ Cấu hình (JSON)</a>
        <a class="btn secondary" href="/api/export/mailboxes.csv" download>📊 Xuất danh sách Mailbox (CSV)</a>
        <a class="btn secondary" href="/api/export/otps.csv" download>🔑 Xuất lịch sử OTP (CSV)</a>
      </div>
    </div>
  `);
}

/* ------------------------------------------------------------ domains page */

async function loadDomains() {
  try {
    const res = await api('/api/domains');
    S.domains = res.items || [];
    if (S.view === 'domains') render();
  } catch (e) {
    toast(e.message);
  }
}

function renderDomainsPage() {
  if (S.me.role !== 'admin') return renderNoReader();
  return pageShell('Quản lý Tên miền (Domains & Access Policy)',
    'Cấu hình các tên miền được phép tiếp nhận email, phân cấp Public, VIP và Yêu cầu phê duyệt.',
    `<button class="btn primary" onclick="newDomainModal()">＋ Thêm Tên miền</button>`,
    `<div class="card" style="padding:0">
      <div class="table-wrap" style="border:0">
        <table class="table">
          <thead>
            <tr>
              <th>Tên miền</th>
              <th>Cấp độ truy cập</th>
              <th>Trạng thái</th>
              <th>Số Mailbox</th>
              <th>Mô tả</th>
              <th>Thao tác</th>
            </tr>
          </thead>
          <tbody>
            ${S.domains.map(d => {
              const accessBadge = d.access_level === 'public' ? '<span class="badge green">PUBLIC</span>' :
                (d.access_level === 'vip' ? '<span class="badge blue">VIP ONLY</span>' :
                (d.access_level === 'approval_only' ? '<span class="badge yellow">CẦN DUYỆT</span>' : '<span class="badge red">KHÓA</span>'));
              return `<tr>
                <td><b>@${esc(d.name)}</b></td>
                <td>${accessBadge}</td>
                <td>${d.is_active ? '<span class="status-dot"></span> Đang bật' : '<span class="status-dot off"></span> Tạm dừng'}</td>
                <td><b>${d.mailbox_count || 0}</b></td>
                <td class="tiny muted">${esc(d.description || '—')}</td>
                <td>
                  <div class="actions">
                    <button class="btn secondary small" onclick="editDomainModal(${d.id})">Sửa</button>
                    <button class="btn secondary small" onclick="toggleDomainActive(${d.id}, ${d.is_active ? 'false' : 'true'})">${d.is_active ? 'Tắt' : 'Bật'}</button>
                    <button class="btn danger small" onclick="deleteDomain(${d.id})">Xóa</button>
                  </div>
                </td>
              </tr>`;
            }).join('') || '<tr><td colspan="6"><div class="empty">Chưa có tên miền nào.</div></td></tr>'}
          </tbody>
        </table>
      </div>
    </div>`);
}

function newDomainModal() {
  modal('Thêm Tên miền mới', `
    <div class="field">
      <label>Tên miền (ví dụ: mydomain.com)</label>
      <input id="dom-name" placeholder="mydomain.com">
    </div>
    <div class="field">
      <label>Cấp độ truy cập (Access Policy)</label>
      <select id="dom-access">
        <option value="public">Public — Mọi người dùng đều được tạo tự do</option>
        <option value="vip">VIP Only — Chỉ tài khoản VIP/Admin</option>
        <option value="approval_only">Approval Only — Bắt buộc gửi đơn xin duyệt</option>
        <option value="disabled">Disabled — Tạm dừng tạo mới</option>
      </select>
    </div>
    <div class="field">
      <label>Ghi chú / Mô tả</label>
      <input id="dom-desc" placeholder="Domain chính cho hệ thống">
    </div>
    <button class="btn primary" onclick="executeCreateDomain()">Thêm Tên miền</button>
  `);
}

async function executeCreateDomain() {
  const name = document.getElementById('dom-name')?.value.trim().toLowerCase();
  const access_level = document.getElementById('dom-access')?.value;
  const description = document.getElementById('dom-desc')?.value.trim();
  if (!name) return toast('Vui lòng nhập tên miền');
  try {
    await api('/api/admin/domains', {
      method: 'POST',
      body: JSON.stringify({ name, access_level, description }),
    });
    toast('Đã thêm tên miền mới!', true);
    closeModal();
    await loadDomains();
    await refreshData();
  } catch (e) {
    toast(e.message);
  }
}

function editDomainModal(id) {
  const d = S.domains.find(x => x.id === id);
  if (!d) return;
  modal(`Sửa cấu hình @${esc(d.name)}`, `
    <div class="field">
      <label>Cấp độ truy cập (Access Policy)</label>
      <select id="dom-edit-access">
        <option value="public" ${d.access_level === 'public' ? 'selected' : ''}>Public — Mọi người dùng đều được tạo tự do</option>
        <option value="vip" ${d.access_level === 'vip' ? 'selected' : ''}>VIP Only — Chỉ tài khoản VIP/Admin</option>
        <option value="approval_only" ${d.access_level === 'approval_only' ? 'selected' : ''}>Approval Only — Bắt buộc gửi đơn xin duyệt</option>
        <option value="disabled" ${d.access_level === 'disabled' ? 'selected' : ''}>Disabled — Tạm dừng tạo mới</option>
      </select>
    </div>
    <div class="field">
      <label>Ghi chú / Mô tả</label>
      <input id="dom-edit-desc" value="${esc(d.description || '')}">
    </div>
    <button class="btn primary" onclick="executeEditDomain(${d.id})">Lưu thay đổi</button>
  `);
}

async function executeEditDomain(id) {
  const access_level = document.getElementById('dom-edit-access')?.value;
  const description = document.getElementById('dom-edit-desc')?.value.trim();
  try {
    await api(`/api/admin/domains/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ access_level, description }),
    });
    toast('Đã cập nhật tên miền!', true);
    closeModal();
    await loadDomains();
    await refreshData();
  } catch (e) {
    toast(e.message);
  }
}

async function toggleDomainActive(id, active) {
  try {
    await api(`/api/admin/domains/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ is_active: active }),
    });
    toast(active ? 'Đã bật tên miền' : 'Đã tạm dừng tên miền', true);
    await loadDomains();
    await refreshData();
  } catch (e) {
    toast(e.message);
  }
}

async function deleteDomain(id) {
  if (!confirm('Bạn có chắc muốn xoá tên miền này?')) return;
  try {
    await api(`/api/admin/domains/${id}`, { method: 'DELETE' });
    toast('Đã xoá tên miền!', true);
    await loadDomains();
    await refreshData();
  } catch (e) {
    toast(e.message);
  }
}

/* ----------------------------------------------------------- requests page */

async function loadMailboxRequests() {
  try {
    const res = await api(`/api/mailbox-requests${S.requestStatusFilter ? `?status=${S.requestStatusFilter}` : ''}`);
    S.mailboxRequests = res.items || [];
    if (S.view === 'requests') render();
  } catch (e) {
    toast(e.message);
  }
}

function filterRequests(status) {
  S.requestStatusFilter = status;
  loadMailboxRequests();
}

function renderRequestsPage() {
  const isAdmin = S.me.role === 'admin';
  return pageShell('Yêu cầu cấp Mailbox & Phê duyệt',
    isAdmin ? 'Quản lý và phê duyệt các đơn xin cấp địa chỉ email đặc biệt từ thành viên.' : 'Theo dõi trạng thái các đơn xin cấp địa chỉ email của bạn.',
    `<button class="btn primary" onclick="newMailboxModal()">＋ Gửi yêu cầu mới</button>
     <button class="btn secondary" onclick="loadMailboxRequests();toast('Đã cập nhật', true)">Refresh</button>`,
    `
    <div class="sub-tabs">
      <button class="sub-tab-btn ${S.requestStatusFilter === '' ? 'active' : ''}" onclick="filterRequests('')">Tất cả (${S.mailboxRequests.length})</button>
      <button class="sub-tab-btn ${S.requestStatusFilter === 'pending' ? 'active' : ''}" onclick="filterRequests('pending')">Đang chờ (${S.mailboxRequests.filter(x => x.status === 'pending').length})</button>
      <button class="sub-tab-btn ${S.requestStatusFilter === 'approved' ? 'active' : ''}" onclick="filterRequests('approved')">Đã duyệt (${S.mailboxRequests.filter(x => x.status === 'approved').length})</button>
      <button class="sub-tab-btn ${S.requestStatusFilter === 'rejected' ? 'active' : ''}" onclick="filterRequests('rejected')">Từ chối (${S.mailboxRequests.filter(x => x.status === 'rejected').length})</button>
    </div>
    <div class="card" style="padding:0">
      <div class="table-wrap" style="border:0">
        <table class="table">
          <thead>
            <tr>
              <th>Thời gian</th>
              ${isAdmin ? '<th>Người xin cấp</th>' : ''}
              <th>Địa chỉ mong muốn</th>
              <th>Mục đích / Ghi chú</th>
              <th>Trạng thái</th>
              <th>Phản hồi Admin</th>
              ${isAdmin ? '<th>Thao tác</th>' : ''}
            </tr>
          </thead>
          <tbody>
            ${S.mailboxRequests.map(r => {
              const statusBadge = r.status === 'pending' ? '<span class="badge yellow">CHỜ DUYỆT</span>' :
                (r.status === 'approved' ? '<span class="badge green">ĐÃ DUYỆT</span>' : '<span class="badge red">TỪ CHỐI</span>');
              return `<tr>
                <td class="tiny">${fmt(r.created_at)}</td>
                ${isAdmin ? `<td><b>${esc(r.username)}</b> <span class="tiny muted">(${esc(r.public_id)})</span></td>` : ''}
                <td><b class="mono">${esc(r.desired_address)}</b></td>
                <td>${esc(r.reason || '—')}</td>
                <td>${statusBadge}</td>
                <td class="tiny muted">${esc(r.admin_note || (r.reviewer_username ? `Duyệt bởi @${r.reviewer_username}` : '—'))}</td>
                ${isAdmin ? `<td>
                  ${r.status === 'pending' ? `
                    <div class="actions">
                      <button class="btn success small" onclick="approveRequest(${r.id})">✓ Duyệt</button>
                      <button class="btn danger small" onclick="rejectRequestModal(${r.id})">✕ Từ chối</button>
                    </div>
                  ` : '—'}
                </td>` : ''}
              </tr>`;
            }).join('') || '<tr><td colspan="7"><div class="empty">Chưa có yêu cầu nào.</div></td></tr>'}
          </tbody>
        </table>
      </div>
    </div>`);
}

async function approveRequest(id) {
  if (!confirm('Duyệt và khởi tạo mailbox này cho thành viên?')) return;
  try {
    const res = await api(`/api/admin/mailbox-requests/${id}/approve`, { method: 'POST' });
    toast(res.notice, true);
    await loadMailboxRequests();
    await refreshData();
  } catch (e) {
    toast(e.message);
  }
}

function rejectRequestModal(id) {
  modal('Từ chối yêu cầu', `
    <div class="field">
      <label>Lý do từ chối (sẽ gửi thông báo cho thành viên)</label>
      <input id="reject-note" placeholder="Ví dụ: Tên miền này tạm thời không nhận tạo mới">
    </div>
    <div class="stack">
      <button class="btn danger" onclick="executeRejectRequest(${id})">Xác nhận Từ chối</button>
      <button class="btn secondary" onclick="closeModal()">Đóng</button>
    </div>
  `);
}

async function executeRejectRequest(id) {
  const admin_note = document.getElementById('reject-note')?.value.trim();
  try {
    const res = await api(`/api/admin/mailbox-requests/${id}/reject`, {
      method: 'POST',
      body: JSON.stringify({ admin_note }),
    });
    toast(res.notice, true);
    closeModal();
    await loadMailboxRequests();
    await refreshData();
  } catch (e) {
    toast(e.message);
  }
}


function bulkCreateOwnModal() {
  modal('Tạo nhiều địa chỉ',
    `<div class="notice">Mỗi dòng một email. Các địa chỉ được tạo thành exact mailbox và thuộc quyền của bạn (tối đa 5.000 địa chỉ/lần).</div>
    <div class="field"><label>Danh sách địa chỉ</label>
      <textarea id="bulk-own-addresses" placeholder="a@example.com&#10;b@example.com&#10;c@example.com"></textarea></div>
    <button class="btn primary" onclick="executeBulkCreateOwn()">Tạo mailbox</button>`);
}

async function executeBulkCreateOwn() {
  try {
    const d = await api('/api/mailboxes/create-bulk', {
      method: 'POST', body: JSON.stringify({ addresses: document.getElementById('bulk-own-addresses').value }),
    });
    toast(d.notice, true);
    closeModal();
    S.view = 'mailboxes';
    await refreshData();
    render();
  } catch (e) {
    toast(e.message);
  }
}

function bulkImportModal(prefillPid = '') {
  if (S.me.role !== 'admin') return toast('Chỉ admin.');
  modal('Admin — Import / gán danh sách email',
    `<div class="notice"><b>Bulk import tạo Exact override.</b> Một địa chỉ đã import sẽ không còn nhận prefix/regex cho tới khi “Đưa về Rules”.</div>
    <div class="field"><label>Danh sách email</label>
      <textarea id="import-addresses" placeholder="abc001@example.com&#10;abc002@example.com"></textarea>
      <div class="hint">Chấp nhận xuống dòng, dấu phẩy hoặc dấu chấm phân biệt. Tối đa 5.000 địa chỉ/lần.</div></div>
    <div class="field"><label>Target User ID(s)</label>
      <input id="import-targets" list="user-list" value="${esc(prefillPid)}" placeholder="U-AAAA, U-BBBB"></div>
    <button class="btn primary" onclick="executeBulkImport()">Import và gán Exact</button>`);
}

async function executeBulkImport() {
  try {
    const addresses = document.getElementById('import-addresses').value;
    const targets = document.getElementById('import-targets').value.split(',')
      .map(x => x.trim().toUpperCase()).filter(Boolean);
    if (!targets.length) return toast('Cần ít nhất 1 User ID');
    const d = await api('/api/admin/import-mailboxes', {
      method: 'POST', body: JSON.stringify({ addresses, target_public_ids: targets }),
    });
    toast(d.notice, true);
    closeModal();
    S.checkedMailboxes.clear();
    S.view = 'mailboxes';
    await refreshData();
    render();
  } catch (e) {
    toast(e.message);
  }
}

function mailboxDetailModal(id) {
  const m = S.mailboxes.find(x => x.id === id);
  if (!m) return toast('Mailbox không tồn tại');
  const canManage = S.me.role === 'admin' || m.is_owner;
  const access = m.users || [];
  modal(`Mailbox — ${m.address}`,
    `<div class="grid">
      <div class="col-6"><div class="card">
        <h3 style="margin-top:0">Thông tin địa chỉ</h3>
        <div class="field"><label>Address</label><input class="mono" disabled value="${esc(m.address)}"></div>
        <div class="field"><label>Routing mode</label><input disabled value="${m.exact_override ? 'EXACT OVERRIDE' : 'PREFIX / REGEX'}"></div>
        <div class="field"><label>Owner</label><input disabled value="${esc(m.owner_username || 'Chưa có owner')}"></div>
        <div class="hint">${m.exact_override
          ? 'Exact override đang chặn toàn bộ routing rule cho địa chỉ này.'
          : 'Địa chỉ này tự động lấy quyền từ các prefix/regex đang khớp.'}</div>
      </div></div>
      <div class="col-6"><div class="card">
        <h3 style="margin-top:0">Thống kê</h3>
        <div class="stat-grid">
          <div class="stat-card"><div class="stat-num">${m.message_count}</div><div class="stat-label">Email</div></div>
          <div class="stat-card"><div class="stat-num">${m.unread_count}</div><div class="stat-label">Unread</div></div>
          <div class="stat-card"><div class="stat-num">${access.length}</div><div class="stat-label">User access</div></div>
          <div class="stat-card"><div class="stat-num">${m.exact_override ? '1' : '0'}</div><div class="stat-label">Exact</div></div>
        </div>
      </div></div>
      <div class="col-12"><div class="card">
        <div class="between">
          <div><b>Access list</b><div class="hint">Source cho biết quyền đến từ Manual hay Routing rule.</div></div>
          ${canManage ? `<button class="btn primary small" onclick="closeModal();assignOneModal(${id})">＋ Gán User</button>` : ''}
        </div>
        <div style="margin-top:8px">${access.map(u => `<div class="attachment">
          <div style="flex:1;min-width:0">
            <div class="between">
              <div><b>${esc(u.username)}</b> ${m.owner_user_id && u.public_id === S.users.find(x => x.id === m.owner_user_id)?.public_id ? '<span class="badge green">OWNER</span>' : ''}</div>
              <span class="badge ${u.source === 'rule' ? 'green' : ''}">${u.source === 'rule' ? 'RULE' : 'MANUAL'}</span>
            </div>
            <div class="tiny muted">${esc(u.public_id)}${u.rule_id ? ' · Rule #' + u.rule_id : ''}</div>
          </div>
          ${canManage && u.source === 'manual' && m.owner_user_id !== S.users.find(x => x.public_id === u.public_id)?.id
            ? `<button class="btn danger small" onclick="revokeOne(${id},'${esc(u.public_id)}')">Thu hồi</button>` : ''}
        </div>`).join('') || '<div class="empty">Chưa có user access.</div>'}</div>
      </div></div>
    </div>
    ${canManage ? `<div class="stack wrap" style="margin-top:10px">
      <button class="btn success" onclick="closeModal();oneAccessModal(${id},'share')">Shared</button>
      <button class="btn danger" onclick="closeModal();oneAccessModal(${id},'transfer')">Transfer</button>
      ${m.exact_override ? `<button class="btn secondary" onclick="returnOneToRules(${id})">Đưa về Rules</button>` : ''}
    </div>` : ''}`);
}

function oneAccessModal(id, mode) {
  const m = S.mailboxes.find(x => x.id === id);
  modal(`${mode === 'transfer' ? 'Transfer' : 'Shared'} — ${m?.address || ''}`,
    `<div class="notice ${mode === 'transfer' ? 'error' : 'ok'}">${mode === 'transfer'
      ? 'Transfer sẽ đổi Owner và revoke toàn bộ access cũ.'
      : 'Shared giữ Owner hiện tại và cấp thêm access.'}</div>
    <div class="field"><label>User ID đích</label><input id="target-user" list="user-list" placeholder="U-XXXXXXXXXX" onblur="lookupTarget()"></div>
    <div id="target-preview" class="hint"></div>
    <button class="btn ${mode === 'transfer' ? 'danger' : 'success'}" onclick="executeOneAccess(${id},'${mode}')">Xác nhận ${mode}</button>`);
}

async function lookupTarget() {
  const el = document.getElementById('target-user');
  const box = document.getElementById('target-preview');
  if (!el || !box) return;
  const v = el.value.trim().toUpperCase();
  if (!v) { box.textContent = ''; return; }
  try {
    const d = await api('/api/users/lookup?public_id=' + encodeURIComponent(v));
    box.innerHTML = `<span class="badge green">${esc(d.username)}</span> ${esc(d.public_id)}`;
  } catch {
    box.innerHTML = `<span class="badge red">Không tìm thấy User ID</span>`;
  }
}

async function executeOneAccess(id, mode) {
  try {
    const target = document.getElementById('target-user').value.trim().toUpperCase();
    const d = await api(`/api/mailboxes/${id}/${mode}`, {
      method: 'POST', body: JSON.stringify({ target_public_id: target }),
    });
    toast(d.notice, true);
    closeModal();
    await refreshData();
    S.view = 'mailboxes';
    render();
  } catch (e) {
    toast(e.message);
  }
}

async function revokeOne(id, pid) {
  try {
    const d = await api(`/api/mailboxes/${id}/revoke`, {
      method: 'POST', body: JSON.stringify({ target_public_id: pid }),
    });
    toast(d.notice, true);
    closeModal();
    await refreshData();
    render();
  } catch (e) {
    toast(e.message);
  }
}

async function returnOneToRules(id) {
  try {
    const d = await api(`/api/mailboxes/${id}/return-to-rules`, { method: 'POST' });
    toast(d.notice, true);
    closeModal();
    await refreshData();
    render();
  } catch (e) {
    toast(e.message);
  }
}

function assignOneModal(id) {
  modal('Admin — gán User', `<div class="field"><label>User ID(s)</label><input id="assign-userids" placeholder="U-AAAA, U-BBBB"></div>
    <button class="btn primary" onclick="executeAssignOne(${id})">Gán exact access</button>`);
}

async function executeAssignOne(id) {
  try {
    const target = document.getElementById('assign-userids').value.split(',')
      .map(x => x.trim().toUpperCase()).filter(Boolean);
    const d = await api('/api/admin/assign', {
      method: 'POST', body: JSON.stringify({ mailbox_ids: [id], target_public_ids: target }),
    });
    toast(d.notice, true);
    closeModal();
    await refreshData();
    render();
  } catch (e) {
    toast(e.message);
  }
}

function bulkAccessModal(mode) {
  const ids = selectedMailboxIds();
  if (!ids.length) return toast('Hãy tick ít nhất 1 mailbox.');
  modal(`${mode === 'transfer' ? 'Bulk Transfer' : 'Bulk Shared'}`,
    `<div class="notice ${mode === 'transfer' ? 'error' : 'ok'}"><b>${ids.length} mailbox</b> được chọn.<br>${mode === 'transfer'
      ? 'Transfer sẽ đổi Owner và thu hồi toàn bộ access cũ trên từng mailbox.'
      : 'Shared sẽ giữ Owner và cấp thêm access.'}</div>
    <div class="field"><label>Target User ID</label><input id="bulk-target" list="user-list" placeholder="U-XXXXXXXXXX" onblur="lookupBulkTarget()"></div>
    <div id="bulk-preview" class="hint"></div>
    <button class="btn ${mode === 'transfer' ? 'danger' : 'success'}" onclick="executeBulkAccess('${mode}')">Xác nhận ${mode} ${ids.length} mailbox</button>`);
}

async function lookupBulkTarget() {
  const v = document.getElementById('bulk-target')?.value.trim().toUpperCase();
  const b = document.getElementById('bulk-preview');
  if (!v || !b) return;
  try {
    const d = await api('/api/users/lookup?public_id=' + encodeURIComponent(v));
    b.innerHTML = `<span class="badge green">${esc(d.username)}</span> ${esc(d.public_id)}`;
  } catch {
    b.innerHTML = '<span class="badge red">Không tìm thấy User ID</span>';
  }
}

async function executeBulkAccess(mode) {
  const ids = selectedMailboxIds();
  const target = document.getElementById('bulk-target').value.trim().toUpperCase();
  try {
    const d = await api(`/api/mailboxes/bulk-${mode}`, {
      method: 'POST', body: JSON.stringify({ mailbox_ids: ids, target_public_id: target }),
    });
    toast(d.notice, true);
    closeModal();
    S.checkedMailboxes.clear();
    await refreshData();
    render();
  } catch (e) {
    toast(e.message);
  }
}

function bulkAssignModal() {
  if (S.me.role !== 'admin') return toast('Chỉ admin.');
  const ids = selectedMailboxIds();
  if (!ids.length) return toast('Hãy tick mailbox.');
  modal(`Admin — gán ${ids.length} mailbox`,
    `<div class="notice">Gán User từ Admin tạo <b>Exact override</b>, vì exact routing phải ưu tiên regex/prefix.</div>
    <div class="field"><label>User ID(s)</label><input id="bulk-assign-users" placeholder="U-AAAA, U-BBBB"></div>
    <button class="btn primary" onclick="executeBulkAssign()">Gán access</button>`);
}

async function executeBulkAssign() {
  try {
    const ids = selectedMailboxIds();
    const targets = document.getElementById('bulk-assign-users').value.split(',')
      .map(x => x.trim().toUpperCase()).filter(Boolean);
    const d = await api('/api/admin/assign', {
      method: 'POST', body: JSON.stringify({ mailbox_ids: ids, target_public_ids: targets }),
    });
    toast(d.notice, true);
    closeModal();
    S.checkedMailboxes.clear();
    await refreshData();
    render();
  } catch (e) {
    toast(e.message);
  }
}

function bulkRevokeModal() {
  const ids = selectedMailboxIds();
  if (!ids.length) return toast('Hãy tick mailbox.');
  modal(`Thu hồi access — ${ids.length} mailbox`,
    `<div class="field"><label>User ID cần thu hồi</label><input id="bulk-revoke-user" list="user-list" placeholder="U-XXXXXXXXXX"></div>
    <button class="btn danger" onclick="executeBulkRevoke()">Thu hồi</button>`);
}

async function executeBulkRevoke() {
  try {
    const d = await api('/api/mailboxes/bulk-revoke', {
      method: 'POST',
      body: JSON.stringify({
        mailbox_ids: selectedMailboxIds(),
        target_public_id: document.getElementById('bulk-revoke-user').value.trim().toUpperCase(),
      }),
    });
    toast(d.notice, true);
    closeModal();
    S.checkedMailboxes.clear();
    await refreshData();
    render();
  } catch (e) {
    toast(e.message);
  }
}

async function bulkReturnToRules() {
  if (!S.checkedMailboxes.size) return toast('Hãy tick mailbox.');
  if (!confirm('Đưa tất cả mailbox đã chọn về routing rules? Exact access sẽ được xoá và quyền sẽ tính lại từ routing rules.')) return;
  try {
    const d = await api('/api/mailboxes/bulk-return-to-rules', {
      method: 'POST', body: JSON.stringify({ mailbox_ids: selectedMailboxIds() }),
    });
    toast(d.notice, true);
    S.checkedMailboxes.clear();
    await refreshData();
    render();
  } catch (e) {
    toast(e.message);
  }
}

/* ========================================================================
   Admin — routing rules
   ===================================================================== */

function getRuleHint(t) {
  if (t === 'catch_all') return 'Catch-all gom toàn bộ email chưa khớp rule khác (* hoặc *@domain.com) tự động cấp cho User này.';
  if (t === 'domain') return 'Domain khớp toàn bộ email gửi đến tên miền này (ví dụ: example.com hoặc @example.com).';
  if (t === 'prefix') return 'Prefix khớp ở đầu toàn bộ địa chỉ (ví dụ: otp- hoặc test-).';
  return 'Regex biểu thức chính quy chạy trên toàn bộ email (ví dụ: ^user-[0-9]+@example\\.com$).';
}

function getRulePlaceholder(t) {
  if (t === 'catch_all') return '* hoặc *@domain.com';
  if (t === 'domain') return 'example.com hoặc @example.com';
  if (t === 'prefix') return 'otp- hoặc team-';
  return '^vip-[a-z0-9]+@domain\\.com$';
}

function updateRuleTypeUI() {
  const type = document.getElementById('rule-type')?.value || 'catch_all';
  const hintEl = document.getElementById('rule-hint');
  const patEl = document.getElementById('rule-pattern');
  if (hintEl) hintEl.textContent = getRuleHint(type);
  if (patEl) patEl.placeholder = getRulePlaceholder(type);
}

function setRulePreset(val) {
  const patEl = document.getElementById('rule-pattern');
  if (patEl) {
    patEl.value = val;
    patEl.focus();
  }
}

function newRuleModal(existing = null, targetPid = '') {
  const r = existing || { rule_type: 'catch_all', pattern: '*', target_public_id: targetPid || '', description: '', is_active: true };
  modal(existing ? 'Sửa routing rule' : 'Tạo routing rule',
    `<div class="field"><label>Loại quy tắc</label>
      <select id="rule-type" onchange="updateRuleTypeUI()">
        <option value="catch_all" ${r.rule_type === 'catch_all' ? 'selected' : ''}>Catch-All (Tất cả email chưa gán)</option>
        <option value="domain" ${r.rule_type === 'domain' ? 'selected' : ''}>Domain (Theo tên miền)</option>
        <option value="prefix" ${r.rule_type === 'prefix' ? 'selected' : ''}>Prefix (Tiền tố)</option>
        <option value="regex" ${r.rule_type === 'regex' ? 'selected' : ''}>Regex (Biểu thức chính quy)</option>
      </select>
    </div>
    <div class="field"><label>Pattern (Mẫu khớp)</label>
      <input id="rule-pattern" class="mono" value="${esc(r.pattern)}" placeholder="${esc(getRulePlaceholder(r.rule_type))}">
      <div id="rule-hint" class="hint" style="margin-top:4px">${esc(getRuleHint(r.rule_type))}</div>
      <div style="display:flex;align-items:center;gap:6px;margin-top:8px;flex-wrap:wrap">
        <span class="tiny muted">Gợi ý nhanh:</span>
        <button type="button" class="btn ghost small" style="padding:2px 8px;font-size:11.5px" onclick="document.getElementById('rule-type').value='catch_all';updateRuleTypeUI();setRulePreset('*')">* (Tất cả)</button>
        <button type="button" class="btn ghost small" style="padding:2px 8px;font-size:11.5px" onclick="document.getElementById('rule-type').value='catch_all';updateRuleTypeUI();setRulePreset('*@mydomain.com')">*@domain</button>
        <button type="button" class="btn ghost small" style="padding:2px 8px;font-size:11.5px" onclick="document.getElementById('rule-type').value='domain';updateRuleTypeUI();setRulePreset('@mydomain.com')">@domain</button>
        <button type="button" class="btn ghost small" style="padding:2px 8px;font-size:11.5px" onclick="document.getElementById('rule-type').value='prefix';updateRuleTypeUI();setRulePreset('otp-')">otp-</button>
      </div>
    </div>
    <div class="field"><label>User ID nhận mailbox</label>
      <input id="rule-target" list="user-list" value="${esc(r.target_public_id || '')}" placeholder="U-XXXXXXXXXX">
      <div class="hint">User ID của thành viên sẽ được tự động cấp mailbox khi khớp rule này.</div>
    </div>
    <div class="field"><label>Mô tả quy tắc</label><textarea id="rule-desc" placeholder="Ví dụ: Phân luồng catch-all cho trưởng nhóm hoặc bộ phận OTP">${esc(r.description || '')}</textarea></div>
    <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:16px">
      <button class="btn secondary" onclick="closeModal()">Hủy</button>
      <button class="btn primary" onclick="${existing ? `saveRule(${existing.id})` : 'createRule()'}">${existing ? 'Lưu thay đổi' : 'Tạo rule'}</button>
    </div>`);
}

function rulePayload() {
  return {
    rule_type: document.getElementById('rule-type').value,
    pattern: document.getElementById('rule-pattern').value,
    target_public_id: document.getElementById('rule-target').value.trim().toUpperCase(),
    description: document.getElementById('rule-desc').value,
  };
}

async function createRule() {
  try {
    const d = await api('/api/admin/rules', { method: 'POST', body: JSON.stringify(rulePayload()) });
    toast(d.notice, true);
    closeModal();
    await refreshData();
    S.view = 'rules';
    render();
  } catch (e) {
    toast(e.message);
  }
}

function editRuleModal(id) {
  const r = S.rules.find(x => x.id === id);
  if (r) newRuleModal(r);
}

async function saveRule(id) {
  try {
    const d = await api(`/api/admin/rules/${id}`, { method: 'PATCH', body: JSON.stringify(rulePayload()) });
    toast(d.notice, true);
    closeModal();
    await refreshData();
    S.view = 'rules';
    render();
  } catch (e) {
    toast(e.message);
  }
}

async function toggleRule(id, next) {
  try {
    const d = await api(`/api/admin/rules/${id}/status`, { method: 'POST', body: JSON.stringify({ is_active: next }) });
    toast(d.notice, true);
    await refreshData();
    S.view = 'rules';
    render();
  } catch (e) {
    toast(e.message);
  }
}

async function deleteRule(id) {
  if (!confirm('Xóa routing rule này? Access rule-derived sẽ được tính lại.')) return;
  try {
    const d = await api(`/api/admin/rules/${id}`, { method: 'DELETE' });
    toast(d.notice, true);
    await refreshData();
    S.view = 'rules';
    render();
  } catch (e) {
    toast(e.message);
  }
}

function ruleTestModal() {
  modal('Test routing rule',
    `<div class="field"><label>Email cần test</label><input id="test-email" placeholder="abcxyz@example.com"></div>
    <button class="btn primary" onclick="testRules()">Test</button>
    <div id="test-result" style="margin-top:10px"></div>`);
}

async function testRules() {
  try {
    const e = document.getElementById('test-email').value.trim();
    const d = await api('/api/admin/rules/test?address=' + encodeURIComponent(e));
    document.getElementById('test-result').innerHTML =
      `<div class="notice ok"><b>${esc(e)}</b><br>Khớp ${d.matches.length} rule.
        <div style="margin-top:7px">${d.matches.map(r => `<div class="attachment"><div>
          <b>${esc(r.target_username)}</b> <span class="badge">${esc(r.rule_type)}</span>
          <div class="tiny mono">${esc(r.pattern)}</div></div></div>`).join('')
        || 'Không có rule nào. Exact override nếu mailbox tồn tại sẽ được kiểm tra riêng.'}</div>
      </div>`;
  } catch (e) {
    toast(e.message);
  }
}

/* ========================================================================
   Boot
   ===================================================================== */

applyTheme();
init();
