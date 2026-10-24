import asyncio
import base64
import gc
import hashlib
import hmac
import html as html_lib
import json
import os
import re
import secrets
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
import email
import email.header
from email import policy
from email.header import decode_header
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, NamedTuple, Optional
from urllib.parse import quote, urljoin, urlparse

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import db as dbm
import schema as schema_mod


# ============================================================
# Configuration
# ============================================================

APP_NAME = os.getenv("APP_NAME", "OTP Inbox")
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# --- database selection ------------------------------------------------
# Set DATABASE_URL (or POSTGRES_URL) to a postgres:// DSN to use PostgreSQL.
# Leave it unset to use the zero-config SQLite file at DATABASE_PATH.
# Managed hosts (Koyeb, Neon, Supabase, Railway) hand out a postgres:// URL;
# paste it verbatim and nothing else needs to change.
USING_POSTGRES = dbm.using_postgres()
if not USING_POSTGRES:
    _db_path = Path(os.getenv("DATABASE_PATH", "./data/otp_inbox.db")).expanduser()
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    DATABASE_PATH = str(_db_path)
    del _db_path

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "change-me-now")
WORKER_INGEST_TOKEN = os.getenv("WORKER_INGEST_TOKEN", "change-this-worker-token")
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "7"))
MAX_EMAIL_BYTES = int(os.getenv("MAX_EMAIL_BYTES", str(10 * 1024 * 1024)))
MAX_BODY_CHARS = int(os.getenv("MAX_BODY_CHARS", "200000"))
MAX_ATTACHMENTS_TOTAL = int(os.getenv("MAX_ATTACHMENTS_TOTAL", str(2 * 1024 * 1024)))
MAX_ATTACHMENT_BYTES = int(os.getenv("MAX_ATTACHMENT_BYTES", str(1 * 1024 * 1024)))
STORE_RAW_MIME = os.getenv("STORE_RAW_MIME", "0") != "0"
STORE_ATTACHMENTS = os.getenv("STORE_ATTACHMENTS", "1") != "0"
AUTO_DELETE_ENABLED = os.getenv("AUTO_DELETE_ENABLED", "0") != "0"
AUTO_DELETE_DAYS = int(os.getenv("AUTO_DELETE_DAYS", "7"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "1") != "0"

# --- self-service registration (referral gated) -------------------
# Both switches default to OFF. Registration is closed until an operator turns
# it on, and every individual user starts with code generation disabled, so no
# account can mint referral codes until an admin explicitly grants the
# permission. Admins can flip either switch at runtime from the dashboard.
REGISTRATION_ENABLED = os.getenv("REGISTRATION_ENABLED", "0") != "0"
REFERRAL_CODE_GENERATION = os.getenv("REFERRAL_CODE_GENERATION", "0") != "0"
REFERRAL_CODE_TTL_DAYS = int(os.getenv("REFERRAL_CODE_TTL_DAYS", "0"))
REFERRAL_MAX_USES_DEFAULT = int(os.getenv("REFERRAL_MAX_USES_DEFAULT", "1"))

# --- abuse limits ---------------------------------------------------
LOGIN_MAX_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "10"))
LOGIN_WINDOW_SECONDS = int(os.getenv("LOGIN_WINDOW_SECONDS", "900"))
REGISTER_MAX_ATTEMPTS = int(os.getenv("REGISTER_MAX_ATTEMPTS", "5"))
REGISTER_WINDOW_SECONDS = int(os.getenv("REGISTER_WINDOW_SECONDS", "3600"))
USERNAME_RE = re.compile(r"[A-Za-z0-9_.-]{3,40}")

# --- search --------------------------------------------------------
MAX_SEARCH_QUERY = int(os.getenv("MAX_SEARCH_QUERY", "200"))
FTS_BODY_CHARS = int(os.getenv("FTS_BODY_CHARS", "4000"))
FTS_MIN_TOKEN = 3
MIN_FULLTEXT_TERM = 3

# Bumped automatically from the asset bytes; ASSET_VERSION overrides it.
ASSET_VERSION = "auto"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auto-cleanup on boot if enabled
    try:
        with db() as conn:
            purge_old_messages(conn)
    except Exception as exc:
        print(f"[OTP Inbox] Startup retention cleanup skipped: {exc}")

    stop_event = asyncio.Event()

    async def _retention_worker():
        while not stop_event.is_set():
            try:
                # Check each minute, sleep 3600s total
                for _ in range(60):
                    if stop_event.is_set():
                        return
                    await asyncio.sleep(60)
                with db() as conn:
                    purged = purge_old_messages(conn)
                    if purged > 0:
                        print(f"[OTP Inbox] Periodic retention: purged {purged} old messages")
                trim_memory()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[OTP Inbox] Retention worker error: {e}")

    task = asyncio.create_task(_retention_worker())
    try:
        yield
    finally:
        stop_event.set()
        task.cancel()


from starlette.middleware.gzip import GZipMiddleware

app = FastAPI(title=APP_NAME, docs_url="/docs", redoc_url=None, lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
# Capture groups may include separators (123-456 / 123 456); normalize to digits later.
_OTP_CODE = r"((?:\d[ \u00a0\u2009\u202f\-]*){3,7}\d)"
OTP_PATTERNS = [
    re.compile(
        rf"(?is)\b(?:otp|one[- ]?time(?:\s+pass(?:word|code))?|verification(?:\s+code)?|verify|"
        rf"passcode|security\s+code|login\s+code|authentication(?:\s+code)?|"
        rf"m[ãa]\s*(?:x[áa]c\s*th[ựu]c|otp)|m[ãa]\s*x[áa]c\s*nh[ậa]n)\b"
        rf"[^\d]{{0,80}}{_OTP_CODE}\b"
    ),
    re.compile(rf"(?is)\b(?:code|pin|m[ãa])\s*(?:is|:|-|là)?\s*{_OTP_CODE}\b"),
    re.compile(
        rf"(?is)\b{_OTP_CODE}\b\s*(?:is\s+your|is\s+the|l[àa])\s*"
        rf"(?:otp|code|passcode|m[ãa]\s*(?:x[áa]c\s*th[ựu]c|otp)?)\b"
    ),
]
OTP_ANY_RE = re.compile(rf"(?<![#\w]){_OTP_CODE}(?!\w)")
OTP_HEX_COLOR_RE = re.compile(r"(?<![0-9A-Fa-f])#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})\b")
OTP_RGB_RE = re.compile(r"(?is)\brgba?\s*\([^)]*\)")
OTP_CSS_NOISE_RE = re.compile(
    r"(?is)\b(?:color|background(?:-color)?|border(?:-color)?|outline-color|"
    r"box-shadow|text-shadow)\s*:\s*[^;}\"']+"
)
VOID_DANGEROUS_TAGS = {"meta", "link", "base", "input", "source", "track", "param", "wbr"}
CONTAINER_DANGEROUS_TAGS = {
    "script", "iframe", "frame", "frameset", "object", "embed", "applet", "form",
    "button", "textarea", "select", "option", "optgroup", "svg", "math",
    "canvas", "template", "video", "audio",
}
DANGEROUS_HTML_TAGS = VOID_DANGEROUS_TAGS | CONTAINER_DANGEROUS_TAGS
VOID_TAGS = {"area", "br", "col", "hr", "img", "source", "wbr"}
MAX_REGEX_LEN = 1500

# Referral codes are shared in plain text, so they are excluded from every
# char class that could turn them ambiguous when read aloud or re-typed.
REFERRAL_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
REFERRAL_CODE_PREFIX = "REF"
REFERRAL_CODE_GROUPS = 2
REFERRAL_CODE_GROUP_LEN = 4
REFERRAL_CODE_RE = re.compile(
    rf"^{REFERRAL_CODE_PREFIX}-[A-Z2-9]{{{REFERRAL_CODE_GROUP_LEN}}}"
    rf"-[A-Z2-9]{{{REFERRAL_CODE_GROUP_LEN}}}$")

# Session cookie attributes, defined once so login and logout always agree.
# A mismatched delete_cookie silently leaves the session alive in the browser.
SESSION_COOKIE = "otp_session"
SESSION_COOKIE_PATH = "/"
SESSION_COOKIE_KWARGS = {"path": SESSION_COOKIE_PATH, "secure": COOKIE_SECURE,
                         "httponly": True, "samesite": "lax"}

SETTING_REGISTRATION_ENABLED = "registration_enabled"
SETTING_REFERRAL_CODE_GENERATION = "referral_code_generation"
SETTING_AUTO_DELETE_ENABLED = "auto_delete_enabled"
SETTING_AUTO_DELETE_DAYS = "auto_delete_days"

SETTING_DEFAULTS = {
    # Seeded on first boot; afterwards the row in app_settings is authoritative.
    SETTING_REGISTRATION_ENABLED: "1" if REGISTRATION_ENABLED else "0",
    SETTING_REFERRAL_CODE_GENERATION: "1" if REFERRAL_CODE_GENERATION else "0",
    SETTING_AUTO_DELETE_ENABLED: "1" if AUTO_DELETE_ENABLED else "0",
    SETTING_AUTO_DELETE_DAYS: str(AUTO_DELETE_DAYS),
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utc_now().isoformat()


def trim_memory() -> None:
    """Force Python garbage collection and release freed memory back to the OS on Linux (malloc_trim)."""
    try:
        gc.collect(1)
        gc.collect()
        import ctypes
        libc = ctypes.CDLL("libc.so.6")
        if hasattr(libc, "malloc_trim"):
            libc.malloc_trim(0)
    except Exception:
        pass


def normalize_email(value: str) -> str:
    raw = str(value or "").strip()
    _name, parsed = parseaddr(raw)
    if parsed:
        return parsed.strip().casefold()
    return raw.strip().casefold()


def clean_text(value: Optional[str], limit: int = 10000) -> str:
    text = str(value or "")
    return text[:limit]


def valid_email(value: str) -> bool:
    return bool(EMAIL_RE.fullmatch(normalize_email(value)))


# ============================================================
# Security helpers
# ============================================================


# A result row: `dict` on PostgreSQL, `sqlite3.Row` on SQLite. Both support
# `row["column"]`, `dict(row)` and `row.keys()`, which is all the app uses.
Row = Any


def hash_secret(value: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", value.encode(), salt, 240_000)
    return base64.urlsafe_b64encode(salt + digest).decode()


def verify_secret(value: str, encoded: str) -> bool:
    try:
        raw = base64.urlsafe_b64decode(encoded.encode())
        salt, expected = raw[:16], raw[16:]
        actual = hashlib.pbkdf2_hmac("sha256", value.encode(), salt, 240_000)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def generate_public_id(prefix: str = "U") -> str:
    return f"{prefix}-{secrets.token_hex(5).upper()}"


def generate_api_key() -> str:
    return "sk-otp-" + secrets.token_urlsafe(32)


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def normalize_referral_code(value: str) -> str:
    """Fold a user-typed code into its canonical form.

    Tolerates missing/extra dashes, lowercase, and surrounding whitespace so a
    code that is read aloud or re-typed still verifies.
    """
    raw = re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()
    if not raw.startswith(REFERRAL_CODE_PREFIX):
        return ""
    body = raw[len(REFERRAL_CODE_PREFIX):]
    if len(body) != REFERRAL_CODE_GROUP_LEN * REFERRAL_CODE_GROUPS:
        return ""
    groups = [body[i:i + REFERRAL_CODE_GROUP_LEN] for i in range(0, len(body), REFERRAL_CODE_GROUP_LEN)]
    return "-".join([REFERRAL_CODE_PREFIX, *groups])


def generate_referral_code() -> str:
    groups = [
        "".join(secrets.choice(REFERRAL_ALPHABET) for _ in range(REFERRAL_CODE_GROUP_LEN))
        for _ in range(REFERRAL_CODE_GROUPS)
    ]
    return "-".join([REFERRAL_CODE_PREFIX, *groups])


def referral_code_fingerprint(code: str) -> str:
    """Identifier stored alongside a registration for auditing.

    This is the first 8 characters of the code's SHA-256 digest, not the code
    itself, so the audit log cannot be replayed as a set of valid codes.
    """
    return hash_token(normalize_referral_code(code))[:8]


# ============================================================
# In-process rate limiting
# ============================================================


class RateLimiter:
    """Fixed-window counter keyed by an arbitrary string.

    Deliberately in-process: the app is deployed as a single instance against a
    local SQLite file (see docs/DEPLOY.md), so shared state is not available
    and a single process is the whole threat surface this needs to cover.
    """

    def __init__(self) -> None:
        self._buckets: dict[str, list[float]] = {}

    def hit(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int, int]:
        """Record an attempt. Returns (allowed, remaining, retry_after_seconds)."""
        if limit <= 0:
            return True, 0, 0
        now = utc_now().timestamp()
        hits = [t for t in self._buckets.get(key, []) if now - t < window_seconds]
        if len(hits) >= limit:
            self._buckets[key] = hits
            return False, 0, max(1, int(window_seconds - (now - hits[0])))
        hits.append(now)
        self._buckets[key] = hits
        return True, max(0, limit - len(hits)), 0

    def clear(self, key: str) -> None:
        self._buckets.pop(key, None)

    def sweep(self) -> None:
        now = utc_now().timestamp()
        for key in [k for k, v in self._buckets.items() if not v or now - max(v) > 86400]:
            self._buckets.pop(key, None)


LOGIN_LIMITER = RateLimiter()
REGISTER_LIMITER = RateLimiter()
CODE_GENERATE_LIMITER = RateLimiter()
API_KEY_LIMITER = RateLimiter()

API_KEY_MAX_REQUESTS = int(os.getenv("API_KEY_MAX_REQUESTS", "120"))
API_KEY_WINDOW_SECONDS = int(os.getenv("API_KEY_WINDOW_SECONDS", "60"))
API_KEY_CACHE_TTL = int(os.getenv("API_KEY_CACHE_TTL", "600"))
ACCESS_CACHE_TTL = int(os.getenv("ACCESS_CACHE_TTL", "180"))
RULES_CACHE_TTL = int(os.getenv("RULES_CACHE_TTL", "60"))
SETTINGS_CACHE_TTL = int(os.getenv("SETTINGS_CACHE_TTL", "60"))
MAILBOX_ADDR_CACHE_MAX = int(os.getenv("MAILBOX_ADDR_CACHE_MAX", "4096"))


class TtlCache:
    """Small process-local TTL map. Values must stay tiny (ids/flags, not MIME)."""

    def __init__(self, ttl_seconds: float, max_size: int = 4096):
        self.ttl = float(ttl_seconds)
        self.max_size = max_size
        self._items: dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any) -> Any:
        hit = self._items.get(key)
        if not hit:
            return None
        expires, value = hit
        if expires < utc_now().timestamp():
            self._items.pop(key, None)
            return None
        return value

    def set(self, key: Any, value: Any) -> None:
        if len(self._items) >= self.max_size:
            # Drop expired first; if still full, drop an arbitrary oldest-ish entry.
            now = utc_now().timestamp()
            for k, (exp, _) in list(self._items.items()):
                if exp < now:
                    self._items.pop(k, None)
            if len(self._items) >= self.max_size:
                self._items.pop(next(iter(self._items)), None)
        self._items[key] = (utc_now().timestamp() + self.ttl, value)

    def invalidate(self, key: Any = None) -> None:
        if key is None:
            self._items.clear()
        else:
            self._items.pop(key, None)

    def clear(self) -> None:
        self._items.clear()


# Verified API keys: prefix -> {user dict fields, key_hash fingerprint, full key hash check done}
API_KEY_CACHE = TtlCache(API_KEY_CACHE_TTL, max_size=512)
# user_id -> frozenset(mailbox_id)
ACCESS_CACHE = TtlCache(ACCESS_CACHE_TTL, max_size=2048)
# singleton list of active routing rule rows (as dicts)
RULES_CACHE = TtlCache(RULES_CACHE_TTL, max_size=32)
# setting key -> "0"|"1"
SETTINGS_CACHE = TtlCache(SETTINGS_CACHE_TTL, max_size=32)
# normalized address -> mailbox_id
MAILBOX_ADDR_CACHE = TtlCache(300, max_size=MAILBOX_ADDR_CACHE_MAX)
# mailbox_id -> lightweight latest message watermark (for poll hints; not authoritative)
LATEST_BY_MAILBOX: dict[int, dict[str, Any]] = {}
LATEST_BY_MAILBOX_MAX = int(os.getenv("LATEST_BY_MAILBOX_MAX", "1024"))


def invalidate_access_caches(*user_ids: int) -> None:
    if not user_ids:
        ACCESS_CACHE.clear()
        return
    for uid in user_ids:
        ACCESS_CACHE.invalidate(uid)


def invalidate_rules_cache() -> None:
    RULES_CACHE.clear()


def invalidate_settings_cache(key: Optional[str] = None) -> None:
    SETTINGS_CACHE.invalidate(key)


def invalidate_api_key_cache(prefix: Optional[str] = None) -> None:
    API_KEY_CACHE.invalidate(prefix)


def remember_latest_message(mailbox_id: int, *, message_id: int, address: str,
                            received_at: str, otp: Optional[str], subject: str) -> None:
    if len(LATEST_BY_MAILBOX) >= LATEST_BY_MAILBOX_MAX and mailbox_id not in LATEST_BY_MAILBOX:
        LATEST_BY_MAILBOX.pop(next(iter(LATEST_BY_MAILBOX)), None)
    LATEST_BY_MAILBOX[mailbox_id] = {
        "id": message_id,
        "address": address,
        "received_at": received_at,
        "otp": otp,
        "subject": subject,
    }


def client_ip(request: Request) -> str:
    """Best-effort client address.

    X-Forwarded-For is only honoured for the first hop, which is the proxy, and
    the value is only used as a rate-limit key and an audit field.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


# ============================================================
# Database
# ============================================================

@contextmanager
def db():
    """Open a connection to the configured backend (SQLite or PostgreSQL)."""
    with dbm.connect() as conn:
        yield conn


def table_columns(conn: Any, table: str) -> set[str]:
    if USING_POSTGRES:
        rows = conn.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema = current_schema() AND table_name = ?""", (table,)).fetchall()
        return {r["column_name"] for r in rows}
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def ensure_column(conn: Any, table: str, column: str, _ddl: str = "") -> None:
    """Add a column if missing, picking the dialect's type from schema.MIGRATIONS."""
    if column in table_columns(conn, table):
        return
    ddl = ""
    for entry in schema_mod.MIGRATIONS:
        if entry[0] == table and entry[1] == column:
            ddl = schema_mod.ddl_for(schema_mod.MIGRATIONS.index(entry), USING_POSTGRES)
            break
    if not ddl:
        ddl = f"{column} TEXT" if USING_POSTGRES else f"{column} TEXT"
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


# ============================================================
# Full-text search
# ============================================================
#
# SQLite uses an FTS5 virtual table maintained by this module. PostgreSQL uses
# a generated tsvector column with a GIN index, which the database keeps in
# sync itself, so there is nothing to do beyond creating the schema.
#
# FTS_AVAILABLE means "indexed full-text search is available on this backend".
# When it is false, search silently falls back to a LIKE scan.

FTS_AVAILABLE = False


def fts_query(text: str) -> str:
    """Safe FTS5 MATCH expression built from free-form user input.

    Kept as a thin re-export so existing callers and tests do not change; the
    implementation lives in db.py alongside the PostgreSQL equivalent.
    """
    return dbm.fts_query(text)


def init_search_index(conn: Any) -> None:
    global FTS_AVAILABLE
    if USING_POSTGRES:
        # The generated column and its GIN index are created by the schema.
        # Verify the column really is there, in case the table predates it.
        if "search_vector" in table_columns(conn, "messages"):
            FTS_AVAILABLE = True
            return
        FTS_AVAILABLE = False
        print("[OTP Inbox] messages.search_vector missing; falling back to LIKE search")
        return

    try:
        conn.execute(
            """CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                   subject, envelope_from, envelope_to, otp, body,
                   message_id UNINDEXED,
                   tokenize='unicode61 remove_diacritics 2'
               )"""
        )
        # Mailboxes are deleted with CASCADE, so keep the index from outliving
        # the rows it describes.
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS trg_messages_fts_delete AFTER DELETE ON messages BEGIN
                   DELETE FROM messages_fts WHERE message_id = old.id;
               END"""
        )
        FTS_AVAILABLE = True
    except sqlite3.Error as exc:
        FTS_AVAILABLE = False
        print(f"[OTP Inbox] FTS5 unavailable ({exc}); falling back to LIKE search")
        return

    # Backfill anything indexed before this feature shipped, or by a fallback run.
    try:
        conn.execute(
            """INSERT INTO messages_fts(message_id,subject,envelope_from,envelope_to,otp,body)
               SELECT id,subject,envelope_from,envelope_to,COALESCE(otp,''),substr(COALESCE(body_text,''),1,?)
               FROM messages
               WHERE id NOT IN (SELECT message_id FROM messages_fts)""",
            (FTS_BODY_CHARS,),
        )
    except sqlite3.Error as exc:
        FTS_AVAILABLE = False
        print(f"[OTP Inbox] Search backfill failed ({exc}); falling back to LIKE search")


def index_message(conn: Any, message_id: int, subject: str, envelope_from: str,
                  envelope_to: str, otp: Optional[str], body_text: Optional[str]) -> None:
    """Keep the SQLite FTS5 index in step with a newly ingested message.

    A no-op on PostgreSQL, where the generated tsvector column updates itself.
    """
    if not FTS_AVAILABLE or USING_POSTGRES:
        return
    try:
        conn.execute("DELETE FROM messages_fts WHERE message_id=?", (message_id,))
        conn.execute(
            """INSERT INTO messages_fts(message_id,subject,envelope_from,envelope_to,otp,body)
               VALUES(?,?,?,?,?,?)""",
            (message_id, subject, envelope_from, envelope_to, otp or "",
             (body_text or "")[:FTS_BODY_CHARS]),
        )
    except sqlite3.Error:
        # Search must never be able to reject an email ingest.
        pass



# ============================================================
# Runtime settings
# ============================================================


def seed_settings(conn: sqlite3.Connection) -> None:
    for key, value in SETTING_DEFAULTS.items():
        conn.execute(
            "INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO NOTHING",
            (key, value, now_iso()),
        )


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    cached = SETTINGS_CACHE.get(key)
    if cached is not None:
        return str(cached)
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    value = str(row["value"]) if row else SETTING_DEFAULTS.get(key, default)
    SETTINGS_CACHE.set(key, value)
    return value


def get_setting_bool(conn: sqlite3.Connection, key: str) -> bool:
    return get_setting(conn, key) == "1"


def set_setting(conn: sqlite3.Connection, key: str, value: bool) -> None:
    conn.execute(
        """INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
        (key, "1" if value else "0", now_iso()),
    )
    SETTINGS_CACHE.set(key, "1" if value else "0")


def registration_open(conn: sqlite3.Connection) -> bool:
    return get_setting_bool(conn, SETTING_REGISTRATION_ENABLED)


def code_generation_open(conn: sqlite3.Connection) -> bool:
    return get_setting_bool(conn, SETTING_REFERRAL_CODE_GENERATION)


def seed_domains(conn: Any) -> None:
    try:
        count = conn.execute("SELECT COUNT(*) c FROM domains").fetchone()["c"]
        if count == 0:
            found = set()
            for r in conn.execute("SELECT pattern, rule_type FROM routing_rules").fetchall():
                pat = (r["pattern"] or "").strip().lower()
                if r["rule_type"] == "domain" and pat and "." in pat:
                    found.add(pat)
                elif r["rule_type"] == "catch_all" and "@" in pat:
                    found.add(pat.split("@")[-1].strip())
            for m in conn.execute("SELECT address FROM mailboxes LIMIT 200").fetchall():
                addr = (m["address"] or "").strip().lower()
                if "@" in addr:
                    dom = addr.split("@")[-1].strip()
                    if "." in dom:
                        found.add(dom)
            if not found:
                # Placeholder so a fresh install has something selectable in the UI.
                # Replace via Admin → Domains (or set domains from your mail DNS).
                found.add(os.getenv("DEFAULT_MAIL_DOMAIN", "example.com").strip().lower() or "example.com")
            now = now_iso()
            for d in sorted(found):
                conn.execute(
                    "INSERT INTO domains(name,access_level,description,is_active,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (d, "public", "Default domain", 1, now, now),
                )
    except Exception as exc:
        print(f"[OTP Inbox] seed_domains notice: {exc}")


