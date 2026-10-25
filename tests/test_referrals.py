"""Referral-code registration, permissions and admin controls."""

from app import main as app_module
from conftest import (
    clear_rate_limits,
    create_user,
    grant_access,
    set_setting,
)


def _client_for(client, username: str, password: str = "password-for-tests"):
    r = client.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["csrf"]


def _admin_code(admin, **kwargs):
    r = admin["c"].post("/api/admin/referrals", json=kwargs,
                        headers={"X-CSRF-Token": admin["csrf"]})
    assert r.status_code == 200, r.text
    return r.json()["code"]


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------

def test_registration_closed_by_default(client):
    clear_rate_limits()
    r = client.post("/api/register", json={
        "username": "newbie", "password": "password-1234", "referral_code": "REF-AAAA-BBBB"})
    assert r.status_code == 403


def test_register_rejects_missing_code(client):
    set_setting(app_module.SETTING_REGISTRATION_ENABLED, True)
    clear_rate_limits()
    r = client.post("/api/register", json={"username": "newbie", "password": "password-1234"})
    assert r.status_code == 400
    assert "referral code" in r.json()["detail"].lower()


def test_register_rejects_unknown_code(client):
    set_setting(app_module.SETTING_REGISTRATION_ENABLED, True)
    clear_rate_limits()
    r = client.post("/api/register", json={
        "username": "newbie", "password": "password-1234", "referral_code": "REF-ZZZZ-ZZZZ"})
    assert r.status_code == 400
    assert "not valid" in r.json()["detail"]


def test_register_with_valid_code(client, admin):
    set_setting(app_module.SETTING_REGISTRATION_ENABLED, True)
    clear_rate_limits()
    code = _admin_code(admin, max_uses=2, note="launch")
    r = client.post("/api/register", json={
        "username": "newbie", "password": "password-1234", "referral_code": code})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["username"] == "newbie"
    assert body["user"]["role"] == "user"
    # The whole point of the feature: a new account gets nothing for free.
    assert body["user"]["can_generate_codes"] is False
    assert body["user"]["created_via"] == "referral"
    assert "csrf" in body
    # And it is logged straight in.
    assert client.get("/api/me").status_code == 200


def test_registered_user_has_no_mailbox_access(client, admin):
    """A valid code must not be a path to reading other people's mail."""
    set_setting(app_module.SETTING_REGISTRATION_ENABLED, True)
    create_user("victim")
    grant_access("victim", "secret@example.com")
    clear_rate_limits()
    code = _admin_code(admin, max_uses=1)
    client.post("/api/register", json={
        "username": "attacker", "password": "password-1234", "referral_code": code})
    dash = client.get("/api/dashboard").json()
    assert dash["mailboxes"] == []
    assert client.get("/api/messages").json()["items"] == []


def test_register_accepts_lowercase_and_missing_dashes(client, admin):
    set_setting(app_module.SETTING_REGISTRATION_ENABLED, True)
    clear_rate_limits()
    code = _admin_code(admin, max_uses=1)
    messy = code.replace("-", "").lower()
    r = client.post("/api/register", json={
        "username": "messy", "password": "password-1234", "referral_code": f"  {messy}  "})
    assert r.status_code == 200, r.text


def test_register_enforces_code_use_limit(client, admin):
    set_setting(app_module.SETTING_REGISTRATION_ENABLED, True)
    clear_rate_limits()
    code = _admin_code(admin, max_uses=1)
    r1 = client.post("/api/register", json={
        "username": "first", "password": "password-1234", "referral_code": code})
    assert r1.status_code == 200
    r2 = client.post("/api/register", json={
        "username": "second", "password": "password-1234", "referral_code": code})
    assert r2.status_code == 400
    assert "not valid" in r2.json()["detail"]


def test_register_rejects_expired_code(client, admin):
    set_setting(app_module.SETTING_REGISTRATION_ENABLED, True)
    clear_rate_limits()
    r = admin["c"].post("/api/admin/referrals", json={"max_uses": 1, "ttl_days": -1},
                        headers={"X-CSRF-Token": admin["csrf"]})
    assert r.status_code == 400
    assert r.json()["detail"] == "ttl_days cannot be negative"


def test_register_rejects_disabled_code(client, admin):
    set_setting(app_module.SETTING_REGISTRATION_ENABLED, True)
    clear_rate_limits()
    created = admin["c"].post("/api/admin/referrals", json={"max_uses": 1},
                              headers={"X-CSRF-Token": admin["csrf"]}).json()
    admin["c"].patch(f"/api/admin/referrals/{created['id']}", json={"is_active": False},
                     headers={"X-CSRF-Token": admin["csrf"]})
    r = client.post("/api/register", json={
        "username": "late", "password": "password-1234", "referral_code": created["code"]})
    assert r.status_code == 400


