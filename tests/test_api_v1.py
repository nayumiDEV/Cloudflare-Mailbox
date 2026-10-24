"""Developer API (Bearer API key) coverage."""

from conftest import create_user, grant_access, ingest


def auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def test_v1_requires_api_key(client):
    assert client.get("/api/v1/inbox").status_code == 401
    assert client.get("/api/v1/inbox", headers=auth("sk-otp-not-a-real-key")).status_code == 401


def test_v1_inbox_filters_address_and_otp(client):
    user = create_user("apiuser")
    grant_access("apiuser", "otp@example.com", owner=True)
    grant_access("apiuser", "other@example.com", owner=True)
    ingest(client, "otp@example.com", subject="Login", body="Use code", otp_hint="112233")
    raw = (
        "From: s@example.com\r\nTo: other@example.com\r\nSubject: Hello\r\n"
        "Date: Mon, 01 Jan 2024 10:00:00 +0000\r\nMessage-ID: <nootp@test>\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\nJust a hello with no code.\r\n"
    ).encode()
    assert client.post(
        "/api/ingest", content=raw,
        headers={
            "X-Worker-Token": "test-worker-token",
            "X-Envelope-From": "s@example.com",
            "X-Envelope-To": "other@example.com",
            "X-Email-Subject": "Hello",
        },
    ).status_code == 201
    r = client.get(
        "/api/v1/inbox",
        params={"address": "otp@example.com", "otp_only": 1, "limit": 20},
        headers=auth(user["api_key"]),
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert items
    assert all(i["address"] == "otp@example.com" for i in items)
    assert all(i.get("otp") for i in items)
    assert "body_text" not in items[0]
    assert "preview" in items[0]
    # otp_only must exclude the no-code mailbox even though the user can see it
    all_otp = client.get(
        "/api/v1/inbox", params={"otp_only": 1}, headers=auth(user["api_key"]),
    ).json()["items"]
    assert all(i["otp"] for i in all_otp)
    assert not any(i["address"] == "other@example.com" for i in all_otp)


def test_v1_include_body(client):
    user = create_user("bodyuser")
    grant_access("bodyuser", "b@example.com", owner=True)
    ingest(client, "b@example.com", subject="S", body="SecretBodyMarker", otp_hint="445566")
    r = client.get(
        "/api/v1/inbox",
        params={"address": "b@example.com", "include_body": 1, "limit": 5},
        headers=auth(user["api_key"]),
    )
    assert r.status_code == 200
    assert "SecretBodyMarker" in (r.json()["items"][0].get("body_text") or "")


def test_v1_access_isolation(client):
    a = create_user("aliceapi")
    b = create_user("bobapi")
    grant_access("aliceapi", "alice@example.com", owner=True)
    grant_access("bobapi", "bob@example.com", owner=True)
    ingest(client, "alice@example.com", subject="A", body="code", otp_hint="111111")
    ingest(client, "bob@example.com", subject="B", body="code", otp_hint="222222")
    alice_items = client.get("/api/v1/inbox", headers=auth(a["api_key"])).json()["items"]
    bob_items = client.get("/api/v1/inbox", headers=auth(b["api_key"])).json()["items"]
    assert {i["address"] for i in alice_items} == {"alice@example.com"}
    assert {i["address"] for i in bob_items} == {"bob@example.com"}


def test_v1_mailboxes_and_message_detail(client):
    user = create_user("detailuser")
    grant_access("detailuser", "d@example.com", owner=True)
    ingest(client, "d@example.com", subject="Detail", body="verify", otp_hint="778899")
    boxes = client.get("/api/v1/mailboxes", headers=auth(user["api_key"])).json()["items"]
    assert any(b["address"] == "d@example.com" for b in boxes)
    inbox = client.get(
        "/api/v1/inbox",
        params={"address": "d@example.com", "otp_only": 1},
        headers=auth(user["api_key"]),
    ).json()["items"]
    mid = inbox[0]["id"]
    detail = client.get(f"/api/v1/messages/{mid}", headers=auth(user["api_key"])).json()
    assert detail["otp"] == "778899"
    assert detail["subject"] == "Detail"
    assert "body_text" in detail
    raw = client.get(f"/api/v1/messages/{mid}/raw", headers=auth(user["api_key"]))
    assert raw.status_code == 200
    assert raw.headers["content-type"].startswith("message/rfc822")


def test_v1_forbidden_message_is_404(client):
    a = create_user("ownera")
    b = create_user("outsider")
    grant_access("ownera", "private@example.com", owner=True)
    ingest(client, "private@example.com", subject="P", body="code", otp_hint="333444")
    mid = client.get("/api/v1/inbox", headers=auth(a["api_key"])).json()["items"][0]["id"]
    assert client.get(f"/api/v1/messages/{mid}", headers=auth(b["api_key"])).status_code == 404


def test_hyphenated_otp_survives_ingest(client):
    user = create_user("hyphenuser")
    grant_access("hyphenuser", "h@example.com", owner=True)
    raw = (
        "From: s@example.com\r\nTo: h@example.com\r\nSubject: Code\r\n"
        "Date: Mon, 01 Jan 2024 10:00:00 +0000\r\n"
        "Message-ID: <hyphen@test>\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        "Your verification code is 123-456.\r\n"
    ).encode()
    r = client.post(
        "/api/ingest",
        content=raw,
        headers={
            "X-Worker-Token": "test-worker-token",
            "X-Envelope-From": "s@example.com",
            "X-Envelope-To": "h@example.com",
            "X-Email-Subject": "Code",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["otp"] == "123456"
    items = client.get(
        "/api/v1/inbox",
        params={"address": "h@example.com", "otp_only": 1},
        headers=auth(user["api_key"]),
    ).json()["items"]
    assert items[0]["otp"] == "123456"


def test_css_color_not_stored_as_otp(client):
    user = create_user("cssuser")
    grant_access("cssuser", "c@example.com", owner=True)
    html = (
        "<html><head><style>body{color:#333333}</style></head>"
        "<body><p>Hello friend</p></body></html>"
    )
    raw = (
        "From: s@example.com\r\nTo: c@example.com\r\nSubject: News\r\n"
        "Date: Mon, 01 Jan 2024 10:00:00 +0000\r\n"
        "Message-ID: <css@test>\r\n"
        "Content-Type: text/html; charset=utf-8\r\n\r\n"
        f"{html}\r\n"
    ).encode()
    r = client.post(
        "/api/ingest",
        content=raw,
        headers={
            "X-Worker-Token": "test-worker-token",
            "X-Envelope-From": "s@example.com",
            "X-Envelope-To": "c@example.com",
            "X-Email-Subject": "News",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json().get("otp") in (None, "")
    items = client.get(
        "/api/v1/inbox",
        params={"address": "c@example.com"},
        headers=auth(user["api_key"]),
    ).json()["items"]
    assert items
    assert not items[0]["otp"]
