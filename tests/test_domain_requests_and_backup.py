"""Tests for Domain policies, Mailbox requests & approval, and System Backup/Maintenance."""

import pytest
from fastapi.testclient import TestClient
from app import main as app_module
from conftest import create_user


def make_user_session(username: str = "reguser"):
    u = create_user(username)
    c = TestClient(app_module.app)
    r = c.post("/api/login", json={"username": username, "password": "password-for-tests"})
    assert r.status_code == 200
    return {"c": c, "csrf": r.json()["csrf"], "user": u}


def test_domains_list_and_admin_crud(admin: dict):
    c: TestClient = admin["c"]
    csrf: str = admin["csrf"]

    # Initial list contains seeded domain
    r = c.get("/api/domains")
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert any(d["name"] == "example.com" for d in data["items"])

    # Create new domain
    r = c.post(
        "/api/admin/domains",
        json={"name": "vip-domain.com", "access_level": "vip", "description": "VIP accounts only"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    dom_id = r.json()["id"]

    # Verify added
    r = c.get("/api/domains")
    assert any(d["name"] == "vip-domain.com" and d["access_level"] == "vip" for d in r.json()["items"])

    # Update domain
    r = c.patch(
        f"/api/admin/domains/{dom_id}",
        json={"access_level": "approval_only", "description": "Updated description"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200

    # Delete domain
    r = c.delete(f"/api/admin/domains/{dom_id}", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200

    # Verify deleted
    r = c.get("/api/domains")
    assert not any(d["id"] == dom_id for d in r.json()["items"])


def test_mailbox_request_flow_and_approval(admin: dict):
    ac: TestClient = admin["c"]
    a_csrf: str = admin["csrf"]
    user_sess = make_user_session("user_req_test")
    uc: TestClient = user_sess["c"]
    u_csrf: str = user_sess["csrf"]

    # Create approval_only domain as admin
    r = ac.post(
        "/api/admin/domains",
        json={"name": "restricted.com", "access_level": "approval_only", "description": "Needs review"},
        headers={"X-CSRF-Token": a_csrf},
    )
    assert r.status_code == 200

    # Regular user attempts direct creation on approval_only domain -> 403
    r = uc.post(
        "/api/mailboxes/create",
        json={"address": "myname@restricted.com"},
        headers={"X-CSRF-Token": u_csrf},
    )
    assert r.status_code == 403
    assert "Approval required" in r.json()["detail"]

    # Regular user submits a mailbox request
    r = uc.post(
        "/api/mailbox-requests",
        json={"desired_address": "myname@restricted.com", "reason": "Testing project integration"},
        headers={"X-CSRF-Token": u_csrf},
    )
    assert r.status_code == 200
    req_id = r.json()["id"]

    # Admin checks requests
    r = ac.get("/api/mailbox-requests?status=pending")
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(x["id"] == req_id and x["status"] == "pending" for x in items)

    # Admin approves request
    r = ac.post(f"/api/admin/mailbox-requests/{req_id}/approve", headers={"X-CSRF-Token": a_csrf})
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # User now has access to the mailbox
    r = uc.get("/api/dashboard")
    assert r.status_code == 200
    user_boxes = r.json()["mailboxes"]
    assert any(m["address"] == "myname@restricted.com" for m in user_boxes)


def test_mailbox_request_rejection(admin: dict):
    ac: TestClient = admin["c"]
    a_csrf: str = admin["csrf"]
    user_sess = make_user_session("user_rej_test")
    uc: TestClient = user_sess["c"]
    u_csrf: str = user_sess["csrf"]

    # Submit request
    r = uc.post(
        "/api/mailbox-requests",
        json={"desired_address": "reject-me@example.com", "reason": "No reason"},
        headers={"X-CSRF-Token": u_csrf},
    )
    assert r.status_code == 200
    req_id = r.json()["id"]

    # Admin rejects
    r = ac.post(
        f"/api/admin/mailbox-requests/{req_id}/reject",
        json={"admin_note": "Domain reserved"},
        headers={"X-CSRF-Token": a_csrf},
    )
    assert r.status_code == 200

    # User checks their requests
    r = uc.get("/api/mailbox-requests")
    assert r.status_code == 200
    req = next(x for x in r.json()["items"] if x["id"] == req_id)
    assert req["status"] == "rejected"
    assert "Domain reserved" in req["admin_note"]


def test_admin_system_stats_and_vacuum(admin: dict):
    c: TestClient = admin["c"]
    csrf: str = admin["csrf"]

    # System stats
    r = c.get("/api/admin/system/stats")
    assert r.status_code == 200
    d = r.json()
    assert "disk_limit_mb" in d
    assert d["disk_limit_mb"] == 0
    assert "db_size_bytes" in d
    assert "total_messages" in d
    assert "total_domains" in d

    # VACUUM
    r = c.post("/api/admin/system/vacuum", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_backups_and_csv_exports(admin: dict):
    c: TestClient = admin["c"]

    # SQLite backup download
    r = c.get("/api/admin/backup/sqlite")
    if not app_module.USING_POSTGRES:
        assert r.status_code == 200
        assert len(r.content) > 0

    # JSON config backup
    r = c.get("/api/admin/backup/json")
    assert r.status_code == 200
    assert "application/json" in r.headers["content-type"]
    data = r.json()
    assert "domains" in data
    assert "mailboxes" in data
    assert "users" in data

    # CSV exports
    r = c.get("/api/export/mailboxes.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]

    r = c.get("/api/export/otps.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