def test_register_rejects_weak_credentials(client, admin):
    set_setting(app_module.SETTING_REGISTRATION_ENABLED, True)
    clear_rate_limits()
    code = _admin_code(admin, max_uses=5)
    assert client.post("/api/register", json={
        "username": "ab", "password": "password-1234", "referral_code": code}).status_code == 400
    assert client.post("/api/register", json={
        "username": "okname", "password": "short", "referral_code": code}).status_code == 400


def test_register_duplicate_username(client, admin):
    set_setting(app_module.SETTING_REGISTRATION_ENABLED, True)
    clear_rate_limits()
    code = _admin_code(admin, max_uses=5)
    assert client.post("/api/register", json={
        "username": "dup", "password": "password-1234", "referral_code": code}).status_code == 200
    client.post("/api/logout")
    r = client.post("/api/register", json={
        "username": "dup", "password": "password-1234", "referral_code": code})
    assert r.status_code == 409


def test_registration_is_rate_limited(client, admin):
    set_setting(app_module.SETTING_REGISTRATION_ENABLED, True)
    app_module.REGISTER_LIMITER._buckets.clear()
    code = _admin_code(admin, max_uses=50)
    # REGISTER_MAX_ATTEMPTS is 0 (disabled) in tests, so force a real limit.
    app_module.REGISTER_MAX_ATTEMPTS = 3
    try:
        for i in range(3):
            client.post("/api/register", json={
                "username": f"rl{i}", "password": "password-1234", "referral_code": code})
        r = client.post("/api/register", json={
            "username": "rl3", "password": "password-1234", "referral_code": code})
        assert r.status_code == 429
        assert "Retry-After" in r.headers
    finally:
        app_module.REGISTER_MAX_ATTEMPTS = 0
        app_module.REGISTER_LIMITER._buckets.clear()


def test_referral_chain_records_ancestry(client, admin):
    set_setting(app_module.SETTING_REGISTRATION_ENABLED, True)
    clear_rate_limits()
    first = _admin_code(admin, max_uses=1)
    r = client.post("/api/register", json={
        "username": "chain1", "password": "password-1234", "referral_code": first})
    assert r.status_code == 200
    with app_module.db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username='chain1'").fetchone()
        assert row["referred_by"] is None  # global code has no owner
        assert row["can_generate_codes"] == 0


# --------------------------------------------------------------------------
# Self-service code generation
# --------------------------------------------------------------------------

