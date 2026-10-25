/* ============================================================
   i18n.js — OTP Inbox internationalisation runtime
   ============================================================
   Supported locales : en  vi  zh  ru
   Storage key       : localStorage 'otp_locale'
   Events            : document dispatches 'otp:locale' on change
   Globals           : t, setLocale, getLocale, localizeHtml,
                       localizeText, SUPPORTED_LOCALES, LOCALES,
                       LOCALE_META
   ============================================================ */
(function () {
  'use strict';

  const SUPPORTED_LOCALES = ['en', 'vi', 'zh', 'ru'];
  const FALLBACK = 'en';

  const LOCALE_META = {
    en: { label: 'English', short: 'EN' },
    vi: { label: 'Tiếng Việt', short: 'VI' },
    zh: { label: '中文', short: '中文' },
    ru: { label: 'Русский', short: 'RU' },
  };

  function detectLocale() {
    try {
      const stored = localStorage.getItem('otp_locale');
      if (stored && SUPPORTED_LOCALES.includes(stored)) return stored;
    } catch (_) { /* private browsing */ }

    const langs =
      (navigator.languages && navigator.languages.length)
        ? Array.from(navigator.languages)
        : [navigator.language || navigator.userLanguage || 'en'];

    for (const lang of langs) {
      const lower = String(lang || '').toLowerCase();
      if (lower.startsWith('zh')) return 'zh';
      const base = lower.split('-')[0];
      if (SUPPORTED_LOCALES.includes(base)) return base;
    }
    return FALLBACK;
  }

  let currentLocale = detectLocale();
  document.documentElement.lang = currentLocale;

  function setLocale(code) {
    if (!SUPPORTED_LOCALES.includes(code)) code = FALLBACK;
    currentLocale = code;
    try { localStorage.setItem('otp_locale', code); } catch (_) { /* ignore */ }
    document.documentElement.lang = code;
    document.dispatchEvent(
      new CustomEvent('otp:locale', { detail: { locale: code } })
    );
  }

  function getLocale() { return currentLocale; }

  window.LOCALES = window.LOCALES || {};

  function t(key, vars) {
    const catalog =
      (window.LOCALES[currentLocale] && window.LOCALES[currentLocale][key] != null
        ? window.LOCALES[currentLocale][key]
        : null) ??
      (window.LOCALES[FALLBACK] && window.LOCALES[FALLBACK][key] != null
        ? window.LOCALES[FALLBACK][key]
        : null) ??
      key;

    if (!vars) return String(catalog);
    return String(catalog).replace(/\{(\w+)\}/g, (_, k) =>
      vars[k] != null ? vars[k] : `{${k}}`
    );
  }

  /* Reverse map: Vietnamese source string → key (built lazily once vi.js loads) */
  let viToKey = null;
  let sortedVi = null;

  function ensureReverseMap() {
    if (viToKey) return;
    viToKey = new Map();
    const vi = window.LOCALES.vi || {};
    for (const [key, value] of Object.entries(vi)) {
      if (typeof value !== 'string' || value.length < 2) continue;
      if (/\{[a-zA-Z_]+\}/.test(value)) continue;
      // Prefer first (more specific keys usually listed first in file order —
      // but Object.entries order is insertion order; keep first wins)
      if (!viToKey.has(value)) viToKey.set(value, key);
    }
    sortedVi = [...viToKey.keys()].sort((a, b) => b.length - a.length);
  }

  /** Translate a plain Vietnamese UI string (or pass-through). */
  function localizeText(text, vars) {
    if (text == null) return text;
    const s = String(text);
    ensureReverseMap();
    const key = viToKey.get(s);
    if (key) return t(key, vars);
    return s;
  }

  const VI_CHAR = /[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđĐ]/;

  function translateChunk(text) {
    if (!text) return text;
    let out = text;
    for (const vi of sortedVi) {
      // Skip short / ambiguous tokens — they collide with English loanwords
      // inside longer labels (e.g. "Rule" ⊂ "Rules" → "Ruless").
      // Vietnamese diacritics are safe at length ≥ 4; pure ASCII needs ≥ 12.
      if (vi.length < 4) continue;
      if (!VI_CHAR.test(vi) && vi.length < 12) continue;
      if (!out.includes(vi)) continue;
      out = out.split(vi).join(t(viToKey.get(vi)));
    }
    return out;
  }

  /**
   * Translate Vietnamese UI copy inside an HTML blob without touching tag
   * structure. Only text nodes and a few safe attributes are rewritten.
   */
  function localizeHtml(html) {
    if (html == null) return html;
    if (currentLocale === 'vi') return String(html);
    ensureReverseMap();
    let out = String(html);

    // Safe attributes whose entire value is UI copy
    out = out.replace(
      /\b(title|placeholder|aria-label|aria-placeholder)="([^"]*)"/g,
      (_, attr, val) => `${attr}="${localizeText(val).replace(/"/g, '&quot;')}"`,
    );

    // Text nodes only (never rewrite inside <...>)
    out = out.replace(/(<[^>]*>)|([^<]+)/g, (m, tag, text) => {
      if (tag) return tag;
      return translateChunk(text);
    });
    return out;
  }

  /** Compact language switcher markup (for auth + header). */
  function languageSwitcherHtml(extraClass = '') {
    const cur = currentLocale;
    const opts = SUPPORTED_LOCALES.map(code => {
      const meta = LOCALE_META[code];
      const sel = code === cur ? 'selected' : '';
      return `<option value="${code}" ${sel}>${meta.short} — ${meta.label}</option>`;
    }).join('');
    return `<label class="lang-switch ${extraClass}" title="Language">
      <span class="lang-switch-icon" aria-hidden="true">🌐</span>
      <select class="lang-switch-select" onchange="setLocale(this.value)" aria-label="Language">
        ${opts}
      </select>
    </label>`;
  }

  window.t = t;
  window.setLocale = setLocale;
  window.getLocale = getLocale;
  window.localizeText = localizeText;
  window.localizeHtml = localizeHtml;
  window.languageSwitcherHtml = languageSwitcherHtml;
  window.SUPPORTED_LOCALES = SUPPORTED_LOCALES;
  window.LOCALE_META = LOCALE_META;
})();
