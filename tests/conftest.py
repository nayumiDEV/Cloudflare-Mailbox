"""Shared fixtures.

The app reads its configuration and opens its database at import time, so the
environment has to be set up before `import app` happens. Every test therefore
runs against a throwaway database created in a temp directory.
"""

import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="otp-inbox-tests-")

# Which backend to exercise. "pg" runs the whole suite through the PostgreSQL
# code path in db.py/main.py using a psycopg stand-in over real SQLite, so the
# dialect handling is covered without needing a PostgreSQL server.
BACKEND = os.getenv("OTP_TEST_BACKEND", "sqlite").lower()
_DB_FILE = str(pathlib.Path(_TMP) / "test.db")

if BACKEND == "pg":
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import pgshim
    pgshim.install(_DB_FILE)
    os.environ["DATABASE_URL"] = f"postgres://shim@localhost/{_DB_FILE}"
    os.environ.pop("DATABASE_PATH", None)
else:
    os.environ["DATABASE_PATH"] = _DB_FILE
    os.environ.pop("DATABASE_URL", None)

os.environ.update({
    "ADMIN_USERNAME": "rootadmin",
    "ADMIN_PASSWORD": "root-admin-password",
    "WORKER_INGEST_TOKEN": "test-worker-token",
    # Both feature switches start OFF; tests opt in explicitly so the default
    # behaviour is what gets asserted.
    "REGISTRATION_ENABLED": "0",
    "REFERRAL_CODE_GENERATION": "0",
    "COOKIE_SECURE": "0",
    "REGISTER_MAX_ATTEMPTS": "0",
    "LOGIN_MAX_ATTEMPTS": "0",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main as app_module  # noqa: E402

# True when the "PostgreSQL" backend is the pgshim stand-in rather than a real
# server. The shim cannot reproduce a real tsvector, so a few assertions that
# depend on body-text indexing are skipped under it.
PG_SHIM = BACKEND == "pg"


@pytest.fixture
def client():
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture(autouse=True)
def isolated_state():
    """Give every test a clean database and empty rate-limit buckets.

    The app is a single module-level singleton, so without this each test would
    see the rows, settings and rate-limit counters left by the previous one and
    the suite would only pass by accident of ordering.
    """
    clear_rate_limits()
    with app_module.db() as conn:
        for table in ("registrations", "referral_codes", "notifications", "audit_logs",
                      "sessions", "attachments", "message_reads", "messages", "mailbox_access",
                      "mailboxes", "routing_rules", "mailbox_requests", "domains"):
            conn.execute(f"DELETE FROM {table}")
        app_module.seed_domains(conn)
        # Keep the seeded admin so the `admin` fixture can log in, but drop
        # every other account, including ones from earlier tests.
        conn.execute("DELETE FROM users WHERE id<>1")
        conn.execute("DELETE FROM users")
        conn.execute(
            """INSERT INTO users(public_id,username,password_hash,role,api_key_hash,api_key_prefix,
                                 is_active,can_generate_codes,referred_by,created_via,created_ip,created_at,updated_at)
               VALUES(?,'rootadmin',?,'admin',?,'testprefix',1,1,NULL,'admin',NULL,?,?)""",
            (app_module.generate_public_id("A"), app_module.hash_secret("root-admin-password"),
             app_module.hash_secret("x" * 24), app_module.now_iso(), app_module.now_iso()),
        )
        # Settings return to the values baked into SETTING_DEFAULTS, which is
        # what "disabled by default" means.
        for key, value in app_module.SETTING_DEFAULTS.items():
            if value in ("0", "1"):
                app_module.set_setting(conn, key, value == "1")
            else:
                conn.execute(
                    """INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                    (key, str(value), app_module.now_iso()),
                )
                app_module.SETTINGS_CACHE.set(key, str(value))
        if app_module.FTS_AVAILABLE:
            conn.execute("DELETE FROM messages_fts")
    yield
    clear_rate_limits()


@pytest.fixture
def admin():
    """A logged-in admin on its own client.

    It deliberately does not reuse the `client` fixture: registering a new
    account replaces the session cookie, which would silently turn the admin
    into that new user and make every later admin call return 403.
    """
    c = TestClient(app_module.app)
    r = c.post("/api/login", json={"username": "rootadmin", "password": "root-admin-password"})
    assert r.status_code == 200, r.text
    yield {"c": c, "csrf": r.json()["csrf"], "user": r.json()["user"]}
    c.close()


@pytest.fixture
def db():
    with app_module.db() as conn:
        yield conn


def clear_rate_limits():
    """The limiters are process-global; tests must not inherit each other's hits."""
    for limiter in (
        app_module.LOGIN_LIMITER,
        app_module.REGISTER_LIMITER,
        app_module.CODE_GENERATE_LIMITER,
        app_module.API_KEY_LIMITER,
    ):
        limiter._buckets.clear()
    for cache in (
        app_module.API_KEY_CACHE,
        app_module.ACCESS_CACHE,
        app_module.RULES_CACHE,
        app_module.SETTINGS_CACHE,
        app_module.MAILBOX_ADDR_CACHE,
    ):
        cache.clear()
    app_module.LATEST_BY_MAILBOX.clear()


def set_setting(key: str, value: bool):
    with app_module.db() as conn:
        app_module.set_setting(conn, key, value)


def create_user(username: str, role: str = "user", can_generate_codes: bool = False,
                active: bool = True, referred_by=None):
    """Create a user directly, bypassing the HTTP layer.

    Returns the user row dict plus ``api_key`` (plaintext, for Developer API tests).
    """
    with app_module.db() as conn:
        public_id = app_module.generate_public_id("U" if role == "user" else "A")
        key = app_module.generate_api_key()
        now = app_module.now_iso()
        cur = conn.execute(
            """INSERT INTO users(public_id,username,password_hash,role,api_key_hash,api_key_prefix,
                                 is_active,can_generate_codes,referred_by,created_via,created_ip,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,'admin',NULL,?,?)""",
            (public_id, username, app_module.hash_secret("password-for-tests"),
             role, app_module.hash_secret(key), key[:16], 1 if active else 0,
             1 if can_generate_codes else 0, referred_by, now, now),
        )
        row = app_module.user_row(conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone())
        row["api_key"] = key
        return row


def grant_access(username: str, address: str, owner: bool = False):
    """Grant a user access the way the real endpoints do.

    A manual grant is only durable on an exact-override mailbox: a mailbox that
    is still rule-derived has its access recomputed (and manual rows dropped) on
    the next ingest, which is the intended routing behaviour.
    """
    with app_module.db() as conn:
        mailbox = app_module.get_or_create_mailbox(conn, address)
        u = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if owner:
            app_module.set_exact_override(conn, mailbox["id"], u["id"], "test owner")
        else:
            app_module.set_exact_override(conn, mailbox["id"], None, "test grant")
        conn.execute(
            "INSERT OR IGNORE INTO mailbox_access(mailbox_id,user_id,source,granted_by,granted_at) VALUES(?,?,'manual',?,?)",
            (mailbox["id"], u["id"], u["id"], app_module.now_iso()),
        )
        app_module.invalidate_access_caches(u["id"])
        return mailbox["id"]


def ingest(client, to: str, subject: str = "Test subject", body: str = "Hello body",
           frm: str = "sender@example.com", otp_hint: str = "") -> dict:
    """Push a message through the public ingest endpoint."""
    text = f"{body} Your verification code is {otp_hint or '123456'}."
    mid = abs(hash((to, subject, body)))
    raw = (
        f"From: {frm}\r\n"
        f"To: {to}\r\n"
        f"Subject: {subject}\r\n"
        f"Date: Mon, 01 Jan 2024 10:00:00 +0000\r\n"
        f"Message-ID: <{mid}@test>\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        f"{text}\r\n"
    ).encode()
    return client.post(
        "/api/ingest",
        content=raw,
        headers={
            "X-Worker-Token": "test-worker-token",
            "X-Envelope-From": frm,
            "X-Envelope-To": to,
            "X-Email-Subject": subject,
        },
    )
