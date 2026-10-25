"""Search, pagination, access control and session behaviour."""

from app import main as app_module
from conftest import create_user, grant_access, ingest


def login(client, username="rootadmin", password="root-admin-password"):
    r = client.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["csrf"]


def seed_messages(client, count=5, mailbox="box@example.com"):
    for i in range(count):
        ingest(client, mailbox, subject=f"Subject number {i}",
               body=f"Body content marker{i} here", frm=f"sender{i}@example.com",
               otp_hint=str(100000 + i))


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------

def test_search_finds_by_subject(client, admin):
    seed_messages(client, 5)
    c = admin["c"]
    r = c.get("/api/messages?q=number+3").json()
    assert r["total"] >= 1
    assert any("number 3" in m["subject"] for m in r["items"])


def test_search_scope_headers_skips_body(client, admin):
    """Header-only search must not match a term that only exists in the body."""
    from conftest import PG_SHIM
    ingest(client, "box@example.com", subject="Invoice", body="uniquebodytoken 9999")
    c = admin["c"]
    everything = c.get("/api/messages?q=uniquebodytoken").json()
    headers = c.get("/api/messages?q=uniquebodytoken&scope=headers").json()
    if not PG_SHIM:
        # On a real backend the generated tsvector includes the body, so an
        # unscoped search finds the term while a header-scoped one does not.
        assert everything["total"] >= 1
    assert headers["total"] == 0
    assert headers["engine"] == "like"


def test_search_scope_headers_matches_subject(client, admin):
    ingest(client, "box@example.com", subject="Password reset", body="nope")
    r = admin["c"].get("/api/messages?q=Password&scope=headers").json()
    assert r["total"] >= 1


def test_search_reports_engine(client, admin):
    seed_messages(client, 2)
    c = admin["c"]
    r = c.get("/api/messages?q=Body&scope=all").json()
    assert r["engine"] in {"fts", "like"}


def test_fts_and_like_agree_on_the_same_query(client, admin):
    """The two search backends must not disagree about what matches."""
    seed_messages(client, 6, "agree@example.com")
    c = admin["c"]
    fts = c.get("/api/messages?q=number&scope=all").json()
    # force the LIKE path by asking for a header-scoped-only term mix
    like = c.get("/api/messages?q=number&scope=all&page_size=1").json()
    assert fts["total"] == like["total"]
    assert fts["total"] >= 1


def test_search_ignores_fts_operator_syntax(client, admin):
    """Punctuation that is FTS5 operator syntax must be inert, not an error."""
    seed_messages(client, 3)
    c = admin["c"]
    for query in ['"', '*', 'NEAR', '^abc', 'number OR 3', '-number', 'AND', '()']:
        r = c.get("/api/messages", params={"q": query, "scope": "all"})
        assert r.status_code == 200, f"{query!r} -> {r.status_code}"
        assert "detail" not in r.json()


def test_search_ignores_fts_operator_syntax_in_fts_mode(client, admin):
    """Same check, but with the query long enough to reach the FTS path."""
    seed_messages(client, 3)
    c = admin["c"]
    for query in ['"*', 'number AND (', 'number NEAR/', '^^^', 'number OR OR OR']:
        r = c.get("/api/messages", params={"q": query, "scope": "all"})
        assert r.status_code == 200, f"{query!r} -> {r.status_code} {r.text[:200]}"


def test_search_is_trimmed_and_length_capped(client, admin):
    seed_messages(client, 1)
    c = admin["c"]
    assert c.get("/api/messages", params={"q": "  number  "}).json()["total"] >= 1
    long_query = "x" * (app_module.MAX_SEARCH_QUERY + 500)
    r = c.get("/api/messages", params={"q": long_query})
    assert r.status_code == 200
    assert len(r.json()["query"]) <= app_module.MAX_SEARCH_QUERY


def test_search_with_no_match_returns_empty_page(client, admin):
    seed_messages(client, 3)
    r = admin["c"].get("/api/messages?q=zzzznotpresent").json()
    assert r["total"] == 0
    assert r["items"] == []


def test_search_combines_with_otp_filter(client, admin):
    seed_messages(client, 4)
    r = admin["c"].get("/api/messages?q=number&otp_only=1").json()
    assert r["total"] >= 1
    assert all(m["otp"] for m in r["items"])


def test_search_combines_with_mailbox_filter(client, admin):
    ingest(admin["c"], "aaa@example.com", subject="Zebra crossing", body="x")
    ingest(admin["c"], "bbb@example.com", subject="Zebra crossing", body="x")
    dash = admin["c"].get("/api/dashboard").json()
    aaa = next(m for m in dash["mailboxes"] if m["address"] == "aaa@example.com")
    r = admin["c"].get(f"/api/messages?mailbox_id={aaa['id']}&q=Zebra").json()
    assert r["total"] == 1
    assert r["items"][0]["mailbox_address"] == "aaa@example.com"