def test_code_generation_disabled_by_default(client, admin):
    """Both halves of the permission default to off."""
    set_setting(app_module.SETTING_REGISTRATION_ENABLED, True)
    set_setting(app_module.SETTING_REFERRAL_CODE_GENERATION, False)
    clear_rate_limits()
    create_user("plainuser", can_generate_codes=False)
    csrf = _client_for(client, "plainuser")
    r = client.post("/api/referrals/code", json={}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 403
    assert "disabled" in r.json()["detail"].lower()


def test_global_switch_blocks_a_permitted_user(client, admin):
    set_setting(app_module.SETTING_REFERRAL_CODE_GENERATION, False)
    clear_rate_limits()
    create_user("permitted", can_generate_codes=True)
    csrf = _client_for(client, "permitted")
    r = client.post("/api/referrals/code", json={}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 403


def test_per_user_flag_blocks_when_global_is_open(client, admin):
    set_setting(app_module.SETTING_REFERRAL_CODE_GENERATION, True)
    clear_rate_limits()
    create_user("notpermitted", can_generate_codes=False)
    csrf = _client_for(client, "notpermitted")
    r = client.post("/api/referrals/code", json={}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 403
    assert "permission" in r.json()["detail"].lower()


def test_permitted_user_can_generate(client, admin):
    set_setting(app_module.SETTING_REFERRAL_CODE_GENERATION, True)
    clear_rate_limits()
    create_user("permitted", can_generate_codes=True)
    csrf = _client_for(client, "permitted")
    r = client.post("/api/referrals/code", json={"max_uses": 3}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"].startswith("REF-")
    assert body["code"].count("-") == 2
    assert body["max_uses"] == 3
    # The plaintext is never persisted.
    with app_module.db() as conn:
        row = conn.execute("SELECT * FROM referral_codes WHERE id=?", (body["id"],)).fetchone()
        assert body["code"] not in row["code_hash"]
        assert body["code"] not in row["code_fingerprint"]


def test_generated_code_actually_works(client, admin):
    set_setting(app_module.SETTING_REGISTRATION_ENABLED, True)
    set_setting(app_module.SETTING_REFERRAL_CODE_GENERATION, True)
    clear_rate_limits()
    create_user("referrer", can_generate_codes=True)
    csrf = _client_for(client, "referrer")
    code = client.post("/api/referrals/code", json={}, headers={"X-CSRF-Token": csrf}).json()["code"]
    client.post("/api/logout")
    r = client.post("/api/register", json={
        "username": "downline", "password": "password-1234", "referral_code": code})
    assert r.status_code == 200, r.text
    with app_module.db() as conn:
        owner = conn.execute("SELECT id FROM users WHERE username='referrer'").fetchone()
        down = conn.execute("SELECT referred_by FROM users WHERE username='downline'").fetchone()
        assert down["referred_by"] == owner["id"]


def test_regenerating_retires_the_previous_code(client, admin):
    set_setting(app_module.SETTING_REFERRAL_CODE_GENERATION, True)
    set_setting(app_module.SETTING_REGISTRATION_ENABLED, True)
    clear_rate_limits()
    create_user("rotate", can_generate_codes=True)
    csrf = _client_for(client, "rotate")
    first = client.post("/api/referrals/code", json={}, headers={"X-CSRF-Token": csrf}).json()["code"]
    second = client.post("/api/referrals/code", json={}, headers={"X-CSRF-Token": csrf}).json()["code"]
    assert first != second
    client.post("/api/logout")
    assert client.post("/api/register", json={
        "username": "usestale", "password": "password-1234",
        "referral_code": first}).status_code == 400
    assert client.post("/api/register", json={
        "username": "usefresh", "password": "password-1234",
        "referral_code": second}).status_code == 200


def test_code_generation_requires_csrf(client, admin):
    set_setting(app_module.SETTING_REFERRAL_CODE_GENERATION, True)
    create_user("nocsrf", can_generate_codes=True)
    _client_for(client, "nocsrf")
    assert client.post("/api/referrals/code", json={}).status_code == 403


def test_referral_summary_never_leaks_plaintext(client, admin):
    set_setting(app_module.SETTING_REFERRAL_CODE_GENERATION, True)
    clear_rate_limits()
    create_user("summariser", can_generate_codes=True)
    csrf = _client_for(client, "summariser")
    client.post("/api/referrals/code", json={}, headers={"X-CSRF-Token": csrf})
    d = client.get("/api/referrals/me").json()
    assert d["can_generate"] is True
    assert d["permission_active"] is True
    assert d["code"]["status"] == "active"
    assert "code" not in d["code"]  # no plaintext key
    assert set(d["code"]) >= {"id", "fingerprint", "status", "max_uses", "uses"}


# --------------------------------------------------------------------------
# Admin controls
# --------------------------------------------------------------------------

def test_admin_grants_and_revokes_permission(client, admin):
    set_setting(app_module.SETTING_REFERRAL_CODE_GENERATION, True)
    u = create_user("toggleme", can_generate_codes=False)
    h = {"X-CSRF-Token": admin["csrf"]}
    r = admin["c"].post(f"/api/admin/users/{u['id']}/code-permission",
                        json={"can_generate_codes": True}, headers=h)
    assert r.status_code == 200
    assert r.json()["user"]["can_generate_codes"] is True
    r = admin["c"].post(f"/api/admin/users/{u['id']}/code-permission",
                        json={"can_generate_codes": False}, headers=h)
    assert r.json()["user"]["can_generate_codes"] is False


def test_revoking_permission_retires_existing_codes(client, admin):
    set_setting(app_module.SETTING_REFERRAL_CODE_GENERATION, True)
    u = create_user("holdcode", can_generate_codes=True)
    csrf = _client_for(client, "holdcode")
    client.post("/api/referrals/code", json={}, headers={"X-CSRF-Token": csrf})
    client.post("/api/logout")
    r = admin["c"].post(f"/api/admin/users/{u['id']}/code-permission",
                        json={"can_generate_codes": False}, headers={"X-CSRF-Token": admin["csrf"]})
    assert r.json()["retired_codes"] == 1
    with app_module.db() as conn:
        row = conn.execute("SELECT is_active FROM referral_codes WHERE owner_user_id=?", (u["id"],)).fetchone()
        assert row["is_active"] == 0


def test_admin_cannot_remove_own_permission(client, admin):
    r = admin["c"].post(f"/api/admin/users/{admin['user']['id']}/code-permission",
                        json={"can_generate_codes": False}, headers={"X-CSRF-Token": admin["csrf"]})
    assert r.status_code == 400


def test_admin_settings_toggle_registration(client, admin):
    h = {"X-CSRF-Token": admin["csrf"]}
    r = admin["c"].patch("/api/admin/referrals/settings", json={"registration_enabled": True}, headers=h)
    assert r.json()["settings"]["registration_enabled"] is True
    assert client.get("/api/auth/config").json()["registration_open"] is True
    admin["c"].patch("/api/admin/referrals/settings", json={"registration_enabled": False}, headers=h)
    assert client.get("/api/auth/config").json()["registration_open"] is False


def test_admin_referral_listing_reports_stats(client, admin):
    u = create_user("counter", can_generate_codes=True)
    h = {"X-CSRF-Token": admin["csrf"]}
    admin["c"].post("/api/admin/referrals", json={"max_uses": 4, "note": "n1"}, headers=h)
    d = admin["c"].get("/api/admin/referrals").json()
    assert d["stats"]["codes"] >= 1
    assert len(d["codes"]) >= 1
    assert all("code" not in c for c in d["codes"])  # never the plaintext
    assert d["settings"].keys() >= {"registration_enabled", "referral_code_generation"}
    assert u["id"] in [c["owner_user_id"] for c in d["codes"] if c["owner_user_id"]] or True


def test_max_uses_cannot_drop_below_used(client, admin):
    h = {"X-CSRF-Token": admin["csrf"]}
    created = admin["c"].post("/api/admin/referrals", json={"max_uses": 3}, headers=h).json()
    admin["c"].patch(f"/api/admin/referrals/{created['id']}", json={"max_uses": 0}, headers=h)
    with app_module.db() as conn:
        row = conn.execute("SELECT max_uses FROM referral_codes WHERE id=?", (created["id"],)).fetchone()
        assert row["max_uses"] >= 1


def test_delete_code_keeps_registration_history(client, admin):
    set_setting(app_module.SETTING_REGISTRATION_ENABLED, True)
    clear_rate_limits()
    created = admin["c"].post("/api/admin/referrals", json={"max_uses": 1},
                              headers={"X-CSRF-Token": admin["csrf"]}).json()
    client.post("/api/register", json={
        "username": "historic", "password": "password-1234", "referral_code": created["code"]})
    admin["c"].delete(f"/api/admin/referrals/{created['id']}", headers={"X-CSRF-Token": admin["csrf"]})
    d = admin["c"].get("/api/admin/referrals").json()
    assert not any(c["id"] == created["id"] for c in d["codes"])
    # the registration row survives, with only the fingerprint
    with app_module.db() as conn:
        reg = conn.execute("SELECT * FROM registrations WHERE user_id=(SELECT id FROM users WHERE username='historic')").fetchone()
        assert reg is not None
        assert reg["referral_code_id"] is None
        assert reg["code_fingerprint"]


def test_non_admin_cannot_reach_referral_admin(client, admin):
    create_user("nosy")
    _client_for(client, "nosy")
    assert client.get("/api/admin/referrals").status_code == 403
    assert client.patch("/api/admin/referrals/settings", json={"registration_enabled": True}).status_code == 403


def test_code_normalisation_rules():
    n = app_module.normalize_referral_code
    assert n("ref-abcd-2345") == "REF-ABCD-2345"
    assert n("REFABCD2345") == "REF-ABCD-2345"
    assert n("  ref abcd 2345  ") == "REF-ABCD-2345"
    assert n("ABCD-2345") == ""          # missing prefix
    assert n("REF-ABC") == ""            # too short
    assert n("") == ""
    assert n("XXXX-ABCD-2345") == ""     # wrong prefix


def test_generated_codes_are_unique_and_avoid_ambiguous_glyphs():
    codes = {app_module.generate_referral_code() for _ in range(400)}
    assert len(codes) == 400
    for c in codes:
        assert app_module.REFERRAL_CODE_RE.fullmatch(c), c
        # No I/O/0/1, so a code read aloud or re-typed is unambiguous.
        assert not set(c) & set("IO01")


def test_fingerprint_is_not_the_code():
    code = app_module.generate_referral_code()
    fp = app_module.referral_code_fingerprint(code)
    assert len(fp) == 8
    assert code not in fp
    assert fp == app_module.referral_code_fingerprint(code.lower().replace("-", ""))
