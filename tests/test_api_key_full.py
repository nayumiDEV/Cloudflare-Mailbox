"""API key works for full read+write surface (not only /api/v1)."""

from conftest import create_user, grant_access, ingest


def auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def test_api_key_reads_me_and_dashboard(client):
    user = create_user("fullreader")
    grant_access("fullreader", "r@example.com", owner=True)
    ingest(client, "r@example.com", subject="Hi", body="code", otp_hint="101010")
    me = client.get("/api/me", headers=auth(user["api_key"]))
    assert me.status_code == 200, me.text
    assert me.json()["auth_via"] == "api_key"
    assert me.json()["csrf"] is None
    assert me.json()["user"]["username"] == "fullreader"
    dash = client.get("/api/dashboard", headers=auth(user["api_key"]))
    assert dash.status_code == 200
    assert any(m["address"] == "r@example.com" for m in dash.json()["mailboxes"])


def test_api_key_write_create_mailbox_without_csrf(client):
    user = create_user("writer")
    r = client.post(
        "/api/mailboxes/create",
        headers={**auth(user["api_key"]), "Content-Type": "application/json"},
        json={"address": "newbox@example.com"},
    )
    assert r.status_code == 200, r.text
    boxes = client.get("/api/v1/mailboxes", headers=auth(user["api_key"])).json()["items"]
    assert any(b["address"] == "newbox@example.com" for b in boxes)


def test_api_key_mark_read_without_csrf(client):
    user = create_user("reader2")
    grant_access("reader2", "m@example.com", owner=True)
    ingest(client, "m@example.com", subject="S", body="code", otp_hint="202020")
    mid = client.get(
        "/api/messages", headers=auth(user["api_key"]),
    ).json()["items"][0]["id"]
    r = client.post(f"/api/messages/{mid}/read", headers=auth(user["api_key"]))
    assert r.status_code == 200, r.text
    detail = client.get(f"/api/messages/{mid}", headers=auth(user["api_key"])).json()
    assert detail["is_read"] is True


def test_session_still_needs_csrf_for_writes(client):
    create_user("csrfuser")
    login = client.post("/api/login", json={"username": "csrfuser", "password": "password-for-tests"})
    assert login.status_code == 200
    # Cookie is set; omit CSRF → 403
    r = client.post("/api/mailboxes/create", json={"address": "needs-csrf@example.com"})
    assert r.status_code == 403
    csrf = login.json()["csrf"]
    ok = client.post(
        "/api/mailboxes/create",
        json={"address": "needs-csrf@example.com"},
        headers={"X-CSRF-Token": csrf},
    )
    assert ok.status_code == 200, ok.text


def test_api_key_admin_assign(client, admin):
    # Get admin API key via regenerate (session + csrf)
    key = admin["c"].post(
        "/api/me/regenerate-key", headers={"X-CSRF-Token": admin["csrf"]},
    ).json()["api_key"]
    target = create_user("assignee")
    # Create mailbox as admin via API key
    created = client.post(
        "/api/mailboxes/create",
        headers={**auth(key), "Content-Type": "application/json"},
        json={"address": "assign-me@example.com", "owner_public_id": target["public_id"]},
    )
    assert created.status_code == 200, created.text
    # Assignee sees it with their key
    boxes = client.get("/api/v1/mailboxes", headers=auth(target["api_key"])).json()["items"]
    assert any(b["address"] == "assign-me@example.com" for b in boxes)


def test_bearer_preferred_over_session(client):
    a = create_user("alpha")
    b = create_user("beta")
    grant_access("alpha", "a@example.com", owner=True)
    grant_access("beta", "b@example.com", owner=True)
    # Log in as alpha (session), but call with beta's API key
    client.post("/api/login", json={"username": "alpha", "password": "password-for-tests"})
    me = client.get("/api/me", headers=auth(b["api_key"])).json()
    assert me["user"]["username"] == "beta"
    addrs = [m["address"] for m in client.get("/api/dashboard", headers=auth(b["api_key"])).json()["mailboxes"]]
    assert addrs == ["b@example.com"]