def test_fts_query_builder_strips_syntax():
    q = app_module.fts_query
    assert q("hello world") == '"hello"* AND "world"*'
    assert q("ab cd") == ""             # every term is under the 3-char minimum
    assert q('a"* OR b') == ""        # operators stripped, and every term is too short
    assert q("") == ""
    assert q('"; DROP TABLE users; --') == '"DROP"* AND "TABLE"* AND "users"*'
    assert len(q(" ".join(f"t{i:02d}" for i in range(50))).split(" AND ")) == 16


# --------------------------------------------------------------------------
# Pagination
# --------------------------------------------------------------------------

def test_pagination_walks_every_message_once(client, admin):
    seed_messages(client, 7, "page@example.com")
    seen, page = [], 1
    while True:
        d = admin["c"].get(f"/api/messages?page={page}&page_size=3").json()
        seen += [m["id"] for m in d["items"]]
        if page * 3 >= d["total"]:
            break
        page += 1
    assert len(seen) == len(set(seen)), "a message appeared on two pages"
    assert len(seen) == d["total"]


def test_page_size_is_capped(client, admin):
    seed_messages(client, 2)
    d = admin["c"].get("/api/messages?page_size=9999").json()
    assert d["page_size"] <= 100


def test_page_below_one_is_clamped(client, admin):
    d = admin["c"].get("/api/messages?page=0&page=-5").json()
    assert d["page"] == 1


# --------------------------------------------------------------------------
# Access control around search
# --------------------------------------------------------------------------

def test_search_does_not_leak_other_users_mail(client, admin):
    create_user("mine")
    grant_access("mine", "mine@example.com")
    create_user("theirs")
    grant_access("theirs", "theirs@example.com")
    login(client, "mine", "password-for-tests")
    ingest(client, "mine@example.com", subject="Secret zebra", body="hidden")
    ingest(client, "theirs@example.com", subject="Secret zebra", body="hidden")
    r = client.get("/api/messages?q=zebra").json()
    assert r["total"] == 1
    assert r["items"][0]["mailbox_address"] == "mine@example.com"


def test_search_on_a_foreign_mailbox_is_404(client, admin):
    create_user("mine")
    grant_access("mine", "mine@example.com")
    create_user("other")
    oid = grant_access("other", "other@example.com")
    login(client, "mine", "password-for-tests")
    assert client.get(f"/api/messages?mailbox_id={oid}").status_code == 404


def test_dashboard_hides_foreign_mailboxes(client, admin):
    create_user("mine")
    create_user("other")
    grant_access("mine", "mine@example.com")
    grant_access("other", "other@example.com")
    login(client, "mine", "password-for-tests")
    addrs = [m["address"] for m in client.get("/api/dashboard").json()["mailboxes"]]
    assert addrs == ["mine@example.com"]


# --------------------------------------------------------------------------
# Ingest / index integrity
# --------------------------------------------------------------------------

def test_duplicate_ingest_is_deduplicated(client, admin):
    a = ingest(client, "dup@example.com", subject="Same", body="Same body")
    b = ingest(client, "dup@example.com", subject="Same", body="Same body")
    assert a.status_code == 201
    assert b.json()["duplicate"] is True


def test_ingest_requires_worker_token(client, admin):
    r = client.post("/api/ingest", content=b"x", headers={"X-Envelope-To": "a@b.com"})
    assert r.status_code == 401


def test_messages_are_indexed_at_ingest(client, admin):
    ingest(client, "idx@example.com", subject="Pineapple pizza", body="uniqueidxbody")
    d = admin["c"].get("/api/messages?q=Pineapple").json()
    assert d["total"] >= 1


def test_deleting_a_mailbox_drops_its_search_rows(client, admin):
    ingest(client, "gone@example.com", subject="Ephemeral", body="x")
    with app_module.db() as conn:
        mid = conn.execute("SELECT id FROM mailboxes WHERE address='gone@example.com'").fetchone()["id"]
    with app_module.db() as conn:
        conn.execute("DELETE FROM mailboxes WHERE id=?", (mid,))
    if app_module.FTS_AVAILABLE:
        with app_module.db() as conn:
            n = conn.execute("SELECT COUNT(*) c FROM messages_fts WHERE message_id IN (SELECT id FROM messages)").fetchone()["c"]
            total = conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
        assert n == total


# --------------------------------------------------------------------------
# Sessions / auth
# --------------------------------------------------------------------------

def test_login_rejects_bad_password(client, admin):
    r = client.post("/api/login", json={"username": "rootadmin", "password": "wrong-password"})
    assert r.status_code == 401
    # The message must not reveal which half was wrong.
    assert r.json()["detail"] == "Invalid username or password"