def list_domains_for_user(conn: Any, user: dict[str, Any]) -> list[dict[str, Any]]:
    if user["role"] == "admin":
        rows = conn.execute("""
            SELECT d.*,
                   (SELECT COUNT(*) FROM mailboxes m WHERE m.address LIKE '%@' || d.name) as mailbox_count
            FROM domains d
            ORDER BY d.is_active DESC, d.name ASC
        """).fetchall()
        return [dict(r) for r in rows]
    else:
        rows = conn.execute("""
            SELECT id, name, access_level, description
            FROM domains
            WHERE is_active = 1
            ORDER BY CASE WHEN access_level = 'public' THEN 0 ELSE 1 END, name ASC
        """).fetchall()
        return [dict(r) for r in rows]


# ============================================================
# Housekeeping
# ============================================================


def purge_expired_sessions(conn: sqlite3.Connection) -> int:
    cur = conn.execute("DELETE FROM sessions WHERE expires_at<=?", (now_iso(),))
    return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def auto_delete_open(conn: sqlite3.Connection) -> bool:
    return get_setting_bool(conn, SETTING_AUTO_DELETE_ENABLED)


def auto_delete_retention_days(conn: sqlite3.Connection) -> int:
    try:
        return max(1, int(get_setting(conn, SETTING_AUTO_DELETE_DAYS, str(AUTO_DELETE_DAYS))))
    except (ValueError, TypeError):
        return 7


def purge_old_messages(conn: Any, days: Optional[int] = None) -> int:
    """Purge messages older than `days` (or the configured auto_delete_days setting).
    
    Cascades to attachments, message_reads, and notifications. Reclaims SQLite disk space.
    """
    if days is None:
        if not auto_delete_open(conn):
            return 0
        days = auto_delete_retention_days(conn)
    else:
        try:
            days = max(1, int(days))
        except (ValueError, TypeError):
            days = 7

    cutoff = (utc_now() - timedelta(days=days)).isoformat()
    rows = conn.execute("SELECT id FROM messages WHERE received_at <= ?", (cutoff,)).fetchall()
    if not rows:
        return 0

    ids = [r["id"] for r in rows]
    batch_size = 200
    deleted = 0
    for i in range(0, len(ids), batch_size):
        chunk = ids[i:i + batch_size]
        placeholders = ",".join("?" for _ in chunk)
        conn.execute(f"DELETE FROM messages WHERE id IN ({placeholders})", chunk)
        deleted += len(chunk)

    LATEST_BY_MAILBOX.clear()
    ACCESS_CACHE.clear()

    if not USING_POSTGRES:
        try:
            conn.execute("PRAGMA incremental_vacuum(500)")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass

    trim_memory()
    return deleted


def ensure_messages_dedupe(conn: Any) -> None:
    """Guarantee a UNIQUE index on (mailbox_id, raw_sha256) for ingest ON CONFLICT.

    Production DBs often have one of these problems:
    - the unique index was never created
    - a *non-unique* index already occupies the name ``idx_messages_dedupe``,
      so ``CREATE UNIQUE INDEX IF NOT EXISTS`` is a silent no-op
    - duplicate rows previously blocked index creation
    """
    try:
        cols = table_columns(conn, "messages")
    except Exception:
        return
    if not cols or "raw_sha256" not in cols:
        return

    if _messages_dedupe_index_is_unique(conn):
        return

    # Wrong or missing index: drop the name so we can recreate it as UNIQUE.
    try:
        conn.execute("DROP INDEX IF EXISTS idx_messages_dedupe")
    except Exception as exc:
        print(f"[OTP Inbox] Drop stale dedupe index skipped ({exc})")

    # Keep the newest row per (mailbox_id, digest); CASCADE drops attachments.
    try:
        cur = conn.execute(
            """DELETE FROM messages
               WHERE raw_sha256 IS NOT NULL
                 AND EXISTS (
                     SELECT 1 FROM messages AS m2
                     WHERE m2.mailbox_id = messages.mailbox_id
                       AND m2.raw_sha256 = messages.raw_sha256
                       AND m2.id > messages.id
                 )"""
        )
        removed = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        if removed:
            print(f"[OTP Inbox] Removed {removed} duplicate message(s) before dedupe index")
    except Exception as exc:
        print(f"[OTP Inbox] Dedupe cleanup skipped ({exc})")

    try:
        conn.execute(
            "CREATE UNIQUE INDEX idx_messages_dedupe ON messages(mailbox_id, raw_sha256)"
        )
        print("[OTP Inbox] Created UNIQUE index idx_messages_dedupe (mailbox_id, raw_sha256)")
    except Exception as exc:
        # Concurrent boot may have won the race — accept if unique index now exists.
        if _messages_dedupe_index_is_unique(conn):
            return
        print(f"[OTP Inbox] FATAL: cannot create messages dedupe index: {exc}")
        raise


def _messages_dedupe_index_is_unique(conn: Any) -> bool:
    """True when a unique index/constraint on (mailbox_id, raw_sha256) exists."""
    if USING_POSTGRES:
        try:
            row = conn.execute(
                """SELECT 1 AS ok FROM pg_indexes
                   WHERE schemaname = current_schema()
                     AND tablename = 'messages'
                     AND indexdef ILIKE '%UNIQUE%'
                     AND indexdef ILIKE '%(mailbox_id%'
                     AND indexdef ILIKE '%raw_sha256%'
                   LIMIT 1"""
            ).fetchone()
            return bool(row)
        except Exception:
            return False
    # SQLite: look for a unique index covering exactly those two columns.
    try:
        for idx in conn.execute("PRAGMA index_list(messages)").fetchall():
            name = idx["name"] if hasattr(idx, "keys") and "name" in idx.keys() else idx[1]
            is_unique = idx["unique"] if hasattr(idx, "keys") and "unique" in idx.keys() else idx[2]
            if not is_unique:
                continue
            cols = [
                (r["name"] if hasattr(r, "keys") and "name" in r.keys() else r[2])
                for r in conn.execute(f"PRAGMA index_info('{name}')").fetchall()
            ]
            if cols == ["mailbox_id", "raw_sha256"]:
                return True
    except Exception:
        return False
    return False


def insert_message_row(
    conn: Any,
    *,
    mailbox_id: int,
    envelope_from: str,
    envelope_to: str,
    subject: str,
    message_id_hdr: Optional[str],
    received_at: str,
    otp: Optional[str],
    body_text: str,
    body_html: str,
    raw: Optional[bytes],
    digest: str,
    has_attachments: int,
    size_bytes: Optional[int] = None,
) -> Optional[int]:
    """Insert a message with atomic dedupe. Returns new id, or None if duplicate.

    Ensures the unique index exists (healing production DBs) and retries once if
    Postgres rejects ON CONFLICT because the constraint was still missing.
    """
    raw_size = size_bytes if size_bytes is not None else (len(raw) if raw is not None else 0)
    raw_blob = raw if STORE_RAW_MIME else None
    params = (
        mailbox_id, envelope_from, envelope_to, subject, message_id_hdr, received_at,
        otp, body_text, body_html, raw_blob, raw_size, digest, has_attachments,
    )
    sql = (
        """INSERT INTO messages(mailbox_id,envelope_from,envelope_to,subject,message_id,received_at,otp,body_text,body_html,raw_mime,size_bytes,raw_sha256,has_attachments)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(mailbox_id,raw_sha256) DO NOTHING"""
    )

    def _run():
        return conn.execute(sql, params)

    try:
        cur = _run()
    except Exception as exc:
        text = str(exc).lower()
        if "on conflict" in text or "unique or exclusion" in text or "invalidcolumnreference" in text:
            ensure_messages_dedupe(conn)
            try:
                cur = _run()
            except Exception:
                # Absolute fallback: unique index still missing — SELECT then INSERT.
                hit = conn.execute(
                    "SELECT id FROM messages WHERE mailbox_id=? AND raw_sha256=?",
                    (mailbox_id, digest),
                ).fetchone()
                if hit:
                    return None
                cur = conn.execute(
                    """INSERT INTO messages(mailbox_id,envelope_from,envelope_to,subject,message_id,received_at,otp,body_text,body_html,raw_mime,size_bytes,raw_sha256,has_attachments)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    params,
                )
                return cur.lastrowid
        else:
            raise
    if cur.lastrowid is None:
        return None
    return cur.lastrowid


def ensure_routing_rules_schema(conn: Any) -> None:
    """Guarantee routing_rules CHECK constraint supports domain and catch_all."""
    if USING_POSTGRES:
        try:
            conn.execute("ALTER TABLE routing_rules DROP CONSTRAINT IF EXISTS routing_rules_rule_type_check")
            conn.execute("ALTER TABLE routing_rules ADD CONSTRAINT routing_rules_rule_type_check CHECK(rule_type IN ('prefix','regex','domain','catch_all'))")
        except Exception:
            pass
    else:
        try:
            row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='routing_rules'").fetchone()
            if row and row["sql"] and "domain" not in row["sql"]:
                conn.execute("PRAGMA foreign_keys = OFF")
                conn.execute("""
                    CREATE TABLE routing_rules_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        rule_type TEXT NOT NULL CHECK(rule_type IN ('prefix','regex','domain','catch_all')),
                        pattern TEXT NOT NULL,
                        target_user_id INTEGER NOT NULL,
                        description TEXT NOT NULL DEFAULT '',
                        is_active INTEGER NOT NULL DEFAULT 1,
                        created_by INTEGER,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY(target_user_id) REFERENCES users(id) ON DELETE CASCADE
                    )
                """)
                conn.execute("INSERT INTO routing_rules_new(id,rule_type,pattern,target_user_id,description,is_active,created_by,created_at,updated_at) SELECT id,rule_type,pattern,target_user_id,description,is_active,created_by,created_at,updated_at FROM routing_rules")
                conn.execute("DROP TABLE routing_rules")
                conn.execute("ALTER TABLE routing_rules_new RENAME TO routing_rules")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_rules_target ON routing_rules(target_user_id)")
                conn.execute("PRAGMA foreign_keys = ON")
        except Exception as exc:
            print(f"[OTP Inbox] Migration of routing_rules skipped ({exc})")


def init_db() -> None:
    """Create or migrate the schema, then make sure an admin account exists.

    Safe to run on every boot: every statement is IF NOT EXISTS or a guarded
    column add, so this is the only migration path a fresh deploy needs.
    """
    if not USING_POSTGRES:
        Path(DATABASE_PATH).expanduser().parent.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        if not USING_POSTGRES:
            # WAL is a persistent database property, so setting it once is
            # enough. It lets the ingest writer commit while readers are still
            # active, removing the writer starvation of the rollback journal.
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA wal_autocheckpoint = 1000")
            try:
                conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
            except Exception:
                pass

        # If an older DB already has duplicate digests, remove them *before*
        # schema DDL tries to create the unique index (kept out of schema DDL
        # so this cleanup can always run first).
        ensure_messages_dedupe(conn)

        conn.executescript(schema_mod.schema_for(USING_POSTGRES))
        ensure_routing_rules_schema(conn)

        # Backfill columns for databases created before a feature existed.
        for table, column, _sqlite_ddl, _pg_ddl in schema_mod.MIGRATIONS:
            ensure_column(conn, table, column)

        # Column migrations may have just added raw_sha256 — ensure index again.
        ensure_messages_dedupe(conn)

        conn.execute("UPDATE users SET updated_at=COALESCE(updated_at,created_at)")
        conn.execute("UPDATE mailboxes SET updated_at=COALESCE(updated_at,created_at)")
        # Access that already existed in the MVP was explicitly granted, so the
        # mailbox becomes exact rather than rule-derived.
        conn.execute(
            "UPDATE mailboxes SET exact_override=1, override_reason=COALESCE(override_reason,'migrated from previous MVP') "
            "WHERE exact_override=0 AND (owner_user_id IS NOT NULL OR id IN (SELECT mailbox_id FROM mailbox_access))"
        )
        conn.execute("UPDATE mailbox_access SET source='manual' WHERE source IS NULL OR source=''")
        conn.execute("UPDATE users SET created_via='admin' WHERE created_via IS NULL OR created_via=''")

        init_search_index(conn)
        seed_settings(conn)
        seed_domains(conn)
        purge_expired_sessions(conn)
        purge_old_messages(conn)

        admin = conn.execute("SELECT id FROM users WHERE username=?", (ADMIN_USERNAME,)).fetchone()
        if not admin:
            public_id = generate_public_id("A")
            api_key = generate_api_key()
            created = now_iso()
            conn.execute(
                """INSERT INTO users(public_id,username,password_hash,role,api_key_hash,api_key_prefix,is_active,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (public_id,ADMIN_USERNAME,hash_secret(ADMIN_PASSWORD),"admin",hash_secret(api_key),api_key[:16],1,created,created),
            )
            print("[OTP Inbox] Admin created")
            print(f"[OTP Inbox] username: {ADMIN_USERNAME}")
            print(f"[OTP Inbox] API key (shown once): {api_key}")
            if not REGISTRATION_ENABLED:
                print("[OTP Inbox] Self-registration is CLOSED (REGISTRATION_ENABLED=0).")
        else:
            # Keep a disabled admin from locking out the deployment.
            conn.execute("UPDATE users SET is_active=1 WHERE id=?", (admin["id"],))
    LOGIN_LIMITER.sweep()
    REGISTER_LIMITER.sweep()
    CODE_GENERATE_LIMITER.sweep()


