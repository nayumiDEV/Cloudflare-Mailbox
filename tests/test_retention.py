"""Tests for auto-delete / data retention and optimization endpoints."""

from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient

from app import main as app_module


def test_admin_retention_get_requires_admin(client: TestClient):
    # Anonymous request
    r = client.get("/api/admin/retention/settings")
    assert r.status_code == 401


def test_admin_retention_get(admin: dict):
    c: TestClient = admin["c"]
    r = c.get("/api/admin/retention/settings")
    assert r.status_code == 200
    data = r.json()
    assert "auto_delete_enabled" in data
    assert "auto_delete_days" in data
    assert data["auto_delete_days"] == 7
    assert "total_messages" in data
    assert "old_messages" in data
    assert "db_size_bytes" in data


def test_admin_retention_patch(admin: dict):
    c: TestClient = admin["c"]
    csrf: str = admin["csrf"]

    # Without CSRF header -> 403
    r = c.patch("/api/admin/retention/settings", json={"auto_delete_enabled": True, "auto_delete_days": 14})
    assert r.status_code == 403

    # With CSRF
    r = c.patch(
        "/api/admin/retention/settings",
        json={"auto_delete_enabled": True, "auto_delete_days": 14},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["auto_delete_enabled"] is True
    assert data["auto_delete_days"] == 14

    # Verify settings persisted
    r = c.get("/api/admin/retention/settings")
    assert r.status_code == 200
    assert r.json()["auto_delete_enabled"] is True
    assert r.json()["auto_delete_days"] == 14


def test_purge_old_messages_cascades(admin: dict):
    c: TestClient = admin["c"]
    csrf: str = admin["csrf"]

    with app_module.db() as conn:
        # Create a mailbox
        mb = app_module.get_or_create_mailbox(conn, "test-retention@example.com")
        mb_id = mb["id"]

        now = datetime.now(timezone.utc)
        old_time = (now - timedelta(days=10)).isoformat()
        recent_time = (now - timedelta(days=2)).isoformat()

        # Insert old message (10 days old)
        old_msg_id = conn.execute(
            """INSERT INTO messages(mailbox_id, envelope_from, envelope_to, subject, body_text, body_html,
                                    raw_mime, received_at)
               VALUES(?, 'old@sender.com', 'test-retention@example.com', 'Old Subject', 'Old text', NULL,
                      ?, ?)""",
            (mb_id, b"raw old mime", old_time),
        ).lastrowid

        # Insert attachment for old message
        conn.execute(
            """INSERT INTO attachments(message_id, filename, content_type, size_bytes, data)
               VALUES(?, 'old_file.txt', 'text/plain', 12, ?)""",
            (old_msg_id, b"old content!"),
        )

        # Insert recent message (2 days old)
        recent_msg_id = conn.execute(
            """INSERT INTO messages(mailbox_id, envelope_from, envelope_to, subject, body_text, body_html,
                                    raw_mime, received_at)
               VALUES(?, 'recent@sender.com', 'test-retention@example.com', 'Recent Subject', 'Recent text', NULL,
                      ?, ?)""",
            (mb_id, b"raw recent mime", recent_time),
        ).lastrowid

        # Insert attachment for recent message
        conn.execute(
            """INSERT INTO attachments(message_id, filename, content_type, size_bytes, data)
               VALUES(?, 'recent_file.txt', 'text/plain', 15, ?)""",
            (recent_msg_id, b"recent content!"),
        )

        if app_module.FTS_AVAILABLE:
            conn.execute(
                "INSERT INTO messages_fts(rowid, envelope_from, envelope_to, subject, body, message_id) VALUES(?, 'old@sender.com', 'test-retention@example.com', 'Old Subject', 'Old text', ?)",
                (old_msg_id, str(old_msg_id)),
            )
            conn.execute(
                "INSERT INTO messages_fts(rowid, envelope_from, envelope_to, subject, body, message_id) VALUES(?, 'recent@sender.com', 'test-retention@example.com', 'Recent Subject', 'Recent text', ?)",
                (recent_msg_id, str(recent_msg_id)),
            )

    # Check stats endpoint shows 1 old message when retention is 7 days
    r = c.get("/api/admin/retention/settings")
    assert r.status_code == 200
    stats = r.json()
    assert stats["total_messages"] == 2
    assert stats["old_messages"] == 1

    # Trigger cleanup for messages older than 7 days
    r = c.post(
        "/api/admin/retention/cleanup",
        json={"days": 7},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    res = r.json()
    assert res["ok"] is True
    assert res["deleted_count"] == 1

    # Verify old message and its attachments are gone, but recent remains
    with app_module.db() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM messages WHERE id=?", (old_msg_id,)).fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) c FROM attachments WHERE message_id=?", (old_msg_id,)).fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) c FROM messages WHERE id=?", (recent_msg_id,)).fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) c FROM attachments WHERE message_id=?", (recent_msg_id,)).fetchone()["c"] == 1


def test_message_raw_bytes_synthesis():
    """Verify synthetic RFC822 EML generation when raw_mime is None."""
    row = {
        "sender": "sender@domain.com",
        "recipient": "rcpt@domain.com",
        "subject": "Hello Test",
        "received_at": "2026-10-09T10:00:00Z",
        "body_text": "This is plain text body.",
        "body_html": "<p>This is plain text body.</p>",
        "raw_mime": None,
    }
    raw = app_module.message_raw_bytes(row)
    assert isinstance(raw, bytes)
    assert b"From: sender@domain.com" in raw
    assert b"To: rcpt@domain.com" in raw
    assert b"Subject: Hello Test" in raw
    assert b"This is plain text body." in raw