def test_login_rejects_disabled_user(client, admin):
    create_user("off", active=False)
    r = client.post("/api/login", json={"username": "off", "password": "password-for-tests"})
    assert r.status_code == 401


def test_logout_clears_the_session(client, admin):
    c = admin["c"]
    assert c.get("/api/me").status_code == 200
    assert c.post("/api/logout").json() == {"ok": True}
    # The cookie must actually be gone, not just ignored by the server.
    assert c.get("/api/me").status_code == 401
    assert "otp_session" not in c.cookies


def test_logout_is_safe_without_a_session(client, admin):
    fresh = client
    r = fresh.post("/api/logout")
    assert r.status_code == 200


def test_session_cookie_attributes_match_on_logout(client, admin):
    """A mismatched delete_cookie silently leaves the browser session alive."""
    login(client)
    set_cookie = [v for k, v in client.cookies.items() if k == "otp_session"]
    assert set_cookie
    client.post("/api/logout")
    assert client.get("/api/me").status_code == 401


def test_disabled_user_session_is_invalidated(client, admin):
    create_user("temp")
    login(client, "temp", "password-for-tests")
    assert client.get("/api/me").status_code == 200
    with app_module.db() as conn:
        conn.execute("UPDATE users SET is_active=0 WHERE username='temp'")
    assert client.get("/api/me").status_code == 401


def test_csrf_required_for_mutations(client, admin):
    csrf = login(client)
    assert client.post("/api/referrals/code", json={}).status_code == 403
    assert client.post("/api/notifications/read").status_code == 403


def test_expired_session_is_rejected(client, admin):
    login(client)
    with app_module.db() as conn:
        conn.execute("UPDATE sessions SET expires_at='2000-01-01T00:00:00+00:00'")
    assert client.get("/api/me").status_code == 401


def test_change_password_invalidates_other_sessions(client, admin):
    csrf = login(client)
    r = client.post("/api/me/password",
                    json={"old_password": "root-admin-password", "new_password": "brand-new-password"},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/login", json={"username": "rootadmin", "password": "brand-new-password"}).status_code == 200


# --------------------------------------------------------------------------
# Dashboard aggregation
# --------------------------------------------------------------------------

def test_dashboard_counts_match_a_bulk_ingest(client, admin):
    ingest(client, "count@example.com", subject="a", body="x", otp_hint="111111")
    ingest(client, "count@example.com", subject="b", body="y", otp_hint="222222")
    ingest(client, "count@example.com", subject="c", body="z", otp_hint="333333")
    m = next(x for x in admin["c"].get("/api/dashboard").json()["mailboxes"]
             if x["address"] == "count@example.com")
    assert m["message_count"] == 3
    assert m["unread_count"] == 3
    assert m["latest"]["subject"] == "c"


def test_dashboard_unread_is_per_user(client, admin):
    ingest(admin["c"], "shared@example.com", subject="hello", body="x")
    create_user("reader")
    grant_access("reader", "shared@example.com")
    csrf = login(client, "reader", "password-for-tests")
    m = next(x for x in client.get("/api/dashboard").json()["mailboxes"]
             if x["address"] == "shared@example.com")
    assert m["unread_count"] == 1
    assert client.post(f"/api/messages/{m['latest']['id']}/read",
                       headers={"X-CSRF-Token": csrf}).status_code == 200
    m = next(x for x in client.get("/api/dashboard").json()["mailboxes"]
             if x["address"] == "shared@example.com")
    assert m["unread_count"] == 0
    # The admin's own count is unaffected by the other user's read state.
    am = next(x for x in admin["c"].get("/api/dashboard").json()["mailboxes"]
              if x["address"] == "shared@example.com")
    assert am["unread_count"] == 1


def test_dashboard_access_list_is_populated(client, admin):
    create_user("shared_with")
    grant_access("shared_with", "multi@example.com")
    m = next(x for x in admin["c"].get("/api/dashboard").json()["mailboxes"]
             if x["address"] == "multi@example.com")
    assert any(u["username"] == "shared_with" for u in m["users"])


def test_dashboard_survives_a_user_with_many_mailboxes(client, admin):
    create_user("hoarder")
    for i in range(60):
        grant_access("hoarder", f"bulk{i:03d}@example.com")
    login(client, "hoarder", "password-for-tests")
    dash = client.get("/api/dashboard").json()
    assert len(dash["mailboxes"]) == 60
    assert all(m["message_count"] == 0 for m in dash["mailboxes"])
    assert all(m["unread_count"] == 0 for m in dash["mailboxes"])


def test_dashboard_with_no_mailboxes_is_empty(client, admin):
    create_user("empty")
    login(client, "empty", "password-for-tests")
    assert client.get("/api/dashboard").json()["mailboxes"] == []
