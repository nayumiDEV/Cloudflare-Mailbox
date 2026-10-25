"""Outbound send API (Cloudflare Email Sending via Worker /send)."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from conftest import create_user, grant_access
from app import main as app_module


def _enable_send(monkeypatch):
    monkeypatch.setattr(app_module, "SEND_ENABLED", True)
    monkeypatch.setattr(app_module, "WORKER_SEND_URL", "https://worker.example/send")


def _csrf(admin):
    return {"X-CSRF-Token": admin["csrf"]}


def test_send_disabled_returns_503(admin):
    mb_id = grant_access("rootadmin", "from@example.com", owner=True)
    r = admin["c"].post(
        f"/api/mailboxes/{mb_id}/send",
        json={"to": "a@example.com", "subject": "Hi", "text": "Body"},
        headers=_csrf(admin),
    )
    assert r.status_code == 503
    assert "WORKER_SEND_URL" in r.json()["detail"]


def test_send_success_records_outbound(admin, monkeypatch):
    _enable_send(monkeypatch)
    mb_id = grant_access("rootadmin", "from@example.com", owner=True)

    def fake_send(payload):
        assert payload["from"] == "from@example.com"
        assert payload["to"] == ["to@example.com"]
        assert payload["subject"] == "Hello"
        assert "Body text" in payload["text"]
        return {"ok": True, "messageId": "cf-msg-123"}

    with patch.object(app_module, "call_worker_send", side_effect=fake_send):
        r = admin["c"].post(
            f"/api/mailboxes/{mb_id}/send",
            json={"to": "to@example.com", "subject": "Hello", "text": "Body text"},
            headers=_csrf(admin),
        )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["status"] == "sent"
    assert data["cf_message_id"] == "cf-msg-123"
    assert data["mailbox"] == "from@example.com"

    listed = admin["c"].get("/api/sent").json()
    assert listed["total"] >= 1
    assert any(i["subject"] == "Hello" for i in listed["items"])

    detail = admin["c"].get(f"/api/sent/{data['id']}").json()
    assert detail["body_text"] == "Body text"
    assert detail["to_addrs"] == ["to@example.com"]


def test_send_access_control_and_forced_from(monkeypatch):
    _enable_send(monkeypatch)
    create_user("otheruser")
    mb_id = grant_access("otheruser", "other@example.com", owner=True)
    create_user("stranger")

    sc = TestClient(app_module.app)
    slogin = sc.post("/api/login", json={"username": "stranger", "password": "password-for-tests"})
    assert slogin.status_code == 200
    denied = sc.post(
        f"/api/mailboxes/{mb_id}/send",
        json={"to": "x@example.com", "subject": "Nope", "text": "x", "from": "forged@evil.com"},
        headers={"X-CSRF-Token": slogin.json()["csrf"]},
    )
    assert denied.status_code == 403
    sc.close()

    oc = TestClient(app_module.app)
    ologin = oc.post("/api/login", json={"username": "otheruser", "password": "password-for-tests"})
    assert ologin.status_code == 200
    with patch.object(app_module, "call_worker_send", return_value={"messageId": "m1"}):
        ok = oc.post(
            f"/api/mailboxes/{mb_id}/send",
            json={"to": "x@example.com", "subject": "Ok", "text": "hi", "from": "forged@evil.com"},
            headers={"X-CSRF-Token": ologin.json()["csrf"]},
        )
    assert ok.status_code == 200
    assert ok.json()["mailbox"] == "other@example.com"
    oc.close()


def test_send_validation(admin, monkeypatch):
    _enable_send(monkeypatch)
    mb_id = grant_access("rootadmin", "from@example.com", owner=True)
    h = _csrf(admin)

    assert admin["c"].post(
        f"/api/mailboxes/{mb_id}/send",
        json={"to": "", "subject": "S", "text": "t"},
        headers=h,
    ).status_code == 400

    assert admin["c"].post(
        f"/api/mailboxes/{mb_id}/send",
        json={"to": "a@example.com", "subject": "", "text": "t"},
        headers=h,
    ).status_code == 400

    assert admin["c"].post(
        f"/api/mailboxes/{mb_id}/send",
        json={"to": "a@example.com", "subject": "S", "text": ""},
        headers=h,
    ).status_code == 400


def test_send_failure_still_recorded(admin, monkeypatch):
    _enable_send(monkeypatch)
    mb_id = grant_access("rootadmin", "from@example.com", owner=True)

    def boom(_payload):
        raise app_module.HTTPException(status_code=400, detail="E_SENDER_NOT_VERIFIED")

    with patch.object(app_module, "call_worker_send", side_effect=boom):
        r = admin["c"].post(
            f"/api/mailboxes/{mb_id}/send",
            json={"to": "a@example.com", "subject": "Fail", "text": "x"},
            headers=_csrf(admin),
        )
    assert r.status_code == 400

    items = admin["c"].get("/api/sent").json()["items"]
    failed = [i for i in items if i["subject"] == "Fail"]
    assert failed
    assert failed[0]["status"] == "failed"


def test_health_and_me_expose_send_flag(client, admin, monkeypatch):
    h = client.get("/health").json()
    assert "send_enabled" in h
    assert h["send_enabled"] is False

    _enable_send(monkeypatch)
    assert client.get("/health").json()["send_enabled"] is True
    me = admin["c"].get("/api/me").json()
    assert me["send_enabled"] is True
    cfg = client.get("/api/auth/config").json()
    assert cfg["send_enabled"] is True


def test_send_via_api_key(client, monkeypatch):
    _enable_send(monkeypatch)
    user = create_user("apisender")
    mb_id = grant_access("apisender", "api@example.com", owner=True)

    with patch.object(app_module, "call_worker_send", return_value={"messageId": "k1"}):
        r = client.post(
            f"/api/mailboxes/{mb_id}/send",
            json={"to": "z@example.com", "subject": "Key", "text": "via key"},
            headers={"Authorization": f"Bearer {user['api_key']}"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["cf_message_id"] == "k1"
