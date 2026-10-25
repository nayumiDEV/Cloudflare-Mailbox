"""Seed script for OTP Inbox demo.
Populates SQLite database with users, mailboxes, routing rules, referral codes,
and realistic Gmail-style OTP messages with rich HTML and plain-text contents.
"""

from __future__ import annotations

import os
import sys
import sqlite3
import datetime
import secrets
import hashlib
import base64

from app import db, schema
from app import main as app


def run_seed():
    db_path = os.getenv("DATABASE_PATH", "./data/otp_inbox.db")
    os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)

    # Run standard app DB initialization
    app.init_db()

    raw_ctx = db.connect()
    conn = raw_ctx.__enter__()

    now = datetime.datetime.now(datetime.timezone.utc)
    now_iso = now.isoformat()

    def dt(hours_ago: float = 0) -> str:
        t = now - datetime.timedelta(hours=hours_ago)
        return t.isoformat()

    print("[Seed] Creating users...")
    
    # 1. Admin user: admin / admin12345
    admin_pass_hash = app.hash_secret("admin12345")
    admin_api_key = secrets.token_urlsafe(32)
    admin_api_hash = app.hash_secret(admin_api_key)
    
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE username = 'admin'")
    row = cur.fetchone()
    if row:
        admin_id = row[0]
        cur.execute(
            "UPDATE users SET password_hash = ?, is_active = 1, can_generate_codes = 1 WHERE id = ?",
            (admin_pass_hash, admin_id)
        )
    else:
        cur.execute(
            """INSERT INTO users (public_id, username, password_hash, role, api_key_hash, api_key_prefix, is_active, can_generate_codes, created_at, updated_at)
               VALUES (?, 'admin', ?, 'admin', ?, ?, 1, 1, ?, ?)""",
            ("U-ADMIN-01", admin_pass_hash, admin_api_hash, admin_api_key[:16], now_iso, now_iso)
        )
        admin_id = cur.lastrowid

    # 2. Regular user: user / user123456
    user_pass_hash = app.hash_secret("user123456")
    user_api_key = secrets.token_urlsafe(32)
    user_api_hash = app.hash_secret(user_api_key)
    
    cur.execute("SELECT id FROM users WHERE username = 'user'")
    row = cur.fetchone()
    if row:
        user_id = row[0]
        cur.execute(
            "UPDATE users SET password_hash = ?, is_active = 1, can_generate_codes = 1 WHERE id = ?",
            (user_pass_hash, user_id)
        )
    else:
        cur.execute(
            """INSERT INTO users (public_id, username, password_hash, role, api_key_hash, api_key_prefix, is_active, can_generate_codes, created_at, updated_at)
               VALUES (?, 'user', ?, 'user', ?, ?, 1, 1, ?, ?)""",
            ("U-USER-02", user_pass_hash, user_api_hash, user_api_key[:16], now_iso, now_iso)
        )
        user_id = cur.lastrowid

    print("[Seed] Creating referral codes...")
    cur.execute("DELETE FROM referral_codes")
    demo_ref_code = "REF-GMAIL-2026"
    cur.execute(
        """INSERT INTO referral_codes (code_hash, code_fingerprint, owner_user_id, created_by, max_uses, uses, is_active, note, created_at)
           VALUES (?, ?, ?, ?, 50, 0, 1, 'Mã giới thiệu dùng thử Gmail OTP', ?)""",
        (app.hash_secret(demo_ref_code), demo_ref_code[:8], admin_id, admin_id, now_iso)
    )

    print("[Seed] Creating mailboxes...")
    # Clear existing messages and mailboxes for clean demo experience
    cur.execute("DELETE FROM message_reads")
    cur.execute("DELETE FROM messages")
    cur.execute("DELETE FROM mailbox_access")
    cur.execute("DELETE FROM mailboxes")

    mailboxes_data = [
        ("auth@gmail-otp.dev", admin_id, 1, "Chuyên nhận OTP bảo mật và xác thực"),
        ("support@mybrand.io", admin_id, 0, "Email hỗ trợ khách hàng và thông báo"),
        ("dev-team@internal.app", user_id, 0, "Mailbox nội bộ nhóm phát triển"),
        ("billing@fintech.co", admin_id, 0, "Mailbox thanh toán và giao dịch tài chính"),
        ("security-alerts@domain.com", None, 0, "Catch-all email thông báo hệ thống"),
    ]

    mb_ids = {}
    for address, owner, exact, reason in mailboxes_data:
        cur.execute(
            """INSERT INTO mailboxes (address, owner_user_id, exact_override, override_reason, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (address, owner, exact, reason, now_iso, now_iso)
        )
        mb_id = cur.lastrowid
        mb_ids[address] = mb_id

        if owner:
            cur.execute(
                """INSERT OR REPLACE INTO mailbox_access (mailbox_id, user_id, source, granted_by, granted_at)
                   VALUES (?, ?, 'owner', ?, ?)""",
                (mb_id, owner, owner, now_iso)
            )

    # Share billing@fintech.co with 'user'
    cur.execute(
        """INSERT OR REPLACE INTO mailbox_access (mailbox_id, user_id, source, granted_by, granted_at)
           VALUES (?, ?, 'shared', ?, ?)""",
        (mb_ids["billing@fintech.co"], user_id, admin_id, now_iso)
    )

    print("[Seed] Creating routing rules...")
    cur.execute("DELETE FROM routing_rules")
    rules_data = [
        ("domain", "@fintech.co", admin_id, "Tất cả thư đuôi fintech.co chuyển về admin", 1),
        ("prefix", "dev-", user_id, "Tiền tố dev- gán quyền cho lập trình viên", 1),
        ("catch_all", "*", admin_id, "Catch-all toàn bộ địa chỉ chưa gán về admin", 1),
        ("regex", r"^billing\+.*@.*$", admin_id, "Lọc email hóa đơn phụ", 1),
    ]
    for rtype, pat, target, desc, is_act in rules_data:
        cur.execute(
            """INSERT INTO routing_rules (rule_type, pattern, target_user_id, description, is_active, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (rtype, pat, target, desc, is_act, admin_id, now_iso, now_iso)
        )

    print("[Seed] Creating realistic messages with OTP...")

    sample_messages = [
        {
            "mailbox": "auth@gmail-otp.dev",
            "from": "Google <no-reply@accounts.google.com>",
            "to": "auth@gmail-otp.dev",
            "subject": "849201 là mã xác minh Google của bạn",
            "otp": "849201",
            "time": dt(0.2), # 12 mins ago
            "is_read": 0,
            "text": "849201 là mã xác minh Google của bạn. Mã có hiệu lực trong 10 phút. Không chia sẻ mã này với bất kỳ ai để bảo vệ tài khoản.",
            "html": """
            <div style="font-family:'Google Sans',Roboto,Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px;background:#ffffff;border-radius:12px;border:1px solid #e0e2ec">
              <div style="text-align:center;margin-bottom:24px">
                <span style="font-size:26px;font-weight:700;color:#4285F4">G</span><span style="font-size:26px;font-weight:700;color:#EA4335">o</span><span style="font-size:26px;font-weight:700;color:#FBBC05">o</span><span style="font-size:26px;font-weight:700;color:#4285F4">g</span><span style="font-size:26px;font-weight:700;color:#34A853">l</span><span style="font-size:26px;font-weight:700;color:#EA4335">e</span>
              </div>
              <h2 style="font-size:20px;font-weight:500;color:#1f1f1f;margin:0 0 16px;text-align:center">Xác minh tài khoản Google</h2>
              <p style="font-size:14px;color:#444746;line-height:1.6;margin:0 0 20px">Bạn đang yêu cầu đăng nhập vào thiết bị mới. Vui lòng sử dụng mã xác minh bảo mật sau đây:</p>
              <div style="background:#f0f4f9;border-radius:12px;padding:20px;text-align:center;margin-bottom:20px">
                <span style="font-size:36px;font-weight:700;letter-spacing:6px;color:#0b57d0;font-family:monospace">849201</span>
              </div>
              <p style="font-size:13px;color:#444746;line-height:1.5">Mã này có hiệu lực trong vòng 10 phút. Nếu bạn không gửi yêu cầu này, vui lòng đổi mật khẩu ngay lập tức.</p>
              <hr style="border:none;border-top:1px solid #f0f4f9;margin:24px 0">
              <div style="font-size:11px;color:#8e918f;text-align:center">Google LLC, 1600 Amphitheatre Parkway, Mountain View, CA 94043, USA</div>
            </div>
            """
        },
        {
            "mailbox": "auth@gmail-otp.dev",
            "from": "GitHub <noreply@github.com>",
            "to": "auth@gmail-otp.dev",
            "subject": "[GitHub] Your two-factor authentication code is 493012",
            "otp": "493012",
            "time": dt(1.5),
            "is_read": 0,
            "text": "Your GitHub two-factor verification code is 493012. Please enter this code within 5 minutes to verify your login session.",
            "html": """
            <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;max-width:540px;margin:0 auto;padding:24px;background:#ffffff;border:1px solid #d0d7de;border-radius:10px">
              <div style="margin-bottom:20px">
                <svg height="32" viewBox="0 0 16 16" width="32" style="fill:#24292f"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"></path></svg>
              </div>
              <h3 style="font-size:18px;color:#24292f;margin:0 0 14px">Two-factor authentication code</h3>
              <p style="font-size:14px;color:#57606a;line-height:1.5;margin:0 0 16px">Here is your verification code to complete sign-in to <strong>GitHub</strong>:</p>
              <div style="background:#f6f8fa;border:1px solid #d0d7de;border-radius:8px;padding:16px;text-align:center;font-size:32px;font-weight:700;letter-spacing:5px;font-family:monospace;color:#0969da;margin-bottom:16px">493012</div>
              <p style="font-size:12px;color:#57606a">Security tip: GitHub staff will never ask you for this code.</p>
            </div>
            """
        },
        {
            "mailbox": "billing@fintech.co",
            "from": "Binance Security <do-not-reply@post.binance.com>",
            "to": "billing@fintech.co",
            "subject": "[Binance] Mã xác minh rút tiền bảo mật: 710485",
            "otp": "710485",
            "time": dt(3.0),
            "is_read": 1,
            "text": "Bạn đang thực hiện thao tác rút 1,500 USDT. Mã xác nhận là 710485. Không cung cấp mã này cho nhân viên hỗ trợ.",
            "html": """
            <div style="font-family:Arial,sans-serif;max-width:540px;margin:0 auto;padding:24px;background:#181a20;color:#eaecef;border-radius:12px">
              <div style="color:#f0b90b;font-size:24px;font-weight:bold;margin-bottom:16px">BINANCE</div>
              <h3 style="color:#ffffff;font-size:18px;margin:0 0 12px">Yêu cầu rút tiền</h3>
              <p style="font-size:14px;color:#848e9c;line-height:1.5">Mã xác thực rút tiền của bạn:</p>
              <div style="background:#2b313a;border-radius:8px;padding:18px;text-align:center;font-size:32px;font-weight:bold;letter-spacing:6px;color:#f0b90b;margin:16px 0">710485</div>
              <p style="font-size:13px;color:#848e9c">Số tiền: 1,500.00 USDT · Mạng lưới: TRC20</p>
            </div>
            """
        },
        {
            "mailbox": "dev-team@internal.app",
            "from": "Telegram <login@telegram.org>",
            "to": "dev-team@internal.app",
            "subject": "Telegram code: 52914",
            "otp": "52914",
            "time": dt(5.0),
            "is_read": 0,
            "text": "Dear user, your Telegram login code is 52914. Do not give this code to anyone.",
            "html": """
            <div style="font-family:-apple-system,sans-serif;max-width:500px;margin:0 auto;padding:20px;background:#ffffff;border-radius:10px;border:1px solid #e2e8f0">
              <h2 style="color:#2481cc;margin-top:0">Telegram Messenger</h2>
              <p style="font-size:15px;color:#222">Mã đăng nhập Telegram của bạn:</p>
              <div style="font-size:36px;font-weight:bold;color:#2481cc;letter-spacing:4px;margin:16px 0">52914</div>
              <p style="font-size:13px;color:#718096">Đừng đưa mã này cho bất kỳ ai, ngay cả khi họ xưng là hỗ trợ Telegram.</p>
            </div>
            """
        },
        {
            "mailbox": "support@mybrand.io",
            "from": "Amazon Web Services <no-reply-aws@amazon.com>",
            "to": "support@mybrand.io",
            "subject": "AWS Account sign-in verification code: 620419",
            "otp": "620419",
            "time": dt(8.5),
            "is_read": 1,
            "text": "Your AWS sign-in verification code is 620419. This code expires in 15 minutes.",
            "html": """
            <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;padding:24px;border:1px solid #eaeded;border-radius:8px">
              <div style="font-size:22px;font-weight:bold;color:#232f3e;margin-bottom:12px">Amazon Web Services</div>
              <p style="color:#16191f;font-size:14px">Verification code for Root account sign-in:</p>
              <div style="background:#f2f3f3;padding:14px;font-size:28px;font-weight:bold;letter-spacing:5px;color:#ec7211;text-align:center;margin:16px 0">620419</div>
              <p style="font-size:12px;color:#545b64">Amazon Web Services, Inc. is an IT service management company.</p>
            </div>
            """
        },
        {
            "mailbox": "auth@gmail-otp.dev",
            "from": "Microsoft Security <account-security-noreply@accountprotection.microsoft.com>",
            "to": "auth@gmail-otp.dev",
            "subject": "Mã bảo mật tài khoản Microsoft: 382910",
            "otp": "382910",
            "time": dt(22.0), # Yesterday
            "is_read": 1,
            "text": "Mã bảo mật Microsoft của bạn là 382910. Vui lòng nhập mã này để hoàn tất xác minh tài khoản.",
            "html": """
            <div style="font-family:'Segoe UI',sans-serif;max-width:540px;margin:0 auto;padding:24px;background:#ffffff;border:1px solid #e1dfdd;border-radius:8px">
              <div style="font-size:20px;font-weight:600;color:#0078d4;margin-bottom:16px">Tài khoản Microsoft</div>
              <p style="font-size:14px;color:#323130">Mã bảo mật cho đăng nhập:</p>
              <div style="font-size:32px;font-weight:bold;letter-spacing:6px;color:#0078d4;padding:12px 0">382910</div>
              <p style="font-size:12px;color:#605e5c">Nếu bạn không nhận ra yêu cầu này, hãy bảo mật tài khoản của bạn ngay lập tức.</p>
            </div>
            """
        },
        {
            "mailbox": "billing@fintech.co",
            "from": "Stripe Payments <support@stripe.com>",
            "to": "billing@fintech.co",
            "subject": "Your Stripe authentication code is 914028",
            "otp": "914028",
            "time": dt(28.0),
            "is_read": 1,
            "text": "Your Stripe login verification code is 914028. Valid for 10 minutes.",
            "html": """
            <div style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:500px;margin:0 auto;padding:20px;border:1px solid #e6ebf1;border-radius:8px">
              <h3 style="color:#635bff;margin-top:0">Stripe Dashboard</h3>
              <p style="color:#425466;font-size:14px">Use the verification code below:</p>
              <div style="font-size:32px;font-weight:bold;letter-spacing:5px;color:#0a2540;margin:16px 0">914028</div>
            </div>
            """
        },
        {
            "mailbox": "auth@gmail-otp.dev",
            "from": "OpenAI <support@openai.com>",
            "to": "auth@gmail-otp.dev",
            "subject": "Mã xác nhận đăng nhập OpenAI: 153892",
            "otp": "153892",
            "time": dt(48.0), # 2 days ago
            "is_read": 1,
            "text": "Mã xác nhận đăng nhập ChatGPT / OpenAI của bạn là 153892.",
            "html": """
            <div style="font-family:sans-serif;max-width:500px;margin:0 auto;padding:20px;border:1px solid #10a37f;border-radius:8px">
              <h2 style="color:#10a37f;margin-top:0">OpenAI</h2>
              <p style="font-size:14px;color:#353740">Mã đăng nhập tạm thời của bạn:</p>
              <div style="font-size:32px;font-weight:bold;color:#202123;letter-spacing:4px;margin:14px 0">153892</div>
            </div>
            """
        },
        {
            "mailbox": "security-alerts@domain.com",
            "from": "Cloudflare <no-reply@cloudflare.com>",
            "to": "security-alerts@domain.com",
            "subject": "Cảnh báo bảo mật: Phát hiện đăng nhập từ IP mới",
            "otp": None,
            "time": dt(72.0),
            "is_read": 0,
            "text": "Tài khoản Cloudflare của bạn vừa được đăng nhập từ IP 113.161.42.18 (Hà Nội, Việt Nam). Nếu không phải bạn, hãy khóa API token.",
            "html": """
            <div style="font-family:sans-serif;max-width:520px;margin:0 auto;padding:20px;border-top:4px solid #f6821f">
              <h3 style="color:#f6821f">Cloudflare Security Alert</h3>
              <p style="font-size:14px;color:#333">Phát hiện phiên đăng nhập mới từ địa chỉ IP chưa nhận diện.</p>
              <p style="font-size:13px;color:#666">Địa điểm: Hà Nội, Việt Nam · Trình duyệt: Chrome on Windows</p>
            </div>
            """
        }
    ]

    for item in sample_messages:
        mb_id = mb_ids[item["mailbox"]]
        size = len(item["text"].encode()) + len(item["html"].encode())
        sha = hashlib.sha256((item["from"] + item["subject"] + item["time"]).encode()).hexdigest()
        
        cur.execute(
            """INSERT INTO messages (mailbox_id, envelope_from, envelope_to, subject, received_at, otp, body_text, body_html, size_bytes, raw_sha256, has_attachments)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (mb_id, item["from"], item["to"], item["subject"], item["time"], item["otp"], item["text"], item["html"], size, sha)
        )
        msg_id = cur.lastrowid
        
        # Mark read if applicable
        if item["is_read"]:
            cur.execute(
                """INSERT OR REPLACE INTO message_reads (message_id, user_id, read_at)
                   VALUES (?, ?, ?)""",
                (msg_id, admin_id, item["time"])
            )

    conn.commit()

    # Re-sync full-text search
    print("[Seed] Backfilling full-text search index...")
    app.init_search_index(conn)
    conn.commit()
    raw_ctx.__exit__(None, None, None)

    print("\n" + "="*60)
    print("[SUCCESS] SEED HOAN TAT THANH CONG!")
    print("="*60)
    print("THONG TIN DANG NHAP TEST:")
    print("1. Tai khoan Quan tri vien (Admin):")
    print("   - Username: admin")
    print("   - Password: admin12345")
    print("   - Quyen: Quan tri toan bo mailbox, routing rules, users, referrals")
    print("\n2. Tai khoan Thanh vien (User):")
    print("   - Username: user")
    print("   - Password: user123456")
    print("   - Quyen: Xem mailbox duoc gan (dev-team@internal.app, billing@fintech.co)")
    print("\n3. Ma gioi thieu dang ky thu nghiem (Referral Code):")
    print("   - Code: REF-GMAIL-2026")
    print("="*60 + "\n")

if __name__ == "__main__":
    run_seed()