def user_row(row: Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "public_id": row["public_id"],
        "username": row["username"],
        "role": row["role"],
        "api_key_prefix": row["api_key_prefix"],
        "is_active": bool(row["is_active"]),
        "can_generate_codes": bool(row["can_generate_codes"]) if "can_generate_codes" in row.keys() else False,
        "created_via": row["created_via"] if "created_via" in row.keys() else "admin",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def user_by_public_id(conn: sqlite3.Connection, public_id: str) -> Optional[Row]:
    return conn.execute("SELECT * FROM users WHERE public_id=?", (str(public_id or "").strip().upper(),)).fetchone()


def user_by_id(conn: sqlite3.Connection, user_id: int) -> Optional[Row]:
    return conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()


def add_notification(conn: sqlite3.Connection, user_id: int, kind: str, title: str, body: str, mailbox_id: Optional[int] = None) -> None:
    conn.execute(
        "INSERT INTO notifications(user_id,kind,title,body,mailbox_id,created_at) VALUES(?,?,?,?,?,?)",
        (user_id,kind,title,body,mailbox_id,now_iso()),
    )


def add_audit(conn: sqlite3.Connection, actor_user_id: Optional[int], action: str, mailbox_id: Optional[int], target_user_id: Optional[int], details: str) -> None:
    conn.execute(
        "INSERT INTO audit_logs(actor_user_id,action,mailbox_id,target_user_id,details,created_at) VALUES(?,?,?,?,?,?)",
        (actor_user_id,action,mailbox_id,target_user_id,details,now_iso()),
    )


# ============================================================
# Referral codes / self-service registration
# ============================================================


def code_expiry_iso(days: Optional[int] = None) -> Optional[str]:
    if days is None:
        days = REFERRAL_CODE_TTL_DAYS
    if days <= 0:
        return None
    return (utc_now() + timedelta(days=days)).isoformat()


def code_status(row: Row) -> str:
    """Human-readable lifecycle state, computed from the row's own fields."""
    if not row["is_active"]:
        return "disabled"
    if row["expires_at"] and row["expires_at"] <= now_iso():
        return "expired"
    if row["uses"] >= row["max_uses"]:
        return "exhausted"
    return "active"


def code_is_redeemable(row: Optional[Row]) -> bool:
    return row is not None and code_status(row) == "active"


def create_referral_code(conn: sqlite3.Connection, owner_user_id: Optional[int], created_by: Optional[int],
                         max_uses: int, expires_at: Optional[str], note: str) -> tuple[Row, str]:
    """Mint a code and return (row, plaintext).

    The plaintext exists only in this return value. Collision is retried because
    the digest is the unique key, not the code itself.
    """
    max_uses = max(1, min(int(max_uses or 1), 1000))
    for _ in range(12):
        code = generate_referral_code()
        code_hash = hash_token(code)
        if conn.execute("SELECT 1 FROM referral_codes WHERE code_hash=?", (code_hash,)).fetchone():
            continue
        cur = conn.execute(
            """INSERT INTO referral_codes(code_hash,code_fingerprint,owner_user_id,created_by,max_uses,uses,expires_at,is_active,note,created_at)
               VALUES(?,?,?,?,?,0,?,1,?,?)""",
            (code_hash, referral_code_fingerprint(code), owner_user_id, created_by,
             max_uses, expires_at, note, now_iso()),
        )
        row = conn.execute("SELECT * FROM referral_codes WHERE id=?", (cur.lastrowid,)).fetchone()
        return row, code
    raise HTTPException(status_code=503, detail="Could not allocate a unique referral code, please retry")


def redeem_referral_code(conn: sqlite3.Connection, raw_code: str) -> Optional[Row]:
    """Look up a code by plaintext. Returns None for anything not redeemable."""
    code = normalize_referral_code(raw_code)
    if not code:
        return None
    row = conn.execute("SELECT * FROM referral_codes WHERE code_hash=?", (hash_token(code),)).fetchone()
    return row if code_is_redeemable(row) else None


def user_referral_summary(conn: sqlite3.Connection, user: Row) -> dict[str, Any]:
    """Everything the 'my referral code' panel needs, minus the plaintext."""
    global_open = code_generation_open(conn)
    can_generate = bool(user["can_generate_codes"])
    code = conn.execute(
        "SELECT * FROM referral_codes WHERE owner_user_id=? ORDER BY created_at DESC LIMIT 1",
        (user["id"],),
    ).fetchone()
    # A user may hold several codes over time (regenerations), so count across all.
    totals = conn.execute(
        """SELECT COUNT(*) codes,CAST(COALESCE(SUM(max_uses),0) AS INTEGER) capacity,CAST(COALESCE(SUM(uses),0) AS INTEGER) uses
           FROM referral_codes WHERE owner_user_id=?""",
        (user["id"],),
    ).fetchone()
    registrations = conn.execute(
        """SELECT r.id,r.code_fingerprint,r.created_at,u.username,u.public_id,u.is_active
           FROM registrations r JOIN users u ON u.id=r.user_id
           WHERE r.referral_code_id IN (SELECT id FROM referral_codes WHERE owner_user_id=?)
           ORDER BY r.created_at DESC,r.id DESC LIMIT 50""",
        (user["id"],),
    ).fetchall()
    return {
        "global_enabled": global_open,
        "can_generate": can_generate,
        "permission_active": global_open and can_generate,
        "reason": referral_block_reason(conn, user),
        "code": {
            "id": code["id"],
            "fingerprint": code["code_fingerprint"],
            "status": code_status(code),
            "max_uses": code["max_uses"],
            "uses": code["uses"],
            "remaining": max(0, code["max_uses"] - code["uses"]),
            "expires_at": code["expires_at"],
            "created_at": code["created_at"],
        } if code else None,
        "totals": {"codes": totals["codes"], "capacity": totals["capacity"], "uses": totals["uses"]},
        "registrations": [dict(r) for r in registrations],
    }


def referral_block_reason(conn: sqlite3.Connection, user: Row) -> Optional[str]:
    """Why this user cannot mint a code right now, or None if they can.

    Admins are never blocked: they can always create codes from the dashboard,
    which keeps an operator from locking themselves out of the feature.
    """
    if user["role"] == "admin":
        return None
    if not code_generation_open(conn):
        return "Code generation is currently disabled by the administrator."
    if not user["can_generate_codes"]:
        return "You do not have the code generation permission yet."
    return None


def can_issue_referral_code(conn: sqlite3.Connection, user: Row) -> bool:
    return referral_block_reason(conn, user) is None


def mailbox_by_id(conn: sqlite3.Connection, mailbox_id: int) -> Optional[Row]:
    return conn.execute("SELECT * FROM mailboxes WHERE id=?", (mailbox_id,)).fetchone()


def get_or_create_mailbox(conn: sqlite3.Connection, address: str) -> Row:
    addr = normalize_email(address)
    cached_id = MAILBOX_ADDR_CACHE.get(addr)
    if cached_id is not None:
        row = conn.execute("SELECT * FROM mailboxes WHERE id=?", (cached_id,)).fetchone()
        if row:
            return row
        MAILBOX_ADDR_CACHE.invalidate(addr)
    row = conn.execute("SELECT * FROM mailboxes WHERE address=?", (addr,)).fetchone()
    if row:
        MAILBOX_ADDR_CACHE.set(addr, row["id"])
        return row
    created = now_iso()
    cur = conn.execute(
        "INSERT INTO mailboxes(address,owner_user_id,exact_override,override_reason,created_at,updated_at) VALUES(?,NULL,0,NULL,?,?)",
        (addr, created, created),
    )
    row = conn.execute("SELECT * FROM mailboxes WHERE id=?", (cur.lastrowid,)).fetchone()
    MAILBOX_ADDR_CACHE.set(addr, row["id"])
    return row


def access_users(conn: sqlite3.Connection, mailbox_id: int) -> list[Row]:
    return conn.execute(
        """SELECT u.*,a.user_id AS access_user_id,a.source,a.rule_id,a.granted_at FROM mailbox_access a JOIN users u ON u.id=a.user_id
           WHERE a.mailbox_id=? ORDER BY CASE WHEN u.id=(SELECT owner_user_id FROM mailboxes WHERE id=?) THEN 0 ELSE 1 END,u.username""",
        (mailbox_id,mailbox_id),
    ).fetchall()


def is_owner(user: Row, mailbox: Row) -> bool:
    return user["role"] == "admin" or mailbox["owner_user_id"] == user["id"]


def user_mailbox_ids(conn: sqlite3.Connection, user: Row) -> frozenset[int]:
    """Cached set of mailbox ids this non-admin user can access."""
    uid = user["id"]
    cached = ACCESS_CACHE.get(uid)
    if cached is not None:
        return cached
    rows = conn.execute("SELECT mailbox_id FROM mailbox_access WHERE user_id=?", (uid,)).fetchall()
    ids = frozenset(r["mailbox_id"] for r in rows)
    ACCESS_CACHE.set(uid, ids)
    return ids


def has_access(conn: sqlite3.Connection, user: Row, mailbox_id: int) -> bool:
    if user["role"] == "admin":
        return bool(mailbox_by_id(conn, mailbox_id))
    return mailbox_id in user_mailbox_ids(conn, user)


def rule_matches(rule: Row, address: str) -> bool:
    address = normalize_email(address)
    rtype = rule["rule_type"]
    pattern = str(rule["pattern"]).strip()
    try:
        if rtype == "prefix":
            prefix = pattern
            if prefix.endswith("*"):
                prefix = prefix[:-1]
            return address.startswith(normalize_email(prefix))
        elif rtype == "domain":
            dom = pattern.lower().lstrip("@*.").strip()
            parts = address.split("@", 1)
            if len(parts) == 2:
                addr_dom = parts[1].lower()
                return addr_dom == dom or addr_dom.endswith("." + dom)
            return False
        elif rtype == "catch_all":
            pat = pattern.lower()
            if pat in ("*", "", "*@*", "@*"):
                return True
            dom = pat.lstrip("@*.").strip()
            parts = address.split("@", 1)
            if len(parts) == 2:
                addr_dom = parts[1].lower()
                return addr_dom == dom or addr_dom.endswith("." + dom)
            return True
        elif rtype == "regex":
            return re.search(pattern, address, flags=re.IGNORECASE) is not None
        return False
    except re.error:
        return False


def active_routing_rules(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cached = RULES_CACHE.get("active")
    if cached is not None:
        return cached
    rows = conn.execute(
        """SELECT r.*,u.username target_username,u.public_id target_public_id
           FROM routing_rules r JOIN users u ON u.id=r.target_user_id
           WHERE r.is_active=1 AND u.is_active=1
           ORDER BY CASE r.rule_type
               WHEN 'regex' THEN 1
               WHEN 'prefix' THEN 2
               WHEN 'domain' THEN 3
               WHEN 'catch_all' THEN 4
               ELSE 5
           END, r.id"""
    ).fetchall()
    rules = [dict(r) for r in rows]
    RULES_CACHE.set("active", rules)
    return rules


def matching_rules(conn: sqlite3.Connection, address: str) -> list[Row]:
    return [r for r in active_routing_rules(conn) if rule_matches(r, address)]


def sync_mailbox_rules(conn: sqlite3.Connection, mailbox: Row, notify: bool = False, actor_user_id: Optional[int] = None) -> list[int]:
    """Synchronize rule-derived access for one non-exact mailbox."""
    if mailbox["exact_override"]:
        return []
    rules = matching_rules(conn, mailbox["address"])
    target_map: dict[int, Row] = {}
    for r in rules:
        if r["target_user_id"] not in target_map:
            target_map[r["target_user_id"]] = r

    existing = conn.execute(
        "SELECT * FROM mailbox_access WHERE mailbox_id=?", (mailbox["id"],)
    ).fetchall()
    existing_ids = {r["user_id"] for r in existing}
    # Non-exact mailbox access is entirely rule-derived.
    for a in existing:
        if a["source"] != "rule" or a["user_id"] not in target_map:
            conn.execute("DELETE FROM mailbox_access WHERE mailbox_id=? AND user_id=?", (mailbox["id"],a["user_id"]))

    added: list[int] = []
    for uid, rule in target_map.items():
        current = conn.execute(
            "SELECT 1 FROM mailbox_access WHERE mailbox_id=? AND user_id=?", (mailbox["id"],uid)
        ).fetchone()
        if not current:
            conn.execute(
                "INSERT INTO mailbox_access(mailbox_id,user_id,source,rule_id,granted_by,granted_at) VALUES(?,?,?,?,?,?)",
                (mailbox["id"],uid,"rule",rule["id"],actor_user_id or rule["created_by"],now_iso()),
            )
            added.append(uid)
            if notify:
                add_notification(
                    conn,uid,"routing",
                    "Mailbox added by routing rule",
                    f"{mailbox['address']} khớp rule {rule['rule_type']} ({rule['pattern']}) và đã được đưa vào inbox của bạn.",
                    mailbox["id"],
                )
    
    # Auto-assign owner to primary target user if mailbox has no owner
    if not mailbox["owner_user_id"] and rules:
        first_target = rules[0]["target_user_id"]
        conn.execute("UPDATE mailboxes SET owner_user_id=?, updated_at=? WHERE id=?", (first_target, now_iso(), mailbox["id"]))
        MAILBOX_ADDR_CACHE.invalidate(mailbox["address"])

    invalidate_access_caches(*(existing_ids | set(target_map.keys())))
    return added


def clear_rule_access(conn: sqlite3.Connection, mailbox_id: int) -> None:
    touched = [r["user_id"] for r in conn.execute(
        "SELECT user_id FROM mailbox_access WHERE mailbox_id=? AND source='rule'", (mailbox_id,)
    ).fetchall()]
    conn.execute("DELETE FROM mailbox_access WHERE mailbox_id=? AND source='rule'", (mailbox_id,))
    if touched:
        invalidate_access_caches(*touched)


def set_exact_override(conn: sqlite3.Connection, mailbox_id: int, owner_user_id: Optional[int], reason: str) -> None:
    clear_rule_access(conn, mailbox_id)
    conn.execute(
        "UPDATE mailboxes SET exact_override=1,owner_user_id=?,override_reason=?,updated_at=? WHERE id=?",
        (owner_user_id, reason, now_iso(), mailbox_id),
    )
    if owner_user_id:
        invalidate_access_caches(owner_user_id)
    else:
        invalidate_access_caches()


# ============================================================
# Email parsing / HTML sanitization
# ============================================================


def parse_received_date(value: str) -> str:
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return now_iso()


def html_to_text(value: str) -> str:
    parser = _TextHTMLParser()
    try:
        parser.feed(value or "")
        parser.close()
        return re.sub(r"\n{3,}", "\n\n", parser.output()).strip()
    except Exception:
        return re.sub(r"<[^>]+>", " ", value or "")


class _TextHTMLParser(HTMLParser):
    """Strip markup for OTP / search text. Never emit <style>/<script>/<head>."""

    _BLOCK = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "hr"}
    _SKIP = {"style", "script", "head", "noscript", "title", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self._SKIP:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self._SKIP:
            if self.skip_depth:
                self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            self.parts.append(data)

    def handle_comment(self, data):
        return

    def output(self):
        return "".join(self.parts)


def normalize_otp_digits(raw: str) -> Optional[str]:
    """Collapse a captured OTP token to contiguous digits (4–8)."""
    digits = re.sub(r"\D", "", raw or "")
    if 4 <= len(digits) <= 8:
        return digits
    return None


def scrub_otp_noise(text: str) -> str:
    """Remove CSS color noise that otherwise looks like OTP digits.

    Only strip hex tokens that contain at least one A–F letter (or were written
    with a leading '#'). Pure digit runs like 123456 must survive.
    """
    text = OTP_CSS_NOISE_RE.sub(" ", text or "")
    text = OTP_HEX_COLOR_RE.sub(" ", text)
    text = OTP_RGB_RE.sub(" ", text)
    # Hex colours without '#' that still include a letter (e.g. leftover ff6600).
    text = re.sub(
        r"(?i)(?<![0-9a-z])(?=[0-9a-f]*[a-f])(?:[0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})(?![0-9a-z])",
        " ",
        text,
    )
    return text


def _otp_looks_like_year(digits: str) -> bool:
    return len(digits) == 4 and 1900 <= int(digits) <= 2100


def extract_otp(text: str, subject: str = "") -> Optional[str]:
    combined = scrub_otp_noise(f"{subject}\n{text}")
    for pattern in OTP_PATTERNS:
        match = pattern.search(combined)
        if match:
            code = normalize_otp_digits(match.group(1))
            if code and not _otp_looks_like_year(code):
                return code
    for raw in OTP_ANY_RE.findall(combined):
        code = normalize_otp_digits(raw)
        if not code or _otp_looks_like_year(code):
            continue
        # Reject candidates that sat next to a hash (CSS) even after scrub.
        return code
    return None


def otp_looks_like_css_false_positive(stored: Optional[str], body_text: str, body_html: str) -> bool:
    """True when stored OTP is likely a scraped CSS colour (#333333 → 333333)."""
    digits = normalize_otp_digits(stored or "") if stored else None
    if not digits:
        return False
    if stored and stored != digits:
        return True
    blob = f"{body_html or ''}\n{body_text or ''}"
    return bool(re.search(rf"(?i)#{digits}\b", blob))


def maybe_repair_otp(conn: Any, row: Row) -> Optional[str]:
    """Re-extract and persist OTP when missing or a CSS false-positive. Returns OTP."""
    subject = row["subject"] or ""
    body_text = row["body_text"] or ""
    body_html = row["body_html"] or ""
    stored = row["otp"]
    needs = (not stored) or otp_looks_like_css_false_positive(stored, body_text, body_html)
    if not needs:
        return stored
    text_src = f"{body_text}\n{html_to_text(body_html)}"
    fresh = extract_otp(text_src, subject)
    if fresh == stored:
        return stored
    conn.execute("UPDATE messages SET otp=? WHERE id=?", (fresh, row["id"]))
    return fresh


IMAGE_PROXY_PREFIX = "https://external-content.duckduckgo.com/iu/?u="


def proxy_remote_image_url(url: str) -> str:
    """Rewrite remote image URLs through DuckDuckGo's image proxy (privacy + hotlink)."""
    url = (url or "").strip()
    if not url:
        return url
    if url.startswith(IMAGE_PROXY_PREFIX):
        return url
    return IMAGE_PROXY_PREFIX + quote(url, safe="")


def safe_url(value: str, *, kind: str, allow_remote_images: bool) -> Optional[str]:
    value = (value or "").strip().replace("\r", "").replace("\n", "")
    if not value:
        return None
    if value.startswith("cid:"):
        return value
    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    if kind == "link":
        if scheme in {"http", "https", "mailto", "tel"}:
            return value
        return None
    if value.startswith("/"):
        return value
    if scheme in {"https", "http"} and allow_remote_images:
        return proxy_remote_image_url(value)
    if scheme == "data" and value.lower().startswith("data:image/"):
        return value
    return None


SAFE_CSS_PROPERTIES = {
    "color", "background", "background-color", "background-image", "background-repeat",
    "background-position", "background-size", "background-clip", "font", "font-family",
    "font-size", "font-weight", "font-style", "font-variant", "line-height", "letter-spacing",
    "text-align", "text-decoration", "text-transform", "text-indent", "white-space",
    "vertical-align", "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
    "border", "border-top", "border-right", "border-bottom", "border-left",
    "border-width", "border-style", "border-color", "border-top-width", "border-right-width",
    "border-bottom-width", "border-left-width", "border-top-style", "border-right-style",
    "border-bottom-style", "border-left-style", "border-top-color", "border-right-color",
    "border-bottom-color", "border-left-color", "border-radius", "border-spacing",
    "border-collapse", "width", "max-width", "min-width", "height", "max-height", "min-height",
    "display", "flex", "flex-direction", "flex-wrap", "flex-grow", "flex-shrink", "flex-basis",
    "justify-content", "align-items", "align-self", "align-content", "gap", "row-gap", "column-gap",
    "float", "clear", "overflow", "overflow-x", "overflow-y", "word-break", "word-wrap",
    "overflow-wrap", "table-layout", "box-sizing", "opacity", "visibility", "list-style",
    "list-style-type", "list-style-position", "position", "top", "right", "bottom", "left",
    "z-index", "direction", "unicode-bidi", "-webkit-text-size-adjust", "mso-line-height-rule",
    "mso-table-lspace", "mso-table-rspace", "mso-padding-alt",
}

_CSS_URL_RE = re.compile(r"(?is)url\s*\(\s*(['\"]?)(.*?)\1\s*\)")


def _sanitize_css_url(raw: str, *, allow_remote_images: bool, cid_map: Optional[dict[str, str]] = None) -> Optional[str]:
    inner = (raw or "").strip().strip("'\"")
    if not inner or any(x in inner.lower() for x in ("javascript:", "vbscript:", "data:text", "expression(")):
        return None
    if inner.lower().startswith("data:image/"):
        return f"url({inner})"
    if inner.lower().startswith("cid:"):
        cid_key = inner[4:].strip("<> \t\r\n").casefold()
        if cid_map:
            found = cid_map.get(cid_key) or cid_map.get(os.path.basename(cid_key))
            if found:
                return f"url('{found}')"
        return None
    clean_inner = inner.strip().lstrip("./\\").casefold()
    if cid_map and clean_inner in cid_map:
        return f"url('{cid_map[clean_inner]}')"
    if allow_remote_images and re.match(r"(?i)^https?://", inner):
        proxied = proxy_remote_image_url(inner)
        return f'url("{html_lib.escape(proxied, quote=True)}")'
    return None


def sanitize_css(style: str, *, allow_remote_images: bool = False, cid_map: Optional[dict[str, str]] = None) -> str:
    style = re.sub(r"/\*.*?\*/", "", style or "", flags=re.S)
    style = re.sub(r"(?is)@import[^;]+;", "", style)
    style = re.sub(r"(?is)(javascript:|expression\s*\(|behavior\s*:)", "", style)
    chunks = []
    for decl in style.split(";"):
        if ":" not in decl:
            continue
        prop, val = decl.split(":", 1)
        prop = prop.strip().lower()
        val = val.strip()
        if prop not in SAFE_CSS_PROPERTIES or not val:
            continue
        lower = val.lower()
        if any(x in lower for x in ("javascript:", "expression(", "@import", "behavior:", "vbscript:")):
            continue

        def _url_sub(match: re.Match) -> str:
            kept = _sanitize_css_url(match.group(2), allow_remote_images=allow_remote_images, cid_map=cid_map)
            return kept or ""

        val = _CSS_URL_RE.sub(_url_sub, val)
        if "url(" in lower and "url(" not in val.lower() and "background" in prop:
            # Drop declarations that only existed for a blocked background image.
            if not val.strip():
                continue
        val = val.replace("<", " ").replace(">", " ").replace("\x00", " ")
        if not val.strip():
            continue
        chunks.append(f"{prop}: {val}")
    return "; ".join(chunks)


def sanitize_stylesheet(css_text: str, *, allow_remote_images: bool = False, cid_map: Optional[dict[str, str]] = None) -> str:
    """Allowlist properties inside a full <style> block (selectors preserved)."""
    css_text = re.sub(r"/\*.*?\*/", "", css_text or "", flags=re.S)
    css_text = re.sub(r"(?is)@import[^;]+;", "", css_text)
    css_text = re.sub(r"(?is)@(?:charset|namespace)[^;]+;", "", css_text)
    out: list[str] = []
    i = 0
    n = len(css_text)
    while i < n:
        start = css_text.find("{", i)
        if start < 0:
            break
        selector = css_text[i:start].strip()
        end = css_text.find("}", start + 1)
        if end < 0:
            break
        body = css_text[start + 1:end]
        i = end + 1
        if not selector or selector.startswith("@"):
            # Drop @media/@font-face etc. for safety and simplicity.
            continue
        cleaned = sanitize_css(body, allow_remote_images=allow_remote_images, cid_map=cid_map)
        if cleaned:
            out.append(f"{selector} {{{cleaned}}}")
    return "\n".join(out)


class _EmailSanitizer(HTMLParser):
    _COMMON = {"class", "id", "style", "dir", "lang", "role", "title"}
    ATTRS = {
        "a": {"href", "target", "rel"} | _COMMON,
        "img": {"src", "alt", "width", "height", "border", "align", "valign", "hspace", "vspace"} | _COMMON,
        "table": {"border", "cellpadding", "cellspacing", "width", "height", "align", "valign", "bgcolor", "background"} | _COMMON,
        "tbody": set(_COMMON), "thead": set(_COMMON), "tfoot": set(_COMMON),
        "tr": {"align", "valign", "bgcolor"} | _COMMON,
        "td": {"colspan", "rowspan", "width", "height", "align", "valign", "bgcolor", "background"} | _COMMON,
        "th": {"colspan", "rowspan", "width", "height", "align", "valign", "bgcolor", "background"} | _COMMON,
        "div": {"align"} | _COMMON, "section": set(_COMMON), "article": set(_COMMON), "main": set(_COMMON),
        "p": {"align"} | _COMMON, "span": set(_COMMON), "center": {"align"} | _COMMON,
        "font": {"color", "face", "size"} | _COMMON,
        "br": set(), "hr": set(_COMMON), "strong": set(_COMMON), "b": set(_COMMON),
        "em": set(_COMMON), "i": set(_COMMON), "u": set(_COMMON), "s": set(_COMMON), "strike": set(_COMMON),
        "blockquote": set(_COMMON), "ul": set(_COMMON), "ol": set(_COMMON), "li": set(_COMMON),
        "h1": set(_COMMON), "h2": set(_COMMON), "h3": set(_COMMON), "h4": set(_COMMON),
        "h5": set(_COMMON), "h6": set(_COMMON),
        "pre": set(_COMMON), "code": set(_COMMON), "small": set(_COMMON), "sup": set(_COMMON), "sub": set(_COMMON),
        "figure": set(_COMMON), "figcaption": set(_COMMON), "dl": set(_COMMON), "dt": set(_COMMON), "dd": set(_COMMON),
        "caption": set(_COMMON), "colgroup": set(_COMMON), "col": {"span", "width"} | _COMMON,
        "tt": set(_COMMON), "mark": set(_COMMON), "abbr": set(_COMMON), "picture": set(_COMMON),
    }
    _SIZE_ATTR = re.compile(r"(?i)^\d{1,5}(?:\.\d{1,2})?(?:%|px)?$")

    def __init__(self, cid_map: dict[str, str], allow_remote_images: bool):
        super().__init__(convert_charrefs=True)
        self.cid_map = cid_map
        self.allow_remote_images = allow_remote_images
        self.out: list[str] = []
        self.skip_depth = 0
        self.style_depth = 0
        self._style_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        if tag in VOID_DANGEROUS_TAGS:
            return
        if tag in CONTAINER_DANGEROUS_TAGS and tag != "style":
            self.skip_depth += 1
            return
        if tag == "style":
            if not self.skip_depth:
                self.style_depth += 1
                self._style_buf = []
            return
        if self.skip_depth or tag not in self.ATTRS:
            return
        attrs_out: list[str] = []
        for key, value in attrs:
            key = key.lower()
            if key.startswith("on") or key in {"srcset", "action", "formaction", "xlink:href", "xmlns", "xmlns:xlink"}:
                continue
            if value is None:
                continue
            value = str(value).strip()
            if key == "style":
                css = sanitize_css(value, allow_remote_images=self.allow_remote_images, cid_map=self.cid_map)
                if css:
                    attrs_out.append(f'style="{html_lib.escape(css, quote=True)}"')
                continue
            if key not in self.ATTRS[tag]:
                continue
            if key == "href":
                v = safe_url(value, kind="link", allow_remote_images=self.allow_remote_images)
                if not v:
                    continue
                value = v
                attrs_out.append('target="_blank"')
                attrs_out.append('rel="noopener noreferrer nofollow"')
            elif key == "src" or (key == "background" and tag in {"table", "td", "th"}):
                if value.lower().startswith("cid:"):
                    cid_key = value[4:].strip("<> \t\r\n").casefold()
                    val = self.cid_map.get(cid_key) or self.cid_map.get(os.path.basename(cid_key))
                    if not val:
                        continue
                    value = val
                else:
                    clean_rel = value.strip().lstrip("./\\").casefold()
                    if clean_rel in self.cid_map:
                        value = self.cid_map[clean_rel]
                    else:
                        v = safe_url(value, kind="image", allow_remote_images=self.allow_remote_images)
                        if not v:
                            continue
                        value = v
            elif key in {"width", "height"}:
                if not self._SIZE_ATTR.fullmatch(value):
                    continue
            elif key in {"colspan", "rowspan", "border", "cellpadding", "cellspacing", "span", "hspace", "vspace"}:
                if not re.fullmatch(r"\d{1,5}", value):
                    continue
            attrs_out.append(f'{key}="{html_lib.escape(value, quote=True)}"')
        attr_s = " " + " ".join(attrs_out) if attrs_out else ""
        self.out.append(f"<{tag}{attr_s}>")

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag == "style":
            if self.style_depth:
                self.style_depth -= 1
                cleaned = sanitize_stylesheet(
                    "".join(self._style_buf), allow_remote_images=self.allow_remote_images, cid_map=self.cid_map
                )
                self._style_buf = []
                if cleaned:
                    self.out.append(f"<style>{cleaned}</style>")
            return
        if tag in CONTAINER_DANGEROUS_TAGS:
            if self.skip_depth:
                self.skip_depth -= 1
            return
        if self.skip_depth or tag not in self.ATTRS or tag in VOID_TAGS:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data: str):
        if self.skip_depth:
            return
        if self.style_depth:
            self._style_buf.append(data)
        else:
            self.out.append(html_lib.escape(data))

    def output(self) -> str:
        return "".join(self.out)


def sanitize_email_html(raw_html: str, cid_map: dict[str, str], allow_remote_images: bool) -> str:
    parser = _EmailSanitizer(cid_map, allow_remote_images)
    parser.feed(raw_html or "")
    parser.close()
    return parser.output()


def _decode_part_data(part: Any, data: bytes) -> str:
    if not data:
        return ""
    try:
        content = part.get_content()
        if isinstance(content, str):
            return content
    except Exception:
        pass

    candidates: list[str] = []
    c = part.get_content_charset()
    if c:
        candidates.append(c.strip().strip("'\""))
    ct = str(part.get("Content-Type") or "")
    m = re.search(r'(?i)charset\s*=\s*["\']?([a-zA-Z0-9_\-]+)', ct)
    if m:
        c_ct = m.group(1).strip()
        if c_ct not in candidates:
            candidates.append(c_ct)

    for enc in candidates:
        for alias in (enc, enc.replace("-", "_"), enc.replace("_", "-")):
            try:
                return data.decode(alias)
            except (LookupError, UnicodeDecodeError):
                pass

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass

    for enc in ("windows-1252", "iso-8859-1", "windows-1258", "latin-1"):
        try:
            return data.decode(enc)
        except (LookupError, UnicodeDecodeError):
            pass

    return data.decode("utf-8", errors="replace")


def parse_email(raw: bytes, fallback_subject: str = "") -> dict[str, Any]:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    raw_sub = msg.get("subject")
    subject = str(raw_sub or fallback_subject or "")
    if raw_sub and "=?" in subject:
        try:
            chunks = email.header.decode_header(subject)
            out_parts = []
            for text, enc in chunks:
                if isinstance(text, bytes):
                    out_parts.append(text.decode(enc or "utf-8", errors="replace"))
                else:
                    out_parts.append(str(text))
            subject = "".join(out_parts)
        except Exception:
            pass

    message_id = str(msg.get("message-id") or "")
    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[dict[str, Any]] = []
    attachment_total = 0

    if msg.is_multipart():
        parts = list(msg.walk())
    else:
        parts = [msg]

    for part in parts:
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        disp = (part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        cid = str(part.get("Content-ID") or "").strip() or None
        location = str(part.get("Content-Location") or "").strip() or None
        try:
            data = part.get_payload(decode=True) or b""
        except Exception:
            data = b""

        is_text_body = False
        is_html_body = False
        if ctype == "text/plain":
            if not filename and "attachment" not in disp:
                is_text_body = True
            elif filename and filename.lower() in {"body.txt", "message.txt", "email.txt", "text.txt"} and "attachment" not in disp:
                is_text_body = True
        elif ctype == "text/html":
            if not filename and "attachment" not in disp:
                is_html_body = True
            elif filename and filename.lower() in {"body.html", "message.html", "email.html", "index.html", "text.html", "content.html"} and "attachment" not in disp:
                is_html_body = True

        if is_text_body:
            decoded = _decode_part_data(part, data)
            text_parts.append(str(decoded))
            continue

        if is_html_body:
            decoded = _decode_part_data(part, data)
            html_parts.append(str(decoded))
            continue

        if not filename and ctype.startswith("image/") and cid:
            filename = "inline-image"

        if filename or "attachment" in disp or cid:
            if len(data) > MAX_ATTACHMENT_BYTES or attachment_total + len(data) > MAX_ATTACHMENTS_TOTAL:
                continue
            attachment_total += len(data)
            clean_cid = str(cid).strip("<> \t\r\n") if cid else None
            obj = {
                "filename": clean_text(filename or "attachment", 255),
                "content_type": clean_text(ctype or "application/octet-stream", 180),
                "disposition": clean_text(disp, 100),
                "content_id": clean_cid,
                "content_location": location,
                "size_bytes": len(data),
                "data": data,
            }
            attachments.append(obj)

    body_text = "\n\n".join(x for x in text_parts if x).strip()
    body_html = "\n<hr>\n".join(x for x in html_parts if x).strip()
    if not body_text and body_html:
        body_text = html_to_text(body_html)
    body_text = body_text[:MAX_BODY_CHARS]
    body_html = body_html[:MAX_BODY_CHARS * 3]
    otp = extract_otp(f"{body_text}\n{html_to_text(body_html)}", subject)
    return {
        "subject": subject[:1000],
        "message_id": message_id[:1000],
        "body_text": body_text,
        "body_html": body_html,
        "otp": otp,
        "attachments": attachments,
    }


# ============================================================
# Sessions / auth
# ============================================================


class Auth(NamedTuple):
    """Resolved request identity.

    Unpacks as the historical `(user, session)` pair. `session` is `None` when
    the caller authenticated with an API key (CSRF does not apply).
    """

    user: Row
    session: Optional[Row]
    via: str = "session"  # "session" | "api_key"

    def __iter__(self):
        return iter((self.user, self.session))


def session_from_request(request: Request) -> Optional[Auth]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    with db() as conn:
        row = conn.execute(
            """SELECT s.id AS session_id,s.csrf_token,s.user_id AS session_user_id,s.expires_at AS session_expires_at,s.created_at AS session_created_at, u.*
               FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?""", (hash_token(token),)
        ).fetchone()
        if not row:
            return None
        try:
            expired = datetime.fromisoformat(row["session_expires_at"]) <= utc_now()
        except Exception:
            expired = True
        if expired or not row["is_active"]:
            conn.execute("DELETE FROM sessions WHERE id=?",(row["session_id"],))
            return None
        return Auth(row, row, "session")


def issue_session(conn: sqlite3.Connection, user_id: int) -> tuple[str,str,str]:
    """Create a session row. Returns (token, csrf, expires_at_iso)."""
    token = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    expires = (utc_now() + timedelta(days=SESSION_DAYS)).isoformat()
    conn.execute(
        "INSERT INTO sessions(token_hash,csrf_token,user_id,expires_at,created_at) VALUES(?,?,?,?,?)",
        (hash_token(token), csrf, user_id, expires, now_iso()),
    )
    return token, csrf, expires


def api_key_user(request: Request) -> Row:
    """Resolve `Authorization: Bearer sk-otp-…`. Raises 401/429 on failure."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Valid API key required")
    key = auth[7:].strip()
    if not key.startswith("sk-otp-") or len(key) < 20:
        raise HTTPException(status_code=401, detail="Valid API key required")
    prefix = key[:16]
    key_fp = hash_token(key)

    cached = API_KEY_CACHE.get(prefix)
    if cached and cached.get("key_fp") == key_fp and cached.get("user"):
        user = cached["user"]
        allowed, _remaining, retry_after = API_KEY_LIMITER.hit(
            f"api:{user['id']}", API_KEY_MAX_REQUESTS, API_KEY_WINDOW_SECONDS
        )
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"API rate limit exceeded. Try again in {retry_after}s.",
                headers={"Retry-After": str(retry_after)},
            )
        return user

    with db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE api_key_prefix=? AND is_active=1", (prefix,)
        ).fetchone()
        if user and verify_secret(key, user["api_key_hash"]):
            # Freeze a plain dict so later cache hits don't depend on a live cursor.
            frozen = dict(user)
            API_KEY_CACHE.set(prefix, {"key_fp": key_fp, "user": frozen})
            allowed, _remaining, retry_after = API_KEY_LIMITER.hit(
                f"api:{frozen['id']}", API_KEY_MAX_REQUESTS, API_KEY_WINDOW_SECONDS
            )
            if not allowed:
                raise HTTPException(
                    status_code=429,
                    detail=f"API rate limit exceeded. Try again in {retry_after}s.",
                    headers={"Retry-After": str(retry_after)},
                )
            return frozen
    raise HTTPException(status_code=401, detail="Valid API key required")


def require_auth(request: Request) -> Auth:
    """Accept session cookie **or** Bearer API key (integration-first).

    Bearer takes precedence when both are present. Login/register/logout/ingest
    stay on their own auth mechanisms and do not call this helper.
    """
    if (request.headers.get("Authorization") or "").startswith("Bearer "):
        return Auth(api_key_user(request), None, "api_key")
    result = session_from_request(request)
    if not result:
        raise HTTPException(status_code=401, detail="Authentication required")
    return result


def require_session(request: Request) -> Auth:
    """Backward-compatible alias for require_auth (session or API key)."""
    return require_auth(request)


def require_admin(request: Request) -> Auth:
    result = require_auth(request)
    if result.user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return result


def require_csrf(request: Request, session: Optional[Row]) -> None:
    """Enforce CSRF for cookie sessions only. API key callers skip CSRF."""
    if session is None:
        return
    if (request.headers.get("Authorization") or "").startswith("Bearer "):
        return
    supplied = request.headers.get("X-CSRF-Token", "")
    if not supplied or not hmac.compare_digest(supplied, session["csrf_token"]):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


# ============================================================
# Dashboard data helpers
# ============================================================


def mailbox_summaries(conn: sqlite3.Connection, user: Row, mailboxes: list[Row]) -> list[dict[str,Any]]:
    """Build every mailbox summary in a fixed number of queries.

    The previous implementation issued four queries per mailbox, so a user with
    N mailboxes cost 4N+ round trips on every dashboard load. Here the counts,
    the latest message and the access list are each fetched once for the whole
    set and then joined in Python, which keeps the cost constant as N grows.

    `mailboxes` must be the already access-filtered list for this user.
    """
    if not mailboxes:
        return []

    ids = [m["id"] for m in mailboxes]
    owner_id = user["id"]

    def _chunk_list(items: list[Any], chunk_size: int = 400) -> list[list[Any]]:
        return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]

    # owners: one map instead of a lookup per mailbox
    owner_ids = list({m["owner_user_id"] for m in mailboxes if m["owner_user_id"]})
    owner_names: dict[int, str] = {}
    if owner_ids:
        for chunk in _chunk_list(owner_ids, 400):
            q = ",".join("?" * len(chunk))
            for r in conn.execute(f"SELECT id,username FROM users WHERE id IN ({q})", chunk):
                owner_names[r["id"]] = r["username"]

    # totals
    totals: dict[int, int] = {mid: 0 for mid in ids}
    for chunk in _chunk_list(ids, 400):
        q = ",".join("?" * len(chunk))
        for r in conn.execute(f"SELECT mailbox_id,COUNT(*) c FROM messages WHERE mailbox_id IN ({q}) GROUP BY mailbox_id", chunk):
            totals[r["mailbox_id"]] = r["c"]

    # unread, always scoped to this user so a shared mailbox shows their own state
    unread: dict[int, int] = {mid: 0 for mid in ids}
    for chunk in _chunk_list(ids, 400):
        q = ",".join("?" * len(chunk))
        for r in conn.execute(
            f"""SELECT mailbox_id,COUNT(*) c FROM messages WHERE mailbox_id IN ({q})
                AND NOT EXISTS(SELECT 1 FROM message_reads r WHERE r.message_id=messages.id AND r.user_id=?)
                GROUP BY mailbox_id""", chunk + [owner_id]):
            unread[r["mailbox_id"]] = r["c"]

    # latest message per mailbox, using the existing (mailbox_id, received_at DESC, id DESC) index
    latest: dict[int, dict[str,Any]] = {}
    for chunk in _chunk_list(ids, 400):
        q = ",".join("?" * len(chunk))
        for r in conn.execute(
            f"""SELECT id,mailbox_id,subject,envelope_from,received_at,otp,has_attachments
                FROM messages WHERE id IN (
                    SELECT MAX(id) FROM messages WHERE mailbox_id IN ({q})
                    GROUP BY mailbox_id
                )""", chunk):
            latest[r["mailbox_id"]] = dict(r)

    # access list for every mailbox at once
    access: dict[int, list[dict[str,Any]]] = {mid: [] for mid in ids}
    for chunk in _chunk_list(ids, 400):
        q = ",".join("?" * len(chunk))
        for r in conn.execute(
            f"""SELECT a.mailbox_id,u.public_id,u.username,u.role,a.source,a.rule_id,
                       CASE WHEN u.id=(SELECT owner_user_id FROM mailboxes m2 WHERE m2.id=a.mailbox_id) THEN 1 ELSE 0 END is_owner
                FROM mailbox_access a JOIN users u ON u.id=a.user_id
                WHERE a.mailbox_id IN ({q})
                ORDER BY is_owner DESC,u.username""", chunk):
            access[r["mailbox_id"]].append({
                "public_id": r["public_id"], "username": r["username"], "role": r["role"],
                "source": r["source"], "rule_id": r["rule_id"],
            })

    return [
        {
            "id": m["id"],
            "address": m["address"],
            "owner_username": owner_names.get(m["owner_user_id"]) if m["owner_user_id"] else None,
            "owner_user_id": m["owner_user_id"],
            "exact_override": bool(m["exact_override"]),
            "override_reason": m["override_reason"],
            "is_owner": m["owner_user_id"] == owner_id,
            "users": access[m["id"]],
            "message_count": totals.get(m["id"], 0),
            "unread_count": unread.get(m["id"], 0),
            "latest": latest.get(m["id"]),
        }
        for m in mailboxes
    ]


def visible_mailboxes(conn: sqlite3.Connection, user: Row) -> list[Row]:
    """Mailboxes this user is allowed to see, ordered for stable display."""
    if user["role"] == "admin":
        return conn.execute("SELECT m.* FROM mailboxes m ORDER BY m.address").fetchall()
    return conn.execute(
        """SELECT m.* FROM mailboxes m
           JOIN mailbox_access a ON a.mailbox_id=m.id AND a.user_id=?
           ORDER BY m.address""", (user["id"],)
    ).fetchall()


# ============================================================
# Startup / static assets
# ============================================================

init_db()

if STATIC_DIR.is_dir():
    # No build step, so no content hashes in the filenames. ASSET_VERSION is
    # stamped into the URLs by index.html and must be bumped with every deploy
    # that changes an asset, otherwise browsers keep serving the stale copy.
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def read_index() -> str:
    """Serve index.html with a content-derived cache buster on the assets.

    There is no bundler, so the asset filenames never change. Without a
    version query string a browser can keep serving the previous app.css after
    a deploy, which looks exactly like "the dark mode fix did not apply". The
    hash is derived from the asset bytes, so it changes whenever they do and
    costs nothing to maintain.
    """
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return html.replace("__ASSET_VERSION__", asset_version())


def asset_version() -> str:
    digest = hashlib.sha256()
    for name in ("app.css", "app.js"):
        path = STATIC_DIR / name
        if path.exists():
            digest.update(path.read_bytes())
    override = os.getenv("ASSET_VERSION", "").strip()
    return override or digest.hexdigest()[:12]


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(read_index(), headers={"Cache-Control": "no-store"})


@app.api_route("/health", methods=["GET", "HEAD"])
async def health() -> dict[str, Any]:
    with db() as conn:
        return {
            "status": "ok",
            "app": APP_NAME,
            "version": asset_version(),
            "search": "fts" if FTS_AVAILABLE else "like",
            "registration": registration_open(conn),
            "code_generation": code_generation_open(conn),
        }


@app.api_route("/ping", methods=["GET", "HEAD"])
async def ping() -> dict[str, str]:
    return {"status": "ok", "ping": "pong"}


# -------------------------- auth -----------------------------

# One generic message for every credential failure, so the response cannot be
# used to tell "no such user" from "wrong password" from "disabled account".
BAD_CREDENTIALS = "Invalid username or password"
PASSWORD_RULE = "Password must be at least 10 characters"
USERNAME_RULE = "Username must be 3-40 chars: letters, numbers, _, ., -"


def validate_credentials(username: str, password: str) -> tuple[str, str]:
    username = str(username or "").strip()
    password = str(password or "")
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")
    if not USERNAME_RE.fullmatch(username):
        raise HTTPException(status_code=400, detail=USERNAME_RULE)
    if len(password) < 10:
        raise HTTPException(status_code=400, detail=PASSWORD_RULE)
    if len(password) > 200:
        raise HTTPException(status_code=400, detail="Password is too long")
    return username, password


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_DAYS * 86400, **SESSION_COOKIE_KWARGS)


def clear_session_cookie(response: Response) -> None:
    # The attributes must match the ones used when setting, or the browser
    # keeps a same-named cookie scoped differently and the session survives.
    response.delete_cookie(SESSION_COOKIE, **SESSION_COOKIE_KWARGS)


async def read_json_object(request: Request, limit: int = 64 * 1024) -> dict[str,Any]:
    """Parse a JSON object body, tolerating an empty or non-JSON body.

    Keeps request parsing in one place so every endpoint treats a missing body
    the same way instead of raising an opaque 422.
    """
    raw = await request.body()
    if not raw or len(raw) > limit:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


@app.post("/api/login")
async def login(request: Request, payload: dict[str,Any], response: Response) -> dict[str,Any]:
    username, password = validate_credentials(payload.get("username"), payload.get("password"))
    limit_key = f"login:{client_ip(request)}"
    allowed, remaining, retry_after = LOGIN_LIMITER.hit(limit_key, LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW_SECONDS)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Too many login attempts. Try again in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )
    with db() as conn:
        user = conn.execute("SELECT * FROM users WHERE username=?",(username,)).fetchone()
        if not user or not user["is_active"] or not verify_secret(password,user["password_hash"]):
            raise HTTPException(status_code=401,detail=BAD_CREDENTIALS)
        # Only a real success clears the bucket; failures keep accumulating.
        LOGIN_LIMITER.clear(limit_key)
        token,csrf,_ = issue_session(conn, user["id"])
    set_session_cookie(response, token)
    return {"user":user_row(user),"csrf":csrf}


@app.post("/api/logout")
async def logout(request: Request,response: Response) -> dict[str,bool]:
    token=request.cookies.get(SESSION_COOKIE)
    if token:
        with db() as conn: conn.execute("DELETE FROM sessions WHERE token_hash=?",(hash_token(token),))
    clear_session_cookie(response)
    return {"ok":True}


@app.get("/api/me")
async def me(request: Request) -> dict[str,Any]:
    user,session=require_session(request)
    with db() as conn:
        referral = user_referral_summary(conn, user)
        registration_open_now = registration_open(conn)
    return {
        "user": user_row(user),
        "csrf": session["csrf_token"] if session else None,
        "auth_via": "api_key" if session is None else "session",
        "referral": referral,
        "registration_open": registration_open_now,
    }


@app.get("/api/auth/config")
async def auth_config() -> dict[str,Any]:
    """Public bootstrap for the login screen.

    Lets the UI show the register link only when registration is actually
    reachable, instead of failing after the user has already typed a code.
    """
    with db() as conn:
        return {
            "registration_open": registration_open(conn),
            "app": APP_NAME,
        }


@app.post("/api/register")
async def register(request: Request,payload: dict[str,Any],response: Response) -> dict[str,Any]:
    """Self-service signup, gated on a valid referral code.

    Deliberately narrow: a registered account has no mailbox access and no code
    generation permission, so a leaked or brute-forced code cannot be escalated
    into access to mail. Granting mailboxes stays an admin/routing-rule action.
    """
    username, password = validate_credentials(payload.get("username"), payload.get("password"))
    raw_code = str(payload.get("referral_code") or "")

    limit_key = f"register:{client_ip(request)}"
    allowed, remaining, retry_after = REGISTER_LIMITER.hit(
        limit_key, REGISTER_MAX_ATTEMPTS, REGISTER_WINDOW_SECONDS)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Too many registration attempts. Try again in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )

    with db() as conn:
        if not registration_open(conn):
            raise HTTPException(status_code=403,detail="Self-registration is currently disabled")
        if not raw_code:
            raise HTTPException(status_code=400,detail="A referral code is required")
        code_row = redeem_referral_code(conn, raw_code)
        # One generic rejection for unknown, disabled, expired and exhausted
        # codes alike, so an attacker cannot probe which condition applied.
        if not code_row:
            raise HTTPException(status_code=400,detail="That referral code is not valid")
        if conn.execute("SELECT 1 FROM users WHERE username=?",(username,)).fetchone():
            raise HTTPException(status_code=409,detail="Username already exists")

        public_id = generate_public_id("U")
        key = generate_api_key()
        created = now_iso()
        ip = client_ip(request)
        cur = conn.execute(
            """INSERT INTO users(public_id,username,password_hash,role,api_key_hash,api_key_prefix,
                                 is_active,can_generate_codes,referred_by,created_via,created_ip,created_at,updated_at)
               VALUES(?,?,?,'user',?,?,1,0,?,'referral',?,?,?)""",
            (public_id,username,hash_secret(password),hash_secret(key),key[:16],
             code_row["owner_user_id"],ip,created,created),
        )
        user_id = cur.lastrowid
        conn.execute(
            "UPDATE referral_codes SET uses=uses+1 WHERE id=?", (code_row["id"],)
        )
        conn.execute(
            """INSERT INTO registrations(user_id,referral_code_id,code_fingerprint,ip,user_agent,created_at)
               VALUES(?,?,?,?,?,?)""",
            (user_id, code_row["id"], code_row["code_fingerprint"], ip,
             clean_text(request.headers.get("user-agent", ""), 300), created),
        )
        add_audit(conn,None,"user.registered",None,user_id,
                  f"Self-registration for {username} ({public_id}) via code #{code_row['id']}")
        if code_row["owner_user_id"]:
            add_notification(conn,code_row["owner_user_id"],"referral","Referral used",
                             f"{username} registered with your referral code.",None)
        user = conn.execute("SELECT * FROM users WHERE id=?",(user_id,)).fetchone()
        # Log the new account straight in; the UI goes directly to the inbox.
        token,csrf,_ = issue_session(conn, user_id)
        referral = user_referral_summary(conn, user)
    set_session_cookie(response, token)
    return {"user":user_row(user),"csrf":csrf,"referral":referral}


@app.get("/api/referrals/me")
async def referral_me(request: Request) -> dict[str,Any]:
    user,session=require_session(request)
    with db() as conn:
        return user_referral_summary(conn, user)


@app.post("/api/referrals/code")
async def referral_generate(request: Request) -> dict[str,Any]:
    """Issue (or replace) the caller's own referral code.

    The plaintext is returned exactly once, in this response. It is never
    stored, so it cannot be displayed again; the caller regenerates instead.
    """
    user,session=require_session(request);require_csrf(request,session)
    body = await read_json_object(request)

    limit_key = f"code:{user['id']}"
    allowed, remaining, retry_after = CODE_GENERATE_LIMITER.hit(limit_key, 5, 3600)
    if not allowed:
        raise HTTPException(status_code=429,
                            detail=f"Too many codes generated. Try again in {retry_after}s.",
                            headers={"Retry-After": str(retry_after)})
    with db() as conn:
        blocked = referral_block_reason(conn, user)
        if blocked:
            raise HTTPException(status_code=403, detail=blocked)
        max_uses = max(1, min(int(body.get("max_uses") or REFERRAL_MAX_USES_DEFAULT), 100))
        # Replacing keeps exactly one usable personal code, so a shared or
        # leaked code can be retired by regenerating.
        conn.execute("UPDATE referral_codes SET is_active=0 WHERE owner_user_id=?", (user["id"],))
        row,code = create_referral_code(conn, user["id"], user["id"], max_uses,
                                        code_expiry_iso(), "self-service")
        add_audit(conn,user["id"],"referral.code_generated",None,user["id"],
                  f"Generated referral code #{row['id']} (max_uses={max_uses})")
    return {"code":code,"id":row["id"],"max_uses":row["max_uses"],
            "expires_at":row["expires_at"],"warning":"This code is shown only once."}


@app.get("/api/me/referrals/code-history")
async def referral_code_history(request: Request) -> dict[str,Any]:
    user,session=require_session(request)
    with db() as conn:
        rows=conn.execute(
            """SELECT id,code_fingerprint,max_uses,uses,is_active,expires_at,note,created_at
               FROM referral_codes WHERE owner_user_id=? ORDER BY created_at DESC,id DESC""",
            (user["id"],),
        ).fetchall()
        return {"items":[{**dict(r),"status":code_status(r)} for r in rows]}


@app.post("/api/me/regenerate-key")
async def regenerate_key(request: Request) -> dict[str,str]:
    user,session=require_session(request);require_csrf(request,session)
    key=generate_api_key()
    old_prefix = user["api_key_prefix"]
    with db() as conn:
        conn.execute("UPDATE users SET api_key_hash=?,api_key_prefix=?,updated_at=? WHERE id=?",(hash_secret(key),key[:16],now_iso(),user["id"]))
        add_audit(conn,user["id"],"user.api_key_rotated",None,None,"API key rotated")
    invalidate_api_key_cache(old_prefix)
    invalidate_api_key_cache(key[:16])
    return {"api_key":key}


@app.post("/api/me/password")
async def change_password(request: Request,payload: dict[str,Any]) -> dict[str,bool]:
    user,session=require_session(request);require_csrf(request,session)
    oldp=str(payload.get("old_password") or "");newp=str(payload.get("new_password") or "")
    if len(newp)<10: raise HTTPException(status_code=400,detail=PASSWORD_RULE)
    with db() as conn:
        current=conn.execute("SELECT password_hash FROM users WHERE id=?",(user["id"],)).fetchone()
        if not current or not verify_secret(oldp,current["password_hash"]): raise HTTPException(status_code=400,detail="Current password is incorrect")
        conn.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?",(hash_secret(newp),now_iso(),user["id"]))
        conn.execute("DELETE FROM sessions WHERE user_id=?",(user["id"],))
    return {"ok":True}


@app.get("/api/users/lookup")
async def lookup_user(request: Request,public_id: str) -> dict[str,Any]:
    require_session(request)
    with db() as conn:
        u=user_by_public_id(conn,public_id)
        if not u or not u["is_active"]: raise HTTPException(status_code=404,detail="User ID not found")
        return {"public_id":u["public_id"],"username":u["username"],"role":u["role"]}


# ------------------------ ingest -----------------------------

@app.post("/api/ingest")
async def ingest(request: Request) -> JSONResponse:
    supplied=request.headers.get("X-Worker-Token","")
    if not WORKER_INGEST_TOKEN or not hmac.compare_digest(supplied,WORKER_INGEST_TOKEN):
        raise HTTPException(status_code=401,detail="Invalid worker token")
    try: content_length=int(request.headers.get("content-length","0"))
    except ValueError: content_length=0
    if content_length and content_length>MAX_EMAIL_BYTES: raise HTTPException(status_code=413,detail="Email too large")
    chunks=[];total=0
    async for chunk in request.stream():
        total+=len(chunk)
        if total>MAX_EMAIL_BYTES: raise HTTPException(status_code=413,detail="Email too large")
        chunks.append(chunk)
    raw=b"".join(chunks)
    envelope_from=clean_text(request.headers.get("X-Envelope-From",""),320) or "unknown@unknown.invalid"
    envelope_to=normalize_email(request.headers.get("X-Envelope-To",""))
    if not valid_email(envelope_to): raise HTTPException(status_code=400,detail="Invalid envelope recipient")
    fallback_subject=clean_text(request.headers.get("X-Email-Subject",""),1000)
    worker_message_id=clean_text(request.headers.get("X-Email-Message-ID",""),1000)
    date_header=clean_text(request.headers.get("X-Email-Date",""),1000)
    digest=hashlib.sha256(raw).hexdigest()
    parsed=parse_email(raw,fallback_subject)
    received_at=parse_received_date(date_header)

    with db() as conn:
        mailbox=get_or_create_mailbox(conn,envelope_to)
        if not mailbox["exact_override"]:
            sync_mailbox_rules(conn,mailbox,notify=True)
        # If mailbox has no owner and no access, check if a default catch-all fallback user is configured
        has_access_row = conn.execute("SELECT 1 FROM mailbox_access WHERE mailbox_id=?", (mailbox["id"],)).fetchone()
        if not has_access_row and not mailbox["owner_user_id"]:
            fallback_pid = get_setting(conn, "default_catch_all_user", "").strip().upper()
            if fallback_pid:
                fallback_user = user_by_public_id(conn, fallback_pid)
                if fallback_user and fallback_user["is_active"]:
                    conn.execute(
                        "INSERT INTO mailbox_access(mailbox_id,user_id,source,rule_id,granted_by,granted_at) VALUES(?,?,?,?,?,?)",
                        (mailbox["id"], fallback_user["id"], "rule", None, None, now_iso()),
                    )
                    conn.execute("UPDATE mailboxes SET owner_user_id=?, updated_at=? WHERE id=?", (fallback_user["id"], now_iso(), mailbox["id"]))
                    MAILBOX_ADDR_CACHE.invalidate(mailbox["address"])
                    invalidate_access_caches(fallback_user["id"])
                    add_notification(
                        conn, fallback_user["id"], "routing",
                        "Mailbox added by default catch-all",
                        f"{mailbox['address']} đã được tự động đưa vào inbox qua cấu hình default catch-all.",
                        mailbox["id"],
                    )
        duplicate=conn.execute("SELECT id FROM messages WHERE mailbox_id=? AND raw_sha256=?",(mailbox["id"],digest)).fetchone()
        if duplicate:
            return JSONResponse({"ok":True,"duplicate":True,"message_id":duplicate["id"],"mailbox":envelope_to})
        message_id = insert_message_row(
            conn,
            mailbox_id=mailbox["id"],
            envelope_from=envelope_from,
            envelope_to=envelope_to,
            subject=parsed["subject"] or fallback_subject or "(no subject)",
            message_id_hdr=parsed["message_id"] or worker_message_id or None,
            received_at=received_at,
            otp=parsed["otp"],
            body_text=parsed["body_text"],
            body_html=parsed["body_html"],
            raw=raw,
            size_bytes=len(raw),
            digest=digest,
            has_attachments=1 if parsed["attachments"] else 0,
        )
        if message_id is None:
            existing=conn.execute("SELECT id FROM messages WHERE mailbox_id=? AND raw_sha256=?",(mailbox["id"],digest)).fetchone()
            return JSONResponse({"ok":True,"duplicate":True,"message_id":existing["id"] if existing else None,"mailbox":envelope_to})
        subject=parsed["subject"] or fallback_subject or "(no subject)"
        index_message(conn,message_id,subject,envelope_from,envelope_to,parsed["otp"],parsed["body_text"])
        for a in parsed["attachments"]:
            att_data = a["data"] if STORE_ATTACHMENTS else (a["data"] if (a["content_id"] and len(a["data"]) <= 256 * 1024) else None)
            conn.execute("""INSERT INTO attachments(message_id,filename,content_type,disposition,content_id,content_location,size_bytes,data)
                            VALUES(?,?,?,?,?,?,?,?)""",(message_id,a["filename"],a["content_type"],a["disposition"],a["content_id"],a["content_location"],a["size_bytes"],att_data))
        users=conn.execute("SELECT user_id FROM mailbox_access WHERE mailbox_id=? AND user_id IN (SELECT id FROM users WHERE is_active=1)",(mailbox["id"],)).fetchall()
        for u in users:
            add_notification(conn,u["user_id"],"email","Email mới",f"{envelope_to}: {parsed['subject'] or '(no subject)'}",mailbox["id"])
        remember_latest_message(
            mailbox["id"], message_id=message_id, address=envelope_to,
            received_at=received_at, otp=parsed["otp"], subject=subject,
        )
    return JSONResponse({"ok":True,"message_id":message_id,"mailbox":envelope_to,"otp":parsed["otp"]},status_code=201)


# ----------------------- dashboard ---------------------------

@app.get("/api/dashboard")
async def dashboard(request: Request) -> dict[str,Any]:
    user,_=require_session(request)
    with db() as conn:
        visible=visible_mailboxes(conn,user)
        mailboxes=mailbox_summaries(conn,user,visible)
        notifs=conn.execute("SELECT id,kind,title,body,mailbox_id,created_at,read_at FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 100",(user["id"],)).fetchall()
        data:dict[str,Any]={"mailboxes":mailboxes,"notifications":[dict(x) for x in notifs],"rules":[],"users":[],"audit":[]}
        if user["role"]=="admin":
            data["users"]=[user_row(x) for x in conn.execute("SELECT * FROM users ORDER BY CASE WHEN role='admin' THEN 0 ELSE 1 END,username").fetchall()]
            cached_rules = RULES_CACHE.get("dashboard_rules")
            if cached_rules is not None:
                data["rules"] = cached_rules
            else:
                rule_rows=conn.execute("""SELECT r.*,u.username target_username,u.public_id target_public_id,cu.username creator_username
                                           FROM routing_rules r JOIN users u ON u.id=r.target_user_id LEFT JOIN users cu ON cu.id=r.created_by
                                           ORDER BY r.updated_at DESC,r.id DESC""").fetchall()
                candidates=[m["address"] for m in conn.execute("SELECT address FROM mailboxes WHERE exact_override=0").fetchall()]
                computed_rules=[{**dict(rr),"match_mailboxes":sum(1 for a in candidates if rule_matches(rr,a))} for rr in rule_rows]
                RULES_CACHE.set("dashboard_rules", computed_rules)
                data["rules"] = computed_rules
            data["audit"]=[dict(x) for x in conn.execute("""SELECT a.*,au.username actor_username,tu.username target_username,m.address mailbox_address
                                                             FROM audit_logs a LEFT JOIN users au ON au.id=a.actor_user_id
                                                             LEFT JOIN users tu ON tu.id=a.target_user_id LEFT JOIN mailboxes m ON m.id=a.mailbox_id
                                                             ORDER BY a.created_at DESC,a.id DESC LIMIT 300""").fetchall()]
            data["referral_stats"]={
                "registration_enabled":registration_open(conn),
                "code_generation_enabled":code_generation_open(conn),
                "codes":conn.execute("SELECT COUNT(*) c FROM referral_codes").fetchone()["c"],
                "uses":conn.execute("SELECT CAST(COALESCE(SUM(uses),0) AS INTEGER) c FROM referral_codes").fetchone()["c"],
                "registrations":conn.execute("SELECT COUNT(*) c FROM registrations").fetchone()["c"],
                "permitted_users":conn.execute("SELECT COUNT(*) c FROM users WHERE can_generate_codes=1 AND is_active=1").fetchone()["c"],
            }
        data["domains"] = list_domains_for_user(conn, user)
        if user["role"] == "admin":
            data["pending_requests_count"] = conn.execute("SELECT COUNT(*) c FROM mailbox_requests WHERE status='pending'").fetchone()["c"]
        else:
            data["my_requests_count"] = conn.execute("SELECT COUNT(*) c FROM mailbox_requests WHERE user_id=? AND status='pending'", (user["id"],)).fetchone()["c"]
        data["referral"]=user_referral_summary(conn,user)
        return data


# ------------------------- messages --------------------------

@app.get("/api/messages")
async def list_messages(request: Request,mailbox_id: int=0,page: int=1,page_size: int=30,q: str="",
                        unread: int=0,otp_only: int=0,scope: str="all") -> dict[str,Any]:
    """List / search messages.

    `scope=headers` searches subject, sender, recipient and OTP only, which is
    index-friendly and fast. `scope=all` (the default) also searches the body;
    that goes through the FTS index when the SQLite build provides it and falls
    back to a LIKE scan otherwise.
    """
    user,_=require_session(request);page=max(1,page);page_size=max(1,min(page_size,100));offset=(page-1)*page_size
    query=q.strip()[:MAX_SEARCH_QUERY]
    with db() as conn:
        params:list[Any]=[];where:list[str]=[];joins=""
        if mailbox_id:
            mailbox=mailbox_by_id(conn,mailbox_id)
            if not mailbox or not has_access(conn,user,mailbox_id): raise HTTPException(status_code=404,detail="Mailbox not found")
            where.append("msg.mailbox_id=?");params.append(mailbox_id)
        elif user["role"]!='admin':
            joins=" JOIN mailbox_access ma ON ma.mailbox_id=msg.mailbox_id AND ma.user_id=? ";params.append(user["id"])
        if query:
            # The clause is built per backend: a tsvector match on PostgreSQL, an
            # FTS5 MATCH on SQLite, and a LIKE scan when neither index applies.
            clause, search_params = dbm.build_search_clause(query, scope, "msg")
            if clause:
                where.append(clause)
                params += search_params
        if unread:
            where.append("NOT EXISTS(SELECT 1 FROM message_reads rr WHERE rr.message_id=msg.id AND rr.user_id=?)");params.append(user["id"])
        if otp_only: where.append("COALESCE(msg.otp,'')<>''")
        clause=" AND ".join(where) if where else "1=1"
        total=conn.execute(f"SELECT COUNT(*) c FROM messages msg{joins} WHERE {clause}",params).fetchone()["c"]
        rows=conn.execute(
            f"""SELECT msg.id,msg.mailbox_id,m.address mailbox_address,msg.envelope_from,msg.envelope_to,msg.subject,msg.received_at,msg.otp,msg.has_attachments,
                       CASE WHEN EXISTS(SELECT 1 FROM message_reads r WHERE r.message_id=msg.id AND r.user_id=?) THEN 1 ELSE 0 END is_read,
                       substr(replace(replace(COALESCE(msg.body_text,''),'\n',' '),'\r',' '),1,220) preview
                FROM messages msg JOIN mailboxes m ON m.id=msg.mailbox_id {joins}
                WHERE {clause} ORDER BY msg.received_at DESC,msg.id DESC LIMIT ? OFFSET ?""",
            [user["id"]]+params+[page_size,offset]
        ).fetchall()
        return {"items":[dict(x) for x in rows],"page":page,"page_size":page_size,"total":total,
                "mailbox":mailbox_id,"query":query,"scope":scope,
                "engine":dbm.search_engine() if query and scope != "headers" else "like"}


@app.get("/api/messages/{message_id}")
async def get_message(request: Request,message_id: int) -> dict[str,Any]:
    user,_=require_session(request)
    with db() as conn:
        row=conn.execute("SELECT * FROM messages WHERE id=?",(message_id,)).fetchone()
        if not row or not has_access(conn,user,row["mailbox_id"]): raise HTTPException(status_code=404,detail="Message not found")
        otp = maybe_repair_otp(conn, row)
        attachments=conn.execute("SELECT id,filename,content_type,disposition,size_bytes FROM attachments WHERE message_id=? ORDER BY id",(message_id,)).fetchall()
        is_read=conn.execute("SELECT 1 FROM message_reads WHERE message_id=? AND user_id=?",(message_id,user["id"])).fetchone() is not None
        return {"id":row["id"],"mailbox_id":row["mailbox_id"],"envelope_from":row["envelope_from"],"envelope_to":row["envelope_to"],"subject":row["subject"],"message_id":row["message_id"],"received_at":row["received_at"],"otp":otp,"body_text":row["body_text"] or "","has_html":bool(row["body_html"]),"is_read":is_read,"attachments":[dict(x) for x in attachments]}


@app.get("/api/messages/{message_id}/html",response_class=HTMLResponse)
async def message_html(request: Request,message_id: int,remote: Optional[int]=None,theme: str="light") -> HTMLResponse:
    """Render a sanitized email for the reader iframe.

    Remote images default ON (Gmail-like). Pass `remote=0` to block them.
    `theme=dark` swaps the document background and default text colour so the
    preview matches the surrounding UI instead of flashing a white panel in
    dark mode. Only those two surface colours are changed; the email's own
    styling is preserved.
    """
    user,_=require_session(request)
    dark = theme == "dark"
    allow_remote = True if remote is None else bool(remote)
    with db() as conn:
        row=conn.execute("SELECT * FROM messages WHERE id=?",(message_id,)).fetchone()
        if not row or not has_access(conn,user,row["mailbox_id"]): raise HTTPException(status_code=404,detail="Message not found")
        maybe_repair_otp(conn, row)
        ats=conn.execute("SELECT id,content_id,content_type,filename,content_location,data FROM attachments WHERE message_id=?",(message_id,)).fetchall()
        cid_map: dict[str, str] = {}
        cid_total = 0
        for a in ats:
            raw_b = dbm.as_bytes(a["data"]) if a["data"] else b""
            if not raw_b:
                continue
            # Guard against large attachments exhausting memory in 512MB RAM
            if len(raw_b) > 512 * 1024 or cid_total + len(raw_b) > 2 * 1024 * 1024:
                continue
            cid_total += len(raw_b)
            ctype = a["content_type"] or "image/png"
            b64_str = base64.b64encode(raw_b).decode("ascii")
            data_uri = f"data:{ctype};base64,{b64_str}"
            if a["content_id"]:
                raw_cid = str(a["content_id"]).strip()
                clean_cid = raw_cid.strip("<> \t\r\n").casefold()
                cid_map[clean_cid] = data_uri
                cid_map[raw_cid.casefold()] = data_uri
            if a["filename"]:
                fname = str(a["filename"]).strip().casefold()
                cid_map[fname] = data_uri
                cid_map[os.path.basename(fname)] = data_uri
            if a["content_location"]:
                loc = str(a["content_location"]).strip().casefold()
                cid_map[loc] = data_uri

        body = sanitize_email_html(row["body_html"] or "", cid_map, allow_remote) if row["body_html"] else ""
        if not body.strip():
            body = f"<pre>{html_lib.escape(row['body_text'] or '')}</pre>"
        csp="default-src 'none'; img-src 'self' data: https: http: blob:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; font-src data: https:; media-src data: https: http:;"
        bg,fg,link = ("#0f1728","#dbe4f5","#93a5ff") if dark else ("#fff","#172033","#2563eb")
        color_scheme = "dark" if dark else "light"
        resize_script = """
<script>
(function(){
  function sendHeight(){
    try {
      var h = Math.max(
        document.body ? document.body.scrollHeight : 0,
        document.body ? document.body.offsetHeight : 0,
        document.documentElement ? document.documentElement.scrollHeight : 0,
        document.documentElement ? document.documentElement.offsetHeight : 0
      );
      if(h > 30) {
        window.parent.postMessage({ type: 'otp_mail_resize', height: h + 24 }, '*');
      }
    } catch(e) {}
  }
  window.addEventListener('load', sendHeight);
  if (typeof ResizeObserver !== 'undefined' && document.body) {
    try { new ResizeObserver(sendHeight).observe(document.body); } catch(e) {}
  }
  setTimeout(sendHeight, 200);
  setTimeout(sendHeight, 800);
  setTimeout(sendHeight, 2000);
})();
</script>
"""
        page=(
            f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<meta name='color-scheme' content='{color_scheme}'>"
            f"<meta http-equiv='Content-Security-Policy' content=\"{csp}\">"
            f"<meta name='referrer' content='no-referrer'>"
            f"<style>"
            f"html,body{{margin:0;padding:0;background:{bg};color:{fg};font:14px/1.55 Arial,Helvetica,sans-serif;overflow-y:visible}}"
            f"body{{padding:18px;overflow-wrap:anywhere;-webkit-text-size-adjust:100%;overflow-x:auto}}"
            f"img{{max-width:100%;height:auto;border:0;display:inline-block}}"
            f"table{{max-width:100%;border-collapse:collapse;border-spacing:0}}"
            f"td,th{{vertical-align:top}}"
            f"a{{color:{link}}}p,div,li{{margin:0 0 .6em}}pre{{white-space:pre-wrap;font:inherit}}"
            f"</style></head><body>{body}{resize_script}</body></html>"
        )
        return HTMLResponse(page,headers={"X-Frame-Options":"SAMEORIGIN","Referrer-Policy":"no-referrer","Cache-Control":"no-store"})


def message_raw_bytes(row: Any) -> bytes:
    """Return raw MIME bytes if stored; otherwise synthesize a clean RFC822 EML."""
    raw = None
    try:
        raw = dbm.as_bytes(row["raw_mime"])
    except (KeyError, IndexError, TypeError):
        pass
    if raw:
        return raw

    def _get(keys: tuple[str, ...], default: str = "") -> str:
        for k in keys:
            try:
                v = row[k]
                if v is not None:
                    return str(v)
            except (KeyError, IndexError, TypeError):
                pass
        return default

    env_from = _get(("envelope_from", "sender"), "unknown@example.com")
    env_to = _get(("envelope_to", "recipient"), "unknown@example.com")
    subj = _get(("subject",), "(no subject)")
    recv_at = _get(("received_at",), now_iso())
    msg_id = _get(("message_id",), "")
    body_html = _get(("body_html",), "")
    body_text = _get(("body_text",), "")

    lines = [
        f"From: {env_from}",
        f"To: {env_to}",
        f"Subject: {subj}",
        f"Date: {recv_at}",
    ]
    if msg_id:
        lines.append(f"Message-ID: {msg_id}")
    if body_html:
        lines.extend([
            "MIME-Version: 1.0",
            'Content-Type: text/html; charset="utf-8"',
            "",
            body_html,
        ])
    else:
        lines.extend([
            "MIME-Version: 1.0",
            'Content-Type: text/plain; charset="utf-8"',
            "",
            body_text,
        ])
    return "\r\n".join(lines).encode("utf-8")


@app.get("/api/messages/{message_id}/raw")
async def raw_message(request: Request,message_id:int)->Response:
    user,_=require_session(request)
    with db() as conn:
        r=conn.execute("SELECT * FROM messages WHERE id=?",(message_id,)).fetchone()
        if not r or not has_access(conn,user,r["mailbox_id"]):raise HTTPException(status_code=404,detail="Message not found")
        return Response(content=message_raw_bytes(r),media_type="message/rfc822",headers={"Content-Disposition":f"inline; filename=message-{message_id}.eml","Cache-Control":"no-store"})


@app.get("/api/messages/{message_id}/raw-text")
async def raw_text(request: Request,message_id:int)->dict[str,str]:
    user,_=require_session(request)
    with db() as conn:
        r=conn.execute("SELECT * FROM messages WHERE id=?",(message_id,)).fetchone()
        if not r or not has_access(conn,user,r["mailbox_id"]):raise HTTPException(status_code=404,detail="Message not found")
        return {"raw":message_raw_bytes(r).decode("utf-8",errors="replace")}


@app.post("/api/messages/{message_id}/read")
async def mark_read(request: Request,message_id:int)->dict[str,bool]:
    user,session=require_session(request);require_csrf(request,session)
    with db() as conn:
        r=conn.execute("SELECT mailbox_id FROM messages WHERE id=?",(message_id,)).fetchone()
        if not r or not has_access(conn,user,r["mailbox_id"]):raise HTTPException(status_code=404,detail="Message not found")
        # ON CONFLICT upsert rather than INSERT OR REPLACE: the latter is
        # SQLite-only syntax, and the former behaves identically on both backends.
        conn.execute("""INSERT INTO message_reads(message_id,user_id,read_at) VALUES(?,?,?)
                         ON CONFLICT(message_id,user_id) DO UPDATE SET read_at=excluded.read_at""",
                    (message_id,user["id"],now_iso()))
    return {"ok":True}


@app.post("/api/messages/{message_id}/unread")
async def mark_unread(request: Request,message_id:int)->dict[str,bool]:
    user,session=require_session(request);require_csrf(request,session)
    with db() as conn:
        r=conn.execute("SELECT mailbox_id FROM messages WHERE id=?",(message_id,)).fetchone()
        if not r or not has_access(conn,user,r["mailbox_id"]):raise HTTPException(status_code=404,detail="Message not found")
        conn.execute("DELETE FROM message_reads WHERE message_id=? AND user_id=?",(message_id,user["id"]))
    return {"ok":True}


@app.post("/api/messages/bulk-read")
async def bulk_message_read(request: Request,payload:dict[str,Any])->dict[str,Any]:
    user,session=require_session(request);require_csrf(request,session);ids=payload.get("message_ids") or []
    if not isinstance(ids,list) or not ids: raise HTTPException(status_code=400,detail="message_ids required")
    changed=0
    with db() as conn:
        for raw_id in ids[:200]:
            try: mid=int(raw_id)
            except: continue
            row=conn.execute("SELECT mailbox_id FROM messages WHERE id=?",(mid,)).fetchone()
            if row and has_access(conn,user,row["mailbox_id"]):
                conn.execute("""INSERT INTO message_reads(message_id,user_id,read_at) VALUES(?,?,?)
                                 ON CONFLICT(message_id,user_id) DO UPDATE SET read_at=excluded.read_at""",
                            (mid,user["id"],now_iso()));changed+=1
    return {"ok":True,"count":changed}


@app.post("/api/messages/bulk-unread")
async def bulk_message_unread(request: Request,payload:dict[str,Any])->dict[str,Any]:
    user,session=require_session(request);require_csrf(request,session);ids=payload.get("message_ids") or []
    if not isinstance(ids,list) or not ids: raise HTTPException(status_code=400,detail="message_ids required")
    changed=0
    with db() as conn:
        for raw_id in ids[:200]:
            try: mid=int(raw_id)
            except: continue
            row=conn.execute("SELECT mailbox_id FROM messages WHERE id=?",(mid,)).fetchone()
            if row and has_access(conn,user,row["mailbox_id"]):
                conn.execute("DELETE FROM message_reads WHERE message_id=? AND user_id=?",(mid,user["id"]));changed+=1
    return {"ok":True,"count":changed}


@app.get("/api/attachments/{attachment_id}")
async def attachment(request: Request,attachment_id:int)->Response:
    user,_=require_session(request)
    with db() as conn:
        a=conn.execute("""SELECT a.*,m.mailbox_id FROM attachments a JOIN messages m ON m.id=a.message_id WHERE a.id=?""",(attachment_id,)).fetchone()
        if not a or not has_access(conn,user,a["mailbox_id"]):raise HTTPException(status_code=404,detail="Attachment not found")
        filename=clean_text(a["filename"] or "attachment",255).replace('"','')
        disposition="inline" if str(a["content_type"]).startswith("image/") else "attachment"
        return Response(content=dbm.as_bytes(a["data"]) or b"",media_type=a["content_type"] or "application/octet-stream",headers={"Content-Disposition":f'{disposition}; filename="{filename}"',"X-Content-Type-Options":"nosniff","Cache-Control":"private,no-store"})


# ---------------------- mailbox management -------------------


def normalize_ids(value: Any) -> list[int]:
    if not isinstance(value,list): raise HTTPException(status_code=400,detail="mailbox_ids must be a list")
    ids=[]
    for x in value:
        try: v=int(x)
        except: continue
        if v>0 and v not in ids:ids.append(v)
    if not ids: raise HTTPException(status_code=400,detail="No mailbox selected")
    return ids[:5000]


def target_user_or_400(conn: sqlite3.Connection, public_id: str) -> Row:
    u=user_by_public_id(conn,public_id)
    if not u or u["role"]!='user' or not u["is_active"]: raise HTTPException(status_code=404,detail="Target user ID not found")
    return u


@app.post("/api/mailboxes/create")
async def create_mailbox(request: Request,payload:dict[str,Any])->dict[str,Any]:
    user,session=require_session(request);require_csrf(request,session)
    address=normalize_email(str(payload.get("address") or ""))
    if not valid_email(address): raise HTTPException(status_code=400,detail="Valid email address required")
    owner_pid=str(payload.get("owner_public_id") or "").strip().upper()
    target_pids=payload.get("target_public_ids") or []
    if not isinstance(target_pids,list): target_pids=[]
    if user["role"]!='admin':
        owner_pid=user["public_id"]
        target_pids=[]
        domain_part = address.split("@")[-1].lower() if "@" in address else ""
        with db() as chk_conn:
            d_row = chk_conn.execute("SELECT access_level, is_active FROM domains WHERE name=?", (domain_part,)).fetchone()
            if d_row:
                if not d_row["is_active"] or d_row["access_level"] == "disabled":
                    raise HTTPException(status_code=400, detail=f"Tên miền @{domain_part} đang tạm khóa tiếp nhận tạo mới")
                if d_row["access_level"] == "approval_only":
                    raise HTTPException(status_code=403, detail=f"Tên miền @{domain_part} yêu cầu gửi đơn xin cấp phép (Approval required)")
                if d_row["access_level"] == "vip" and not user.get("can_generate_codes"):
                    raise HTTPException(status_code=403, detail=f"Tên miền @{domain_part} chỉ dành cho thành viên VIP")
    with db() as conn:
        exists=conn.execute("SELECT id FROM mailboxes WHERE address=?",(address,)).fetchone()
        if exists: raise HTTPException(status_code=409,detail="Mailbox already exists")
        owner=None
        if owner_pid:
            owner=target_user_or_400(conn,owner_pid) if user["role"]=='admin' else user
        elif user["role"]=='admin': owner=None
        targets=[]
        for pid in target_pids:
            u=target_user_or_400(conn,str(pid));
            if all(u["id"]!=x["id"] for x in targets):targets.append(u)
        if owner and all(owner["id"]!=x["id"] for x in targets): targets.insert(0,owner)
        if not owner and targets and user["role"]=='admin': owner=targets[0]
        if user["role"]!='admin': owner=user
        if user["role"]=='admin' and not owner and not targets:
            exact=0
        else: exact=1
        created=now_iso();cur=conn.execute("INSERT INTO mailboxes(address,owner_user_id,exact_override,override_reason,created_at,updated_at) VALUES(?,?,?,?,?,?)",(address,owner["id"] if owner else None,exact,"manually created" if exact else None,created,created))
        mid=cur.lastrowid
        for t in targets:
            conn.execute("INSERT INTO mailbox_access(mailbox_id,user_id,source,rule_id,granted_by,granted_at) VALUES(?,?,?,?,?,?)",(mid,t["id"],"manual",None,user["id"],now_iso()))
            add_notification(conn,t["id"],"mailbox_created","Mailbox created",f"{address} đã được tạo và cấp cho bạn.",mid)
        add_audit(conn,user["id"],"mailbox.created",mid,owner["id"] if owner else None,f"Created {address}; exact_override={exact}; targets={[x['public_id'] for x in targets]}")
        return {"ok":True,"mailbox_id":mid,"notice":f"Đã tạo mailbox {address}."}



@app.post("/api/mailboxes/create-bulk")
async def create_mailboxes_bulk(request: Request,payload:dict[str,Any])->dict[str,Any]:
    user,session=require_session(request);require_csrf(request,session)
    raw=payload.get("addresses","")
    parts=[str(x).strip() for x in raw] if isinstance(raw,list) else re.split(r"[\n,;\s]+",str(raw or ""))
    addresses=[];invalid=[]
    for value in parts:
        if not value: continue
        addr=normalize_email(value)
        if valid_email(addr) and addr not in addresses: addresses.append(addr)
        elif value: invalid.append(value)
    if not addresses: raise HTTPException(status_code=400,detail="No valid email addresses supplied")
    if len(addresses)>5000: raise HTTPException(status_code=400,detail="Maximum 5000 addresses per bulk create")
    created=0;existing=0
    with db() as conn:
        for addr in addresses:
            m=conn.execute("SELECT * FROM mailboxes WHERE address=?",(addr,)).fetchone()
            if m:
                if user["role"]=="admin": existing+=1; continue
                if has_access(conn,user,m["id"]): existing+=1; continue
                raise HTTPException(status_code=409,detail=f"Mailbox {addr} already exists and is not assigned to you")
            t=now_iso()
            cur=conn.execute("INSERT INTO mailboxes(address,owner_user_id,exact_override,override_reason,created_at,updated_at) VALUES(?,?,?,?,?,?)",(addr,user["id"],1,"user bulk create",t,t))
            mid=cur.lastrowid
            conn.execute("INSERT INTO mailbox_access(mailbox_id,user_id,source,rule_id,granted_by,granted_at) VALUES(?,?,?,?,?,?)",(mid,user["id"],"manual",None,user["id"],t))
            MAILBOX_ADDR_CACHE.set(addr, mid)
            created+=1
        if created:
            if created == 1:
                add_audit(conn,user["id"],"mailbox.bulk_created",mid,user["id"],f"Bulk created {addresses[0]} as exact mailbox")
            else:
                sample = ", ".join(addresses[:3]) + (f" and {created-3} more" if created > 3 else "")
                add_audit(conn,user["id"],"mailbox.bulk_created",None,user["id"],f"Bulk created {created} mailboxes ({sample})")
            invalidate_access_caches(user["id"])
        notice=f"Đã tạo {created} mailbox."
        if existing: notice+=f" Bỏ qua {existing} mailbox đã tồn tại."
        if invalid: notice+=f" Bỏ qua {len(invalid)} địa chỉ không hợp lệ."
        return {"ok":True,"created":created,"existing":existing,"invalid":invalid[:50],"notice":notice}


@app.post("/api/mailboxes/{mailbox_id}/share")
async def share_mailbox(request: Request,mailbox_id:int,payload:dict[str,Any])->dict[str,Any]:
    user,session=require_session(request);require_csrf(request,session)
    with db() as conn:
        m=mailbox_by_id(conn,mailbox_id)
        if not m or not is_owner(user,m):raise HTTPException(status_code=403,detail="Owner permission required")
        target=target_user_or_400(conn,str(payload.get("target_public_id") or ""))
        if target["id"]==user["id"]:raise HTTPException(status_code=400,detail="Cannot share to yourself")
        old_users=access_users(conn,mailbox_id)
        set_exact_override(conn,mailbox_id,m["owner_user_id"],"manual shared")
        exists=conn.execute("SELECT 1 FROM mailbox_access WHERE mailbox_id=? AND user_id=?",(mailbox_id,target["id"])).fetchone()
        if not exists:
            conn.execute("INSERT INTO mailbox_access(mailbox_id,user_id,source,rule_id,granted_by,granted_at) VALUES(?,?,?,?,?,?)",(mailbox_id,target["id"],"manual",None,user["id"],now_iso()))
        for a in old_users:
            if a["access_user_id"]!=target["id"]:
                add_notification(conn,a["access_user_id"],"shared","Mailbox shared",f"{m['address']} đã được shared với {target['username']} ({target['public_id']}).",mailbox_id)
        add_notification(conn,target["id"],"shared","Mailbox shared with you",f"{m['address']} đã được shared với bạn bởi {user['username']}. Owner hiện tại vẫn giữ quyền.",mailbox_id)
        invalidate_access_caches(target["id"], user["id"])
        add_audit(conn,user["id"],"mailbox.shared",mailbox_id,target["id"],f"Shared {m['address']} with {target['username']} ({target['public_id']})")
    return {"ok":True,"notice":f"Shared {m['address']} cho {target['username']}. Exact override đã được bật."}


@app.post("/api/mailboxes/{mailbox_id}/transfer")
async def transfer_mailbox(request: Request,mailbox_id:int,payload:dict[str,Any])->dict[str,Any]:
    user,session=require_session(request);require_csrf(request,session)
    with db() as conn:
        m=mailbox_by_id(conn,mailbox_id)
        if not m or not is_owner(user,m):raise HTTPException(status_code=403,detail="Owner permission required")
        target=target_user_or_400(conn,str(payload.get("target_public_id") or ""))
        if target["id"]==user["id"]:raise HTTPException(status_code=400,detail="Cannot transfer to yourself")
        affected=[a["access_user_id"] for a in access_users(conn,mailbox_id)]
        old_owner=m["owner_user_id"]
        set_exact_override(conn,mailbox_id,target["id"],"manual transfer")
        conn.execute("DELETE FROM mailbox_access WHERE mailbox_id=?",(mailbox_id,))
        conn.execute("INSERT INTO mailbox_access(mailbox_id,user_id,source,rule_id,granted_by,granted_at) VALUES(?,?,?,?,?,?)",(mailbox_id,target["id"],"manual",None,user["id"],now_iso()))
        notified=set()
        for uid in affected:
            if uid!=target["id"]:
                add_notification(conn,uid,"transfer_out","Mailbox transferred away",f"{m['address']} đã được transfer sang {target['username']} ({target['public_id']}). Bạn mất quyền truy cập.",mailbox_id);notified.add(uid)
        add_notification(conn,target["id"],"transfer_in","Mailbox transferred to you",f"{m['address']} đã được transfer cho bạn. Owner/access cũ đã bị thu hồi.",mailbox_id)
        add_audit(conn,user["id"],"mailbox.transferred",mailbox_id,target["id"],f"Transferred {m['address']} from user_id={old_owner} to {target['username']} ({target['public_id']}); affected={affected}")
        invalidate_access_caches(*affected, target["id"])
    return {"ok":True,"notice":f"Transfer {m['address']} sang {target['username']} thành công. Access cũ đã bị thu hồi."}


@app.post("/api/mailboxes/{mailbox_id}/revoke")
async def revoke_mailbox(request: Request,mailbox_id:int,payload:dict[str,Any])->dict[str,Any]:
    user,session=require_session(request);require_csrf(request,session)
    with db() as conn:
        m=mailbox_by_id(conn,mailbox_id)
        if not m or not is_owner(user,m):raise HTTPException(status_code=403,detail="Owner permission required")
        target=target_user_or_400(conn,str(payload.get("target_public_id") or ""))
        if target["id"]==m["owner_user_id"]:raise HTTPException(status_code=400,detail="Cannot revoke the owner")
        hit=conn.execute("SELECT 1 FROM mailbox_access WHERE mailbox_id=? AND user_id=?",(mailbox_id,target["id"])).fetchone()
        if hit: conn.execute("DELETE FROM mailbox_access WHERE mailbox_id=? AND user_id=?",(mailbox_id,target["id"]))
        add_notification(conn,target["id"],"revoke","Mailbox access revoked",f"Quyền truy cập {m['address']} của bạn đã bị thu hồi bởi {user['username']}.",mailbox_id)
        add_audit(conn,user["id"],"mailbox.access_revoked",mailbox_id,target["id"],f"Revoked {target['username']} from {m['address']}")
        invalidate_access_caches(target["id"])
    return {"ok":True,"notice":f"Đã thu hồi access của {target['username']} trên {m['address']}."}


@app.post("/api/mailboxes/{mailbox_id}/return-to-rules")
async def return_to_rules(request: Request,mailbox_id:int)->dict[str,Any]:
    user,session=require_session(request);require_csrf(request,session)
    with db() as conn:
        m=mailbox_by_id(conn,mailbox_id)
        if not m or not is_owner(user,m):raise HTTPException(status_code=403,detail="Owner permission required")
        old_access=access_users(conn,mailbox_id)
        conn.execute("UPDATE mailboxes SET owner_user_id=NULL,exact_override=0,override_reason=NULL,updated_at=? WHERE id=?",(now_iso(),mailbox_id))
        conn.execute("DELETE FROM mailbox_access WHERE mailbox_id=?",(mailbox_id,))
        m2=mailbox_by_id(conn,mailbox_id)
        sync_mailbox_rules(conn,m2,notify=False,actor_user_id=user["id"])
        for a in old_access:
            if a["access_user_id"]!=user["id"]:
                add_notification(conn,a["access_user_id"],"routing_reset","Mailbox returned to rules",f"{m['address']} đã bỏ exact override và được routing lại theo prefix/regex.",mailbox_id)
        add_audit(conn,user["id"],"mailbox.returned_to_rules",mailbox_id,None,f"Returned {m['address']} to prefix/regex routing")
    return {"ok":True,"notice":f"{m['address']} đã quay về routing rules."}


@app.post("/api/mailboxes/bulk-share")
async def bulk_share(request: Request,payload:dict[str,Any])->dict[str,Any]:
    user,session=require_session(request);require_csrf(request,session);ids=normalize_ids(payload.get("mailbox_ids"));target_pid=str(payload.get("target_public_id") or "")
    with db() as conn:
        target=target_user_or_400(conn,target_pid)
        if target["id"]==user["id"]:raise HTTPException(status_code=400,detail="Cannot share to yourself")
        done=[]
        for mid in ids:
            m=mailbox_by_id(conn,mid)
            if not m or not is_owner(user,m):raise HTTPException(status_code=403,detail=f"Owner permission required for mailbox #{mid}")
            old_users=access_users(conn,mid)
            set_exact_override(conn,mid,m["owner_user_id"],"manual bulk shared")
            if not conn.execute("SELECT 1 FROM mailbox_access WHERE mailbox_id=? AND user_id=?",(mid,target["id"])).fetchone():
                conn.execute("INSERT INTO mailbox_access(mailbox_id,user_id,source,rule_id,granted_by,granted_at) VALUES(?,?,?,?,?,?)",(mid,target["id"],"manual",None,user["id"],now_iso()))
            for old_user in old_users:
                if old_user["access_user_id"] != target["id"]:
                    add_notification(conn,old_user["access_user_id"],"shared","Mailbox batch shared",f"{m['address']} đã được shared với {target['username']} ({target['public_id']}). Bạn vẫn giữ quyền.",mid)
            add_notification(conn,target["id"],"shared","Mailbox batch shared with you",f"{m['address']} đã được shared với bạn bởi {user['username']}. Owner cũ vẫn giữ quyền.",mid)
            add_audit(conn,user["id"],"mailbox.bulk_shared",mid,target["id"],f"Bulk shared {m['address']} to {target['username']} ({target['public_id']})")
            done.append(m["address"])
    return {"ok":True,"count":len(done),"notice":f"Shared thành công {len(done)} mailbox cho {target['username']}. Exact override đã bật."}


@app.post("/api/mailboxes/bulk-transfer")
async def bulk_transfer(request: Request,payload:dict[str,Any])->dict[str,Any]:
    user,session=require_session(request);require_csrf(request,session);ids=normalize_ids(payload.get("mailbox_ids"));target_pid=str(payload.get("target_public_id") or "")
    with db() as conn:
        target=target_user_or_400(conn,target_pid)
        if target["id"]==user["id"]:raise HTTPException(status_code=400,detail="Cannot transfer to yourself")
        done=[]
        for mid in ids:
            m=mailbox_by_id(conn,mid)
            if not m or not is_owner(user,m):raise HTTPException(status_code=403,detail=f"Owner permission required for mailbox #{mid}")
            affected=[a["access_user_id"] for a in access_users(conn,mid)]
            set_exact_override(conn,mid,target["id"],"manual bulk transfer")
            conn.execute("DELETE FROM mailbox_access WHERE mailbox_id=?",(mid,))
            conn.execute("INSERT INTO mailbox_access(mailbox_id,user_id,source,rule_id,granted_by,granted_at) VALUES(?,?,?,?,?,?)",(mid,target["id"],"manual",None,user["id"],now_iso()))
            for uid in affected:
                if uid!=target["id"]: add_notification(conn,uid,"transfer_out","Mailbox transferred away",f"{m['address']} đã được transfer sang {target['username']}.",mid)
            add_notification(conn,target["id"],"transfer_in","Mailbox transferred to you",f"{m['address']} đã được transfer cho bạn.",mid)
            add_audit(conn,user["id"],"mailbox.bulk_transferred",mid,target["id"],f"Bulk transferred {m['address']} to {target['username']} ({target['public_id']}); affected={affected}")
            done.append(m["address"])
    return {"ok":True,"count":len(done),"notice":f"Transfer thành công {len(done)} mailbox sang {target['username']}. Access cũ đã bị thu hồi."}


@app.post("/api/mailboxes/bulk-revoke")
async def bulk_revoke(request: Request,payload:dict[str,Any])->dict[str,Any]:
    user,session=require_session(request);require_csrf(request,session);ids=normalize_ids(payload.get("mailbox_ids"));target_pid=str(payload.get("target_public_id") or "")
    with db() as conn:
        target=target_user_or_400(conn,target_pid);changed=0
        for mid in ids:
            m=mailbox_by_id(conn,mid)
            if not m or not is_owner(user,m):raise HTTPException(status_code=403,detail=f"Owner permission required for mailbox #{mid}")
            if target["id"]==m["owner_user_id"]:raise HTTPException(status_code=400,detail=f"Cannot revoke owner of {m['address']}")
            if conn.execute("SELECT 1 FROM mailbox_access WHERE mailbox_id=? AND user_id=?",(mid,target["id"])).fetchone():
                conn.execute("DELETE FROM mailbox_access WHERE mailbox_id=? AND user_id=?",(mid,target["id"]))
                add_notification(conn,target["id"],"revoke","Mailbox access revoked",f"Quyền {m['address']} của bạn đã bị thu hồi.",mid);add_audit(conn,user["id"],"mailbox.bulk_access_revoked",mid,target["id"],f"Revoked {target['username']} from {m['address']}");changed+=1
    return {"ok":True,"count":changed,"notice":f"Đã thu hồi {changed} access."}


@app.post("/api/mailboxes/bulk-return-to-rules")
async def bulk_return_to_rules(request: Request,payload:dict[str,Any])->dict[str,Any]:
    user,session=require_session(request);require_csrf(request,session);ids=normalize_ids(payload.get("mailbox_ids"));changed=0
    with db() as conn:
        for mid in ids:
            m=mailbox_by_id(conn,mid)
            if not m or not is_owner(user,m):raise HTTPException(status_code=403,detail=f"Owner permission required for mailbox #{mid}")
            conn.execute("UPDATE mailboxes SET owner_user_id=NULL,exact_override=0,override_reason=NULL,updated_at=? WHERE id=?",(now_iso(),mid))
            old_access=access_users(conn,mid)
            conn.execute("DELETE FROM mailbox_access WHERE mailbox_id=?",(mid,))
            sync_mailbox_rules(conn,mailbox_by_id(conn,mid),notify=False,actor_user_id=user["id"])
            current_ids={x["access_user_id"] for x in access_users(conn,mid)}
            for old_user in old_access:
                if old_user["access_user_id"] not in current_ids:
                    add_notification(conn,old_user["access_user_id"],"routing_reset","Mailbox returned to rules",f"{m['address']} đã được đưa về prefix/regex và quyền cũ có thể đã thay đổi.",mid)
            add_audit(conn,user["id"],"mailbox.bulk_returned_to_rules",mid,None,f"Returned {m['address']} to routing rules");changed+=1
    return {"ok":True,"count":changed,"notice":f"Đã đưa {changed} mailbox về routing rules."}


@app.post("/api/admin/assign")
async def admin_assign(request: Request,payload:dict[str,Any])->dict[str,Any]:
    admin,session=require_admin(request);require_csrf(request,session);ids=normalize_ids(payload.get("mailbox_ids"));targets=payload.get("target_public_ids") or []
    if not isinstance(targets,list) or not targets:raise HTTPException(status_code=400,detail="target_public_ids required")
    with db() as conn:
        target_rows=[target_user_or_400(conn,str(x)) for x in targets]
        done=0
        for mid in ids:
            m=mailbox_by_id(conn,mid)
            if not m:continue
            owner=m["owner_user_id"]
            if owner is None: owner=target_rows[0]["id"]
            set_exact_override(conn,mid,owner,"admin exact assignment")
            conn.execute("UPDATE mailboxes SET owner_user_id=?,updated_at=? WHERE id=?",(owner,now_iso(),mid))
            for t in target_rows:
                if not conn.execute("SELECT 1 FROM mailbox_access WHERE mailbox_id=? AND user_id=?",(mid,t["id"])).fetchone():
                    conn.execute("INSERT INTO mailbox_access(mailbox_id,user_id,source,rule_id,granted_by,granted_at) VALUES(?,?,?,?,?,?)",(mid,t["id"],"manual",None,admin["id"],now_iso()))
                add_notification(conn,t["id"],"admin_assign","Mailbox assigned by admin",f"Admin đã gán {m['address']} cho bạn.",mid)
                add_audit(conn,admin["id"],"mailbox.admin_assigned",mid,t["id"],f"Admin exact assigned {m['address']} to {t['username']} ({t['public_id']})")
            done+=1
    return {"ok":True,"count":done,"notice":f"Đã gán {done} mailbox cho {len(target_rows)} user. Exact override đã bật."}


@app.post("/api/admin/import-mailboxes")
async def admin_import_mailboxes(request: Request,payload:dict[str,Any])->dict[str,Any]:
    """Create or update many exact mailboxes and assign manual access in one operation.
    Input can be a newline/comma/semicolon separated string or an array of addresses.
    This deliberately creates an exact override so regex/prefix cannot take precedence over an explicit admin assignment.
    """
    admin,session=require_admin(request);require_csrf(request,session)
    raw = payload.get("addresses", "")
    if isinstance(raw, list):
        parts=[str(x).strip() for x in raw]
    else:
        parts=re.split(r"[\n,;\s]+", str(raw or ""))
    addresses=[];invalid=[]
    for value in parts:
        if not value: continue
        addr=normalize_email(value)
        if valid_email(addr):
            if addr not in addresses: addresses.append(addr)
        else:
            invalid.append(value)
    if not addresses: raise HTTPException(status_code=400,detail="No valid email addresses supplied")
    if len(addresses)>5000: raise HTTPException(status_code=400,detail="Maximum 5000 addresses per import")
    raw_targets=payload.get("target_public_ids") or []
    if not isinstance(raw_targets,list): raw_targets=[]
    with db() as conn:
        targets=[]
        for pid in raw_targets:
            targets_pid=str(pid).strip().upper()
            if not targets_pid: continue
            u=target_user_or_400(conn,targets_pid)
            if all(x["id"]!=u["id"] for x in targets): targets.append(u)
        if not targets:
            raise HTTPException(status_code=400,detail="At least one target user is required")
        created=0;updated=0;assigned=0;already=0
        sample=[]
        for addr in addresses:
            m=conn.execute("SELECT * FROM mailboxes WHERE address=?",(addr,)).fetchone()
            if m:
                updated+=1
            else:
                t=now_iso()
                cur=conn.execute("INSERT INTO mailboxes(address,owner_user_id,exact_override,override_reason,created_at,updated_at) VALUES(?,?,?,?,?,?)",(addr,targets[0]["id"],1,"admin bulk import",t,t))
                m=conn.execute("SELECT * FROM mailboxes WHERE id=?",(cur.lastrowid,)).fetchone()
                created+=1
            set_exact_override(conn,m["id"],m["owner_user_id"] or targets[0]["id"],"admin bulk import")
            conn.execute("UPDATE mailboxes SET owner_user_id=COALESCE(owner_user_id,?),exact_override=1,override_reason=?,updated_at=? WHERE id=?",(targets[0]["id"],"admin bulk import",now_iso(),m["id"]))
            for target in targets:
                exists=conn.execute("SELECT 1 FROM mailbox_access WHERE mailbox_id=? AND user_id=?",(m["id"],target["id"])).fetchone()
                if exists:
                    already+=1
                else:
                    conn.execute("INSERT INTO mailbox_access(mailbox_id,user_id,source,rule_id,granted_by,granted_at) VALUES(?,?,?,?,?,?)",(m["id"],target["id"],"manual",None,admin["id"],now_iso()))
                    assigned+=1
                    if len(addresses) <= 20:
                        add_notification(conn,target["id"],"admin_assign","Mailbox assigned by admin",f"Admin đã gán {addr} cho bạn (bulk import). Exact override đang bật.",m["id"])
                    if len(addresses) <= 5:
                        add_audit(conn,admin["id"],"mailbox.bulk_import_assigned",m["id"],target["id"],f"Bulk import assigned {addr} to {target['username']} ({target['public_id']})")
            if len(sample)<10: sample.append(addr)
        if len(addresses) > 20 and assigned > 0:
            for target in targets:
                add_notification(conn,target["id"],"admin_assign","Mailboxes assigned by admin",f"Admin đã gán {assigned} mailbox cho bạn (bulk import). Exact override đang bật.",None)
        notice=f"Import {len(addresses)} địa chỉ: tạo mới {created}, đã tồn tại {updated}, thêm access {assigned}."
        if invalid: notice+=f" Bỏ qua {len(invalid)} địa chỉ không hợp lệ."
        add_audit(conn,admin["id"],"mailbox.bulk_import",None,None,f"Imported {len(addresses)} addresses; created={created}; updated={updated}; targets={[x['public_id'] for x in targets]}; invalid={len(invalid)}")
        return {"ok":True,"created":created,"existing":updated,"assigned":assigned,"already_assigned":already,"invalid":invalid[:50],"sample":sample,"notice":notice}


# ----------------------- routing rules -----------------------


def validate_rule(rule_type: str, pattern: str) -> str:
    if rule_type not in {"prefix", "regex", "domain", "catch_all"}:
        raise HTTPException(status_code=400, detail="rule_type must be prefix, regex, domain, or catch_all")
    pattern = str(pattern or "").strip()
    if rule_type != "catch_all" and not pattern:
        raise HTTPException(status_code=400, detail="Pattern is required")
    if len(pattern) > MAX_REGEX_LEN:
        raise HTTPException(status_code=400, detail=f"Pattern too long (max {MAX_REGEX_LEN})")
    if rule_type == "regex":
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            raise HTTPException(status_code=400, detail=f"Invalid regex: {e}")
    elif rule_type == "domain":
        dom = pattern.lower().lstrip("*@.").strip()
        if not dom or "." not in dom:
            raise HTTPException(status_code=400, detail="Invalid domain format (e.g. example.com or @example.com)")
        pattern = dom
    elif rule_type == "catch_all":
        if not pattern or pattern in ("*", "*@*", "@*"):
            pattern = "*"
        else:
            dom = pattern.lower().lstrip("*@.").strip()
            if dom and "." in dom:
                pattern = f"*@{dom}"
            else:
                pattern = "*"
    else:
        pattern = pattern.strip()
    return pattern


@app.post("/api/admin/rules")
async def create_rule(request: Request,payload:dict[str,Any])->dict[str,Any]:
    admin,session=require_admin(request);require_csrf(request,session)
    rtype=str(payload.get("rule_type") or "");pattern=validate_rule(rtype,payload.get("pattern"));pid=str(payload.get("target_public_id") or "")
    desc=clean_text(payload.get("description") or "",500)
    with db() as conn:
        target=target_user_or_400(conn,pid);created=now_iso();cur=conn.execute("INSERT INTO routing_rules(rule_type,pattern,target_user_id,description,is_active,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(rtype,pattern,target["id"],desc,1,admin["id"],created,created))
        # Apply to current non-exact mailboxes.
        invalidate_rules_cache()
        for m in conn.execute("SELECT * FROM mailboxes WHERE exact_override=0").fetchall(): sync_mailbox_rules(conn,m,notify=True,actor_user_id=admin["id"])
        add_audit(conn,admin["id"],"routing_rule.created",None,target["id"],f"Created {rtype} rule {pattern!r} for {target['username']} ({target['public_id']})")
    return {"ok":True,"rule_id":cur.lastrowid,"notice":f"Đã tạo {rtype} rule cho {target['username']} và reconcile mailbox hiện tại."}


@app.patch("/api/admin/rules/{rule_id}")
async def update_rule(request: Request,rule_id:int,payload:dict[str,Any])->dict[str,Any]:
    admin,session=require_admin(request);require_csrf(request,session)
    rtype=str(payload.get("rule_type") or "");pattern=validate_rule(rtype,payload.get("pattern"));pid=str(payload.get("target_public_id") or "");desc=clean_text(payload.get("description") or "",500)
    with db() as conn:
        rule=conn.execute("SELECT * FROM routing_rules WHERE id=?",(rule_id,)).fetchone()
        if not rule:raise HTTPException(status_code=404,detail="Rule not found")
        target=target_user_or_400(conn,pid);conn.execute("UPDATE routing_rules SET rule_type=?,pattern=?,target_user_id=?,description=?,updated_at=? WHERE id=?",(rtype,pattern,target["id"],desc,now_iso(),rule_id))
        invalidate_rules_cache()
        for m in conn.execute("SELECT * FROM mailboxes WHERE exact_override=0").fetchall(): sync_mailbox_rules(conn,m,notify=False,actor_user_id=admin["id"])
        add_audit(conn,admin["id"],"routing_rule.updated",None,target["id"],f"Updated rule #{rule_id} to {rtype} {pattern!r} for {target['username']}")
    return {"ok":True,"notice":"Routing rule đã cập nhật và reconcile."}


@app.post("/api/admin/rules/{rule_id}/status")
async def rule_status(request: Request,rule_id:int,payload:dict[str,Any])->dict[str,Any]:
    admin,session=require_admin(request);require_csrf(request,session);active=bool(payload.get("is_active"))
    with db() as conn:
        rule=conn.execute("SELECT * FROM routing_rules WHERE id=?",(rule_id,)).fetchone()
        if not rule:raise HTTPException(status_code=404,detail="Rule not found")
        conn.execute("UPDATE routing_rules SET is_active=?,updated_at=? WHERE id=?",(1 if active else 0,now_iso(),rule_id))
        invalidate_rules_cache()
        for m in conn.execute("SELECT * FROM mailboxes WHERE exact_override=0").fetchall(): sync_mailbox_rules(conn,m,notify=False,actor_user_id=admin["id"])
        add_audit(conn,admin["id"],"routing_rule.status",None,rule["target_user_id"],f"Rule #{rule_id} active={active}")
    return {"ok":True,"notice":f"Rule đã {'bật' if active else 'tắt'} và access đã được reconcile."}


@app.delete("/api/admin/rules/{rule_id}")
async def delete_rule(request: Request,rule_id:int)->dict[str,Any]:
    admin,session=require_admin(request);require_csrf(request,session)
    with db() as conn:
        rule=conn.execute("SELECT * FROM routing_rules WHERE id=?",(rule_id,)).fetchone()
        if not rule:raise HTTPException(status_code=404,detail="Rule not found")
        conn.execute("DELETE FROM routing_rules WHERE id=?",(rule_id,))
        invalidate_rules_cache()
        for m in conn.execute("SELECT * FROM mailboxes WHERE exact_override=0").fetchall(): sync_mailbox_rules(conn,m,notify=False,actor_user_id=admin["id"])
        add_audit(conn,admin["id"],"routing_rule.deleted",None,rule["target_user_id"],f"Deleted rule #{rule_id}: {rule['rule_type']} {rule['pattern']!r}")
    return {"ok":True,"notice":"Routing rule đã xóa và access đã được reconcile."}


@app.get("/api/admin/rules/test")
async def test_rules(request: Request,address:str)->dict[str,Any]:
    require_admin(request)
    address=normalize_email(address)
    if not valid_email(address):raise HTTPException(status_code=400,detail="Valid email required")
    with db() as conn:
        m=conn.execute("SELECT exact_override,owner_user_id FROM mailboxes WHERE address=?",(address,)).fetchone()
        if m and m["exact_override"]:
            owner=user_by_id(conn,m["owner_user_id"]) if m["owner_user_id"] else None
            return {"address":address,"exact_override":True,"owner":owner["username"] if owner else None,"matches":[]}
        return {"address":address,"exact_override":False,"owner":None,"matches":[{"id":r["id"],"rule_type":r["rule_type"],"pattern":r["pattern"],"target_username":r["target_username"],"target_public_id":r["target_public_id"],"description":r["description"]} for r in matching_rules(conn,address)]}


# --------------------- referrals / admin ---------------------


def referral_code_json(row: Row) -> dict[str,Any]:
    """Admin-facing view of a code. Never includes the plaintext."""
    return {
        "id": row["id"],
        "fingerprint": row["code_fingerprint"],
        "owner_user_id": row["owner_user_id"],
        "owner_username": row["owner_username"] if "owner_username" in row.keys() else None,
        "created_by": row["created_by"],
        "max_uses": row["max_uses"],
        "uses": row["uses"],
        "remaining": max(0, row["max_uses"] - row["uses"]),
        "expires_at": row["expires_at"],
        "is_active": bool(row["is_active"]),
        "note": row["note"],
        "created_at": row["created_at"],
        "status": code_status(row),
    }


REFERRAL_SELECT = """
    SELECT c.*,u.username owner_username
    FROM referral_codes c LEFT JOIN users u ON u.id=c.owner_user_id
"""


def parse_expires_at(value: Any) -> Optional[str]:
    """Accept '', 'never' or a number of days; return an ISO instant or None."""
    if value is None or value == "" or str(value).strip().lower() in {"never", "null", "none"}:
        return None
    try:
        days = int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="ttl_days must be a number of days, or 0 for no expiry")
    if days < 0:
        raise HTTPException(status_code=400, detail="ttl_days cannot be negative")
    return (utc_now() + timedelta(days=days)).isoformat() if days else None


@app.get("/api/admin/referrals")
async def admin_referrals(request: Request,limit:int=100,owner_id:int=0)->dict[str,Any]:
    admin,session=require_admin(request)
    limit=max(1,min(limit,500))
    with db() as conn:
        where,params="",[]
        if owner_id>0:
            where=" WHERE c.owner_user_id=?"
            params=[owner_id]
        codes=conn.execute(
            REFERRAL_SELECT + where + " ORDER BY c.created_at DESC,c.id DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        registrations=conn.execute(
            """SELECT g.id,g.code_fingerprint,g.ip,g.created_at,u.username,u.public_id,u.is_active,
                      o.username referrer_username,c.id code_id
               FROM registrations g
               JOIN users u ON u.id=g.user_id
               LEFT JOIN referral_codes c ON c.id=g.referral_code_id
               LEFT JOIN users o ON o.id=c.owner_user_id
               ORDER BY g.created_at DESC,g.id DESC LIMIT ?""", (min(limit,200),)
        ).fetchall()
        totals=conn.execute(
            """SELECT COUNT(*) codes,CAST(COALESCE(SUM(uses),0) AS INTEGER) uses,
                      CAST(COALESCE(SUM(CASE WHEN owner_user_id IS NOT NULL THEN 1 ELSE 0 END),0) AS INTEGER) personal
               FROM referral_codes"""
        ).fetchone()
        reg_total=conn.execute("SELECT COUNT(*) c FROM registrations").fetchone()["c"]
        return {
            "settings": {
                SETTING_REGISTRATION_ENABLED: registration_open(conn),
                SETTING_REFERRAL_CODE_GENERATION: code_generation_open(conn),
            },
            "codes": [referral_code_json(r) for r in codes],
            "registrations": [dict(r) for r in registrations],
            "stats": {
                "codes": totals["codes"], "uses": totals["uses"],
                "personal_codes": totals["personal"], "registrations": reg_total,
            },
        }


@app.patch("/api/admin/referrals/settings")
async def admin_referral_settings(request: Request,payload:dict[str,Any])->dict[str,Any]:
    """Master switches for the whole feature.

    registration_enabled gates POST /api/register. referral_code_generation is
    the global half of the code-generation permission: when it is off, no user
    can mint a code regardless of their individual grant, but existing codes
    keep working so nobody is locked out of an account they already made.
    """
    admin,session=require_admin(request);require_csrf(request,session)
    with db() as conn:
        changed=[]
        for key,setting in ((SETTING_REGISTRATION_ENABLED,"registration_enabled"),
                            (SETTING_REFERRAL_CODE_GENERATION,"referral_code_generation")):
            if key in payload:
                value = payload[key]
                if isinstance(value,str):
                    value = value.strip().lower() in {"1","true","yes","on"}
                set_setting(conn,key,bool(value))
                changed.append(f"{setting}={'on' if value else 'off'}")
        if changed:
            add_audit(conn,admin["id"],"referral.settings",None,None,"Updated "+", ".join(changed))
        return {"ok":True,"settings":{
            SETTING_REGISTRATION_ENABLED: registration_open(conn),
            SETTING_REFERRAL_CODE_GENERATION: code_generation_open(conn),
        }}


@app.post("/api/admin/referrals")
async def admin_create_referral_code(request: Request,payload:dict[str,Any])->dict[str,Any]:
    """Mint an admin-issued code. Owner-less codes are not self-service."""
    admin,session=require_admin(request);require_csrf(request,session)
    max_uses=max(1,min(int(payload.get("max_uses") or REFERRAL_MAX_USES_DEFAULT),1000))
    expires_at=parse_expires_at(payload.get("ttl_days") if payload.get("ttl_days") is not None else REFERRAL_CODE_TTL_DAYS)
    note=clean_text(payload.get("note") or "",200)
    owner_public_id=str(payload.get("owner_public_id") or "").strip()
    with db() as conn:
        owner_id=None
        if owner_public_id:
            owner=user_by_public_id(conn,owner_public_id)
            if not owner: raise HTTPException(status_code=404,detail="Owner user not found")
            owner_id=owner["id"]
        row,code=create_referral_code(conn,owner_id,admin["id"],max_uses,expires_at,note)
        add_audit(conn,admin["id"],"referral.code_created",None,owner_id,
                  f"Admin created referral code #{row['id']} max_uses={max_uses} owner={owner_public_id or 'none'}")
        return {"ok":True,"code":code,"id":row["id"],"max_uses":max_uses,"expires_at":expires_at,
                "warning":"This code is shown only once."}


@app.patch("/api/admin/referrals/{code_id}")
async def admin_update_referral_code(request: Request,code_id:int,payload:dict[str,Any])->dict[str,Any]:
    admin,session=require_admin(request);require_csrf(request,session)
    with db() as conn:
        row=conn.execute("SELECT * FROM referral_codes WHERE id=?",(code_id,)).fetchone()
        if not row: raise HTTPException(status_code=404,detail="Referral code not found")
        if "is_active" in payload:
            conn.execute("UPDATE referral_codes SET is_active=? WHERE id=?",(1 if payload["is_active"] else 0,code_id))
        if "max_uses" in payload:
            max_uses=max(1,min(int(payload["max_uses"] or 1),1000))
            # Never let a lowered limit fall below what has already been used,
            # otherwise the row would report 'exhausted' for the wrong reason.
            max_uses=max(max_uses,row["uses"])
            conn.execute("UPDATE referral_codes SET max_uses=? WHERE id=?",(max_uses,code_id))
        if "ttl_days" in payload:
            conn.execute("UPDATE referral_codes SET expires_at=? WHERE id=?",(parse_expires_at(payload["ttl_days"]),code_id))
        if "note" in payload:
            conn.execute("UPDATE referral_codes SET note=? WHERE id=?",(clean_text(payload["note"] or "",200),code_id))
        updated=conn.execute("SELECT * FROM referral_codes WHERE id=?",(code_id,)).fetchone()
        add_audit(conn,admin["id"],"referral.code_updated",None,row["owner_user_id"],
                  f"Updated referral code #{code_id}: {payload}")
        return {"ok":True,"code":referral_code_json(updated)}


@app.delete("/api/admin/referrals/{code_id}")
async def admin_delete_referral_code(request: Request,code_id:int)->dict[str,Any]:
    admin,session=require_admin(request);require_csrf(request,session)
    with db() as conn:
        row=conn.execute("SELECT * FROM referral_codes WHERE id=?",(code_id,)).fetchone()
        if not row: raise HTTPException(status_code=404,detail="Referral code not found")
        # Registrations keep their fingerprint, so the audit trail survives.
        conn.execute("DELETE FROM referral_codes WHERE id=?",(code_id,))
        add_audit(conn,admin["id"],"referral.code_deleted",None,row["owner_user_id"],
                  f"Deleted referral code #{code_id} (uses={row['uses']})")
    return {"ok":True}


@app.post("/api/admin/users/{user_id}/code-permission")
async def admin_user_code_permission(request: Request,user_id:int,payload:dict[str,Any])->dict[str,Any]:
    """Per-user grant of the code-generation permission.

    Defaults to disabled for every account. Revoking it also retires any code
    the user already holds, so a single switch fully removes their ability to
    bring new accounts into the system.
    """
    admin,session=require_admin(request);require_csrf(request,session)
    enabled=bool(payload.get("can_generate_codes"))
    with db() as conn:
        target=user_by_id(conn,user_id)
        if not target: raise HTTPException(status_code=404,detail="User not found")
        if target["id"]==admin["id"] and not enabled:
            raise HTTPException(status_code=400,detail="You cannot remove your own code generation permission")
        conn.execute("UPDATE users SET can_generate_codes=?,updated_at=? WHERE id=?",(1 if enabled else 0,now_iso(),user_id))
        retired=0
        if not enabled:
            cur=conn.execute("UPDATE referral_codes SET is_active=0 WHERE owner_user_id=? AND is_active=1",(user_id,))
            retired=cur.rowcount or 0
        add_audit(conn,admin["id"],"referral.permission",None,user_id,
                  f"Code generation for {target['username']} set to {'on' if enabled else 'off'}; retired_codes={retired}")
        if target["id"]!=admin["id"]:
            add_notification(conn,user_id,"referral","Code generation updated",
                             "Bạn đã được bật quyền tạo mã giới thiệu." if enabled
                             else "Quyền tạo mã giới thiệu của bạn đã bị tắt.")
        updated=conn.execute("SELECT * FROM users WHERE id=?",(user_id,)).fetchone()
        return {"ok":True,"user":user_row(updated),"retired_codes":retired}


# ------------------------ admin users ------------------------

@app.post("/api/admin/users")
async def admin_create_user(request: Request,payload:dict[str,Any])->dict[str,Any]:
    admin,session=require_admin(request);require_csrf(request,session)
    username=str(payload.get("username") or "").strip();password=str(payload.get("password") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}",username):raise HTTPException(status_code=400,detail="Username must be 3-40 chars: letters, numbers, _, ., -")
    if len(password)<10:raise HTTPException(status_code=400,detail="Password must be at least 10 characters")
    with db() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username=?",(username,)).fetchone():raise HTTPException(status_code=409,detail="Username already exists")
        public_id=generate_public_id("U");key=generate_api_key();created=now_iso();cur=conn.execute("INSERT INTO users(public_id,username,password_hash,role,api_key_hash,api_key_prefix,is_active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",(public_id,username,hash_secret(password),"user",hash_secret(key),key[:16],1,created,created))
        add_audit(conn,admin["id"],"user.created",None,cur.lastrowid,f"Created user {username} ({public_id})")
        u=conn.execute("SELECT * FROM users WHERE id=?",(cur.lastrowid,)).fetchone()
        return {"ok":True,"user":user_row(u),"api_key":key}


@app.post("/api/admin/users/{user_id}/status")
async def admin_user_status(request: Request,user_id:int,payload:dict[str,Any])->dict[str,Any]:
    admin,session=require_admin(request);require_csrf(request,session);active=bool(payload.get("is_active"))
    with db() as conn:
        u=user_by_id(conn,user_id)
        if not u or u["role"]!='user':raise HTTPException(status_code=404,detail="User not found")
        conn.execute("UPDATE users SET is_active=?,updated_at=? WHERE id=?",(1 if active else 0,now_iso(),user_id))
        if not active:conn.execute("DELETE FROM sessions WHERE user_id=?",(user_id,))
        add_audit(conn,admin["id"],"user.status",None,user_id,f"User {u['username']} active={active}")
        invalidate_api_key_cache(u["api_key_prefix"])
        if not active:
            invalidate_access_caches(user_id)
    return {"ok":True}


@app.patch("/api/admin/users/{user_id}")
async def admin_update_user(request: Request,user_id:int,payload:dict[str,Any])->dict[str,Any]:
    admin,session=require_admin(request);require_csrf(request,session)
    username=str(payload.get("username") or "").strip()
    role=str(payload.get("role") or "user").strip().lower()
    new_password=str(payload.get("new_password") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}",username):
        raise HTTPException(status_code=400,detail="Username must be 3-40 chars: letters, numbers, _, ., -")
    if role not in {"admin","user"}:
        raise HTTPException(status_code=400,detail="Role must be admin or user")
    if new_password and len(new_password)<10:
        raise HTTPException(status_code=400,detail="New password must be at least 10 characters")
    with db() as conn:
        target=user_by_id(conn,user_id)
        if not target: raise HTTPException(status_code=404,detail="User not found")
        if target["id"]==admin["id"] and role!="admin":
            raise HTTPException(status_code=400,detail="You cannot remove your own admin role")
        if target["role"]=="admin" and role!="admin":
            admins=conn.execute("SELECT COUNT(*) c FROM users WHERE role='admin' AND is_active=1").fetchone()["c"]
            if admins<=1: raise HTTPException(status_code=400,detail="At least one active admin is required")
        if conn.execute("SELECT 1 FROM users WHERE username=? AND id<>?",(username,user_id)).fetchone():
            raise HTTPException(status_code=409,detail="Username already exists")
        updated=now_iso()
        if new_password:
            conn.execute("UPDATE users SET username=?,role=?,password_hash=?,updated_at=? WHERE id=?",(username,role,hash_secret(new_password),updated,user_id))
            conn.execute("DELETE FROM sessions WHERE user_id=?",(user_id,))
        else:
            conn.execute("UPDATE users SET username=?,role=?,updated_at=? WHERE id=?",(username,role,updated,user_id))
        add_audit(conn,admin["id"],"user.updated",None,user_id,f"Updated user {target['username']} -> {username}; role={role}; password_reset={bool(new_password)}")
        updated_user=conn.execute("SELECT * FROM users WHERE id=?",(user_id,)).fetchone()
        return {"ok":True,"user":user_row(updated_user)}


@app.get("/api/admin/mailboxes/search")
async def admin_search_mailboxes(request: Request,q: str="",user_id: int=0,limit: int=30)->dict[str,Any]:
    require_admin(request)
    query=normalize_email(q)
    if len(query)<2: return {"items":[]}
    limit=max(1,min(limit,100))
    with db() as conn:
        # ILIKE on PostgreSQL so the search is case-insensitive on both backends.
        rows=conn.execute(f"""SELECT m.id,m.address,m.owner_user_id,m.exact_override,
                   EXISTS(SELECT 1 FROM mailbox_access a WHERE a.mailbox_id=m.id AND a.user_id=?) assigned
                   FROM mailboxes m WHERE m.address {dbm.like_operator()} ? ORDER BY m.address LIMIT ?""",(user_id,f"%{query}%",limit)).fetchall()
        items=[]
        for row in rows:
            item=dict(row)
            item["assigned"]=bool(item["assigned"])
            item["exact_override"]=bool(item["exact_override"])
            items.append(item)
        return {"items":items}



@app.get("/api/admin/users/{user_id}/detail")
async def admin_user_detail(request: Request,user_id:int)->dict[str,Any]:
    require_admin(request)
    with db() as conn:
        u=user_by_id(conn,user_id)
        if not u: raise HTTPException(status_code=404,detail="User not found")
        mrows=conn.execute("""SELECT m.*,ma.source,ma.rule_id FROM mailboxes m JOIN mailbox_access ma ON ma.mailbox_id=m.id WHERE ma.user_id=? ORDER BY m.address""",(user_id,)).fetchall()
        rrows=conn.execute("SELECT * FROM routing_rules WHERE target_user_id=? ORDER BY is_active DESC,updated_at DESC",(user_id,)).fetchall()
        return {"user":user_row(u),"mailboxes":[dict(x) for x in mrows],"rules":[dict(x) for x in rrows]}


# ------------------------ notifications ---------------------

@app.post("/api/notifications/read")
async def notifications_read(request: Request)->dict[str,bool]:
    user,session=require_session(request);require_csrf(request,session)
    with db() as conn:conn.execute("UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL",(now_iso(),user["id"]))
    return {"ok":True}


# ------------------------- audit -----------------------------

@app.get("/api/admin/audit")
async def admin_audit(request: Request,limit:int=300)->dict[str,Any]:
    require_admin(request);limit=max(1,min(limit,1000))
    with db() as conn:
        rows=conn.execute("""SELECT a.*,au.username actor_username,tu.username target_username,m.address mailbox_address
                            FROM audit_logs a LEFT JOIN users au ON au.id=a.actor_user_id
                            LEFT JOIN users tu ON tu.id=a.target_user_id LEFT JOIN mailboxes m ON m.id=a.mailbox_id
                            ORDER BY a.created_at DESC,a.id DESC LIMIT ?""",(limit,)).fetchall()
        return {"items":[dict(r) for r in rows]}


# ----------------------- developer API -----------------------

@app.get("/api/v1/mailboxes")
async def api_v1_mailboxes(request: Request) -> dict[str, Any]:
    """List mailboxes the caller can read (id + address)."""
    user, _ = require_auth(request)
    with db() as conn:
        rows = visible_mailboxes(conn, user)
        return {"items": [{"id": r["id"], "address": r["address"]} for r in rows]}


@app.get("/api/v1/inbox")
async def api_v1_inbox(
    request: Request,
    limit: int = 50,
    since: str = "",
    address: str = "",
    mailbox_id: int = 0,
    otp_only: int = 0,
    include_body: int = 0,
) -> dict[str, Any]:
    """Poll inbox for automation. Prefer address + otp_only + since."""
    user, _ = require_auth(request)
    limit = max(1, min(limit, 200))
    addr = normalize_email(address) if address else ""
    with db() as conn:
        params: list[Any] = []
        joins = ""
        where = ["1=1"]
        if user["role"] != "admin":
            joins = " JOIN mailbox_access a ON a.mailbox_id=m.id AND a.user_id=? "
            params.append(user["id"])
        if mailbox_id:
            if not has_access(conn, user, mailbox_id):
                raise HTTPException(status_code=404, detail="Mailbox not found")
            where.append("msg.mailbox_id=?")
            params.append(mailbox_id)
        if addr:
            if not valid_email(addr):
                raise HTTPException(status_code=400, detail="Invalid address")
            where.append("m.address=?")
            params.append(addr)
        if since:
            where.append("msg.received_at>?")
            params.append(since)
        if otp_only:
            where.append("COALESCE(msg.otp,'')<>''")
        body_select = ", msg.body_text" if include_body else ""
        rows = conn.execute(
            f"""SELECT msg.id,msg.mailbox_id,m.address,msg.envelope_from,msg.envelope_to,msg.subject,
                       msg.received_at,msg.otp,msg.has_attachments,
                       substr(replace(replace(COALESCE(msg.body_text,''),'\n',' '),'\r',' '),1,220) preview
                       {body_select}
                FROM messages msg JOIN mailboxes m ON m.id=msg.mailbox_id {joins}
                WHERE {' AND '.join(where)}
                ORDER BY msg.received_at DESC, msg.id DESC LIMIT ?""",
            params + [limit],
        ).fetchall()
        items = []
        for r in rows:
            item = {
                "id": r["id"],
                "mailbox_id": r["mailbox_id"],
                "address": r["address"],
                "envelope_from": r["envelope_from"],
                "envelope_to": r["envelope_to"],
                "subject": r["subject"],
                "received_at": r["received_at"],
                "otp": r["otp"],
                "has_attachments": r["has_attachments"],
                "preview": r["preview"] or "",
            }
            if include_body:
                item["body_text"] = r["body_text"] or ""
            items.append(item)
        return {"items": items}


@app.get("/api/v1/messages/{message_id}")
async def api_v1_message(request: Request, message_id: int) -> dict[str, Any]:
    user, _ = require_auth(request)
    with db() as conn:
        row = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if not row or not has_access(conn, user, row["mailbox_id"]):
            raise HTTPException(status_code=404, detail="Message not found")
        otp = maybe_repair_otp(conn, row)
        mailbox = mailbox_by_id(conn, row["mailbox_id"])
        attachments = conn.execute(
            "SELECT id,filename,content_type,disposition,size_bytes FROM attachments WHERE message_id=? ORDER BY id",
            (message_id,),
        ).fetchall()
        return {
            "id": row["id"],
            "mailbox_id": row["mailbox_id"],
            "address": mailbox["address"] if mailbox else None,
            "envelope_from": row["envelope_from"],
            "envelope_to": row["envelope_to"],
            "subject": row["subject"],
            "message_id": row["message_id"],
            "received_at": row["received_at"],
            "otp": otp,
            "body_text": row["body_text"] or "",
            "has_html": bool(row["body_html"]),
            "has_attachments": row["has_attachments"],
            "attachments": [dict(x) for x in attachments],
        }


@app.get("/api/v1/messages/{message_id}/raw")
async def api_v1_raw(request: Request, message_id: int) -> Response:
    user, _ = require_auth(request)
    with db() as conn:
        r = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if not r or not has_access(conn, user, r["mailbox_id"]):
            raise HTTPException(status_code=404, detail="Message not found")
        return Response(
            content=message_raw_bytes(r),
            media_type="message/rfc822",
            headers={"Content-Disposition": f"inline; filename=message-{message_id}.eml"},
        )


# --------------------- admin retention ------------------------

@app.get("/api/admin/retention/settings")
async def admin_retention_get(request: Request) -> dict[str, Any]:
    require_admin(request)
    with db() as conn:
        db_path = DATABASE_PATH if not USING_POSTGRES else None
        db_size = os.path.getsize(db_path) if db_path and os.path.exists(db_path) else 0
        total_messages = conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
        retention_days = auto_delete_retention_days(conn)
        cutoff = (utc_now() - timedelta(days=retention_days)).isoformat()
        old_count = conn.execute("SELECT COUNT(*) c FROM messages WHERE received_at <= ?", (cutoff,)).fetchone()["c"]
        return {
            "auto_delete_enabled": auto_delete_open(conn),
            "auto_delete_days": retention_days,
            "total_messages": total_messages,
            "old_messages": old_count,
            "db_size_bytes": db_size,
        }


@app.patch("/api/admin/retention/settings")
async def admin_retention_patch(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    admin, session = require_admin(request)
    require_csrf(request, session)
    with db() as conn:
        changed = []
        if "auto_delete_enabled" in payload:
            val = payload["auto_delete_enabled"]
            if isinstance(val, str):
                val = val.strip().lower() in {"1", "true", "yes", "on"}
            set_setting(conn, SETTING_AUTO_DELETE_ENABLED, bool(val))
            changed.append(f"auto_delete_enabled={'on' if val else 'off'}")
        if "auto_delete_days" in payload:
            try:
                days = max(1, int(payload["auto_delete_days"]))
                conn.execute(
                    """INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                    (SETTING_AUTO_DELETE_DAYS, str(days), now_iso()),
                )
                SETTINGS_CACHE.set(SETTING_AUTO_DELETE_DAYS, str(days))
                changed.append(f"auto_delete_days={days}")
            except (ValueError, TypeError):
                pass
        if changed:
            add_audit(conn, admin["id"], "retention.settings", None, None, "Updated " + ", ".join(changed))
        return {
            "ok": True,
            "auto_delete_enabled": auto_delete_open(conn),
            "auto_delete_days": auto_delete_retention_days(conn),
        }


@app.post("/api/admin/retention/cleanup")
async def admin_retention_cleanup(request: Request, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    admin, session = require_admin(request)
    require_csrf(request, session)
    payload = payload or {}
    days = payload.get("days")
    with db() as conn:
        deleted = purge_old_messages(conn, days=days)
        add_audit(conn, admin["id"], "retention.cleanup", None, None, f"Manual cleanup: purged {deleted} messages")
        return {"ok": True, "deleted_count": deleted}


# ------------------------- domains ----------------------------

@app.get("/api/domains")
async def api_domains(request: Request) -> dict[str, Any]:
    user, _ = require_session(request)
    with db() as conn:
        return {"items": list_domains_for_user(conn, user)}


@app.post("/api/admin/domains")
async def admin_create_domain(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    admin, session = require_admin(request)
    require_csrf(request, session)
    name = str(payload.get("name") or "").strip().lower()
    access_level = str(payload.get("access_level") or "public").strip().lower()
    description = str(payload.get("description") or "").strip()
    if access_level not in {"public", "vip", "approval_only", "disabled"}:
        raise HTTPException(status_code=400, detail="access_level không hợp lệ (public, vip, approval_only, disabled)")
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", name):
        raise HTTPException(status_code=400, detail="Tên domain không hợp lệ (ví dụ: example.com)")
    now = now_iso()
    with db() as conn:
        if conn.execute("SELECT 1 FROM domains WHERE name=?", (name,)).fetchone():
            raise HTTPException(status_code=409, detail="Domain đã tồn tại")
        cur = conn.execute(
            "INSERT INTO domains(name,access_level,description,is_active,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (name, access_level, description, 1, now, now),
        )
        add_audit(conn, admin["id"], "domain.create", None, None, f"Tạo domain {name} ({access_level})")
        return {"ok": True, "id": cur.lastrowid, "name": name}


@app.patch("/api/admin/domains/{domain_id}")
async def admin_update_domain(request: Request, domain_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    admin, session = require_admin(request)
    require_csrf(request, session)
    with db() as conn:
        d = conn.execute("SELECT * FROM domains WHERE id=?", (domain_id,)).fetchone()
        if not d:
            raise HTTPException(status_code=404, detail="Domain không tồn tại")
        access_level = str(payload.get("access_level") or d["access_level"]).strip().lower()
        if access_level not in {"public", "vip", "approval_only", "disabled"}:
            raise HTTPException(status_code=400, detail="access_level không hợp lệ")
        is_active = 1 if payload.get("is_active", bool(d["is_active"])) else 0
        description = str(payload.get("description", d["description"] or "")).strip()
        now = now_iso()
        conn.execute(
            "UPDATE domains SET access_level=?, is_active=?, description=?, updated_at=? WHERE id=?",
            (access_level, is_active, description, now, domain_id),
        )
        add_audit(conn, admin["id"], "domain.update", None, None, f"Cập nhật domain {d['name']} -> {access_level}, active={is_active}")
        return {"ok": True}


@app.delete("/api/admin/domains/{domain_id}")
async def admin_delete_domain(request: Request, domain_id: int) -> dict[str, Any]:
    admin, session = require_admin(request)
    require_csrf(request, session)
    with db() as conn:
        d = conn.execute("SELECT * FROM domains WHERE id=?", (domain_id,)).fetchone()
        if not d:
            raise HTTPException(status_code=404, detail="Domain không tồn tại")
        count = conn.execute("SELECT COUNT(*) c FROM domains").fetchone()["c"]
        if count <= 1:
            raise HTTPException(status_code=400, detail="Không thể xoá domain duy nhất còn lại trong hệ thống")
        conn.execute("DELETE FROM domains WHERE id=?", (domain_id,))
        add_audit(conn, admin["id"], "domain.delete", None, None, f"Xoá domain {d['name']}")
        return {"ok": True}


# -------------------- mailbox requests ------------------------

@app.get("/api/mailbox-requests")
async def api_mailbox_requests(request: Request, status: str = "") -> dict[str, Any]:
    user, _ = require_session(request)
    with db() as conn:
        if user["role"] == "admin":
            where = []
            params: list[Any] = []
            if status:
                where.append("mr.status=?")
                params.append(status)
            where_sql = ("WHERE " + " AND ".join(where)) if where else ""
            rows = conn.execute(f"""
                SELECT mr.*, u.username, u.public_id,
                       ru.username reviewer_username
                FROM mailbox_requests mr
                JOIN users u ON u.id=mr.user_id
                LEFT JOIN users ru ON ru.id=mr.reviewed_by
                {where_sql}
                ORDER BY CASE WHEN mr.status='pending' THEN 0 ELSE 1 END, mr.created_at DESC
                LIMIT 200
            """, params).fetchall()
            return {"items": [dict(r) for r in rows]}
        else:
            rows = conn.execute("""
                SELECT mr.*, ru.username reviewer_username
                FROM mailbox_requests mr
                LEFT JOIN users ru ON ru.id=mr.reviewed_by
                WHERE mr.user_id=?
                ORDER BY mr.created_at DESC
                LIMIT 100
            """, (user["id"],)).fetchall()
            return {"items": [dict(r) for r in rows]}


@app.post("/api/mailbox-requests")
async def api_create_mailbox_request(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    user, session = require_session(request)
    require_csrf(request, session)
    desired = normalize_email(str(payload.get("desired_address") or ""))
    reason = str(payload.get("reason") or "").strip()
    if not valid_email(desired):
        raise HTTPException(status_code=400, detail="Địa chỉ email không hợp lệ")
    now = now_iso()
    with db() as conn:
        if conn.execute("SELECT 1 FROM mailboxes WHERE address=?", (desired,)).fetchone():
            raise HTTPException(status_code=409, detail="Địa chỉ email này đã tồn tại trong hệ thống")
        if conn.execute("SELECT 1 FROM mailbox_requests WHERE desired_address=? AND status='pending'", (desired,)).fetchone():
            raise HTTPException(status_code=409, detail="Đang có một yêu cầu chờ duyệt cho địa chỉ này")
        cur = conn.execute(
            "INSERT INTO mailbox_requests(user_id, desired_address, reason, status, created_at, updated_at) VALUES(?,?,?,?,?,?)",
            (user["id"], desired, reason, "pending", now, now),
        )
        for a in conn.execute("SELECT id FROM users WHERE role='admin'").fetchall():
            add_notification(conn, a["id"], "request", "Yêu cầu tạo Mailbox mới", f"Người dùng @{user['username']} xin cấp mailbox {desired}")
        return {"ok": True, "id": cur.lastrowid, "notice": "Đã gửi yêu cầu cấp Mailbox tới Quản trị viên."}


@app.post("/api/admin/mailbox-requests/{request_id}/approve")
async def admin_approve_request(request: Request, request_id: int) -> dict[str, Any]:
    admin, session = require_admin(request)
    require_csrf(request, session)
    now = now_iso()
    with db() as conn:
        req = conn.execute("SELECT * FROM mailbox_requests WHERE id=?", (request_id,)).fetchone()
        if not req:
            raise HTTPException(status_code=404, detail="Yêu cầu không tồn tại")
        if req["status"] != "pending":
            raise HTTPException(status_code=400, detail=f"Yêu cầu này đã ở trạng thái {req['status']}")
        addr = req["desired_address"]
        uid = req["user_id"]
        
        mb = conn.execute("SELECT id FROM mailboxes WHERE address=?", (addr,)).fetchone()
        if mb:
            mb_id = mb["id"]
            conn.execute("UPDATE mailboxes SET owner_user_id=?, exact_override=1, override_reason='duyet don', updated_at=? WHERE id=?", (uid, now, mb_id))
        else:
            cur = conn.execute(
                "INSERT INTO mailboxes(address, owner_user_id, exact_override, override_reason, created_at, updated_at) VALUES(?,?,?,?,?,?)",
                (addr, uid, 1, "duyet don tao email", now, now)
            )
            mb_id = cur.lastrowid
        
        conn.execute(
            "INSERT INTO mailbox_access(mailbox_id, user_id, source, granted_by, granted_at) VALUES(?,?,?,?,?) ON CONFLICT(mailbox_id, user_id) DO NOTHING",
            (mb_id, uid, "approval", admin["id"], now)
        )
        conn.execute(
            "UPDATE mailbox_requests SET status='approved', reviewed_by=?, updated_at=? WHERE id=?",
            (admin["id"], now, request_id)
        )
        add_notification(conn, uid, "approval", "Yêu cầu Mailbox đã được DUYỆT! 🎉", f"Địa chỉ {addr} đã được tạo và sẵn sàng sử dụng.", mb_id)
        add_audit(conn, admin["id"], "request.approve", mb_id, uid, f"Duyệt cấp mailbox {addr} cho user #{uid}")
        invalidate_access_caches(uid)
        return {"ok": True, "notice": f"Đã duyệt và khởi tạo mailbox {addr} thành công!"}


@app.post("/api/admin/mailbox-requests/{request_id}/reject")
async def admin_reject_request(request: Request, request_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    admin, session = require_admin(request)
    require_csrf(request, session)
    note = str(payload.get("admin_note") or "").strip()
    now = now_iso()
    with db() as conn:
        req = conn.execute("SELECT * FROM mailbox_requests WHERE id=?", (request_id,)).fetchone()
        if not req:
            raise HTTPException(status_code=404, detail="Yêu cầu không tồn tại")
        if req["status"] != "pending":
            raise HTTPException(status_code=400, detail=f"Yêu cầu này đã ở trạng thái {req['status']}")
        conn.execute(
            "UPDATE mailbox_requests SET status='rejected', admin_note=?, reviewed_by=?, updated_at=? WHERE id=?",
            (note, admin["id"], now, request_id)
        )
        reason_msg = f" Lý do: {note}" if note else ""
        add_notification(conn, req["user_id"], "rejection", "Yêu cầu Mailbox bị từ chối", f"Yêu cầu cấp {req['desired_address']} đã bị từ chối.{reason_msg}")
        add_audit(conn, admin["id"], "request.reject", None, req["user_id"], f"Từ chối yêu cầu {req['desired_address']}: {note}")
        return {"ok": True, "notice": "Đã từ chối yêu cầu."}


# -------------------- system stats & vacuum -------------------

@app.get("/api/admin/system/stats")
async def admin_system_stats(request: Request) -> dict[str, Any]:
    require_admin(request)
    with db() as conn:
        db_path = DATABASE_PATH if not USING_POSTGRES else None
        db_size = os.path.getsize(db_path) if db_path and os.path.exists(db_path) else 0
        total_messages = conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
        retention_days = auto_delete_retention_days(conn)
        cutoff = (utc_now() - timedelta(days=retention_days)).isoformat()
        old_count = conn.execute("SELECT COUNT(*) c FROM messages WHERE received_at <= ?", (cutoff,)).fetchone()["c"]
        total_attachments = conn.execute("SELECT COUNT(*) c FROM attachments").fetchone()["c"]
        total_att_bytes = conn.execute("SELECT CAST(COALESCE(SUM(size_bytes),0) AS INTEGER) c FROM attachments").fetchone()["c"]
        total_mailboxes = conn.execute("SELECT COUNT(*) c FROM mailboxes").fetchone()["c"]
        total_users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        total_rules = conn.execute("SELECT COUNT(*) c FROM routing_rules").fetchone()["c"]
        total_domains = conn.execute("SELECT COUNT(*) c FROM domains").fetchone()["c"]
        pending_requests = conn.execute("SELECT COUNT(*) c FROM mailbox_requests WHERE status='pending'").fetchone()["c"]

        return {
            "db_engine": "PostgreSQL" if USING_POSTGRES else "SQLite",
            "db_size_bytes": db_size,
            "koyeb_disk_limit_mb": 2000,
            "auto_delete_enabled": auto_delete_open(conn),
            "auto_delete_days": retention_days,
            "total_messages": total_messages,
            "old_messages": old_count,
            "total_attachments": total_attachments,
            "total_att_bytes": total_att_bytes,
            "total_mailboxes": total_mailboxes,
            "total_users": total_users,
            "total_rules": total_rules,
            "total_domains": total_domains,
            "pending_requests": pending_requests,
        }


@app.post("/api/admin/system/vacuum")
async def admin_system_vacuum(request: Request) -> dict[str, Any]:
    admin, session = require_admin(request)
    require_csrf(request, session)
    if USING_POSTGRES:
        return {"ok": True, "notice": "VACUUM chỉ áp dụng cho SQLite. PostgreSQL chạy VACUUM tự động."}
    
    db_path = DATABASE_PATH
    size_before = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    try:
        raw = sqlite3.connect(db_path, timeout=60)
        raw.execute("VACUUM")
        raw.close()
        trim_memory()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi khi chạy VACUUM: {e}")
    
    size_after = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    freed = max(0, size_before - size_after)
    freed_mb = round(freed / (1024 * 1024), 2)
    with db() as conn:
        add_audit(conn, admin["id"], "system.vacuum", None, None, f"Chạy VACUUM SQLite: giải phóng {freed_mb} MB")
    return {"ok": True, "size_before": size_before, "size_after": size_after, "freed_bytes": freed, "notice": f"Đã tối ưu database! Giải phóng thành công {freed_mb} MB đĩa."}


@app.get("/api/admin/backup/sqlite")
async def admin_backup_sqlite(request: Request):
    require_admin(request)
    if USING_POSTGRES:
        raise HTTPException(status_code=400, detail="Hệ thống đang chạy PostgreSQL. Hãy sử dụng JSON backup.")
    if not os.path.exists(DATABASE_PATH):
        raise HTTPException(status_code=404, detail="File cơ sở dữ liệu không tồn tại")
    
    filename = f"otp-inbox-backup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.db"
    return FileResponse(
        DATABASE_PATH,
        media_type="application/x-sqlite3",
        filename=filename,
    )


@app.get("/api/admin/backup/json")
async def admin_backup_json(request: Request):
    require_admin(request)
    with db() as conn:
        users = [user_row(x) for x in conn.execute("SELECT * FROM users").fetchall()]
        mailboxes = [dict(x) for x in conn.execute("SELECT id, address, owner_user_id, exact_override, override_reason, created_at, updated_at FROM mailboxes").fetchall()]
        access = [dict(x) for x in conn.execute("SELECT * FROM mailbox_access").fetchall()]
        rules = [dict(x) for x in conn.execute("SELECT * FROM routing_rules").fetchall()]
        domains = [dict(x) for x in conn.execute("SELECT * FROM domains").fetchall()]
        settings = [dict(x) for x in conn.execute("SELECT * FROM app_settings").fetchall()]
        referrals = [dict(x) for x in conn.execute("SELECT id, code_fingerprint, owner_user_id, max_uses, uses, expires_at, is_active, note, created_at FROM referral_codes").fetchall()]
        
        dump = {
            "version": "1.0",
            "exported_at": now_iso(),
            "users": users,
            "domains": domains,
            "mailboxes": mailboxes,
            "mailbox_access": access,
            "routing_rules": rules,
            "app_settings": settings,
            "referral_codes": referrals,
        }
        content = json.dumps(dump, indent=2, ensure_ascii=False)
        filename = f"otp-inbox-config-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )


@app.get("/api/export/mailboxes.csv")
async def export_mailboxes_csv(request: Request):
    user, _ = require_session(request)
    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Address", "Messages", "Unread", "Owner", "Exact Override", "Created At"])
    with db() as conn:
        visible = visible_mailboxes(conn, user)
        summaries = mailbox_summaries(conn, user, visible)
        for m in summaries:
            writer.writerow([
                m["id"],
                m["address"],
                m.get("message_count", 0),
                m.get("unread_count", 0),
                m.get("owner_username", ""),
                "Yes" if m.get("exact_override") else "No",
                m.get("created_at", "")
            ])
    csv_data = output.getvalue()
    filename = f"mailboxes-{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
    return Response(
        content=csv_data.encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.get("/api/export/otps.csv")
async def export_otps_csv(request: Request):
    user, _ = require_session(request)
    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Time", "Mailbox", "From", "Subject", "OTP"])
    with db() as conn:
        params: list[Any] = []
        where = ["COALESCE(msg.otp,'') <> ''"]
        joins = ""
        if user["role"] != "admin":
            joins = " JOIN mailbox_access a ON a.mailbox_id=m.id AND a.user_id=? "
            params.append(user["id"])
        rows = conn.execute(f"""
            SELECT msg.id, msg.received_at, m.address, msg.envelope_from, msg.subject, msg.otp
            FROM messages msg JOIN mailboxes m ON m.id=msg.mailbox_id {joins}
            WHERE {' AND '.join(where)}
            ORDER BY msg.received_at DESC LIMIT 2000
        """, params).fetchall()
        for r in rows:
            writer.writerow([
                r["id"],
                r["received_at"],
                r["address"],
                r["envelope_from"],
                r["subject"],
                r["otp"] or ""
            ])
    csv_data = output.getvalue()
    filename = f"otp-history-{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
    return Response(
        content=csv_data.encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )
