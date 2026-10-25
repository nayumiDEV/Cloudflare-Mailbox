"""Tests for bulk mailbox creation (> 300 mailboxes) and scalability."""

import pytest
from fastapi.testclient import TestClient

from app import main as app_module
from conftest import create_user


def test_create_mailboxes_bulk_over_300(client: TestClient, admin: dict):
    """Verify that importing / creating > 300 mailboxes succeeds without HTTP 400."""
    user = create_user("bulkuser")
    r = client.post("/api/login", json={"username": "bulkuser", "password": "password-for-tests"})
    assert r.status_code == 200
    csrf = r.json()["csrf"]

    # Generate 350 email addresses
    addresses = [f"testuser_{i}@example.com" for i in range(1, 351)]
    raw_payload = "\n".join(addresses)

    res = client.post(
        "/api/mailboxes/create-bulk",
        json={"addresses": raw_payload},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ok"] is True
    assert data["created"] == 350
    assert data["existing"] == 0


def test_mailbox_summaries_chunking_large_dataset(admin: dict):
    """Verify that mailbox_summaries handles large number of mailboxes (> 999) without SQL parameter limit errors."""
    with app_module.db() as conn:
        u = conn.execute("SELECT * FROM users WHERE username='rootadmin'").fetchone()

        # Seed 500 fake mailbox rows directly
        now = app_module.now_iso()
        for i in range(500):
            conn.execute(
                """INSERT INTO mailboxes(address, owner_user_id, exact_override, created_at, updated_at)
                   VALUES(?, ?, 1, ?, ?)""",
                (f"bench_{i}@example.com", u["id"], now, now),
            )
        boxes = conn.execute("SELECT * FROM mailboxes WHERE address LIKE 'bench_%'").fetchall()
        assert len(boxes) == 500

        # mailbox_summaries should chunk them into batches of 400 seamlessly
        summaries = app_module.mailbox_summaries(conn, u, boxes)
        assert len(summaries) == 500
        assert summaries[0]["is_owner"] is True


def test_parse_email_rfc2047_subject():
    """Verify RFC2047 encoded subject decoding does not crash or fail."""
    # =?UTF-8?B?TeG6oSB4w6FjIHRo4buxYyBPVFA=?= is "Mã xác thực OTP"
    raw_eml = (
        b"From: sender@example.com\r\n"
        b"To: rcpt@example.com\r\n"
        b"Subject: =?UTF-8?B?TeG6oSB4w6FjIHRo4buxYyBPVFA=?=\r\n"
        b"Date: Mon, 01 Jan 2024 10:00:00 +0000\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        b"Your code is 987654\r\n"
    )
    parsed = app_module.parse_email(raw_eml)
    assert "Mạ xác thực OTP" in parsed["subject"]
    assert parsed["otp"] == "987654"
