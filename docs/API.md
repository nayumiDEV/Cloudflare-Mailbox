# OTP Inbox — API Documentation

**Languages:** [English](en/API.md) · [Tiếng Việt](vi/API.md) · [中文](zh/API.md) · [Русский](ru/API.md)

REST API for **OTP Inbox** (FastAPI). Default base URL:

```
https://<your-host>
```

Interactive docs (Swagger): `GET /docs`

---

## Mục lục

1. [Xác thực](#1-xác-thực)
2. [Quy ước chung](#2-quy-ước-chung)
3. [Health & UI](#3-health--ui)
4. [Auth](#4-auth)
5. [Tài khoản (/api/me)](#5-tài-khoản-apime)
6. [Referral](#6-referral)
7. [Ingest (Worker)](#7-ingest-worker)
8. [Dashboard](#8-dashboard)
9. [Messages](#9-messages)
10. [Attachments](#10-attachments)
11. [Mailboxes](#11-mailboxes)
12. [Admin — Assign / Import](#12-admin--assign--import)
13. [Admin — Routing rules](#13-admin--routing-rules)
14. [Admin — Referrals](#14-admin--referrals)
15. [Admin — Users](#15-admin--users)
16. [Notifications & Audit](#16-notifications--audit)
17. [Developer API (API Key)](#17-developer-api-convenience-apiv1)
18. [Outbound send](#18-outbound-send)
19. [Mã lỗi](#19-mã-lỗi)
20. [Ví dụ curl](#20-ví-dụ-curl)

---

## 1. Xác thực

Hệ thống **ưu tiên tích hợp API**. Hầu hết endpoint `/api/*` chấp nhận **API key hoặc session**.

| Cơ chế | Cách dùng | Dùng cho |
| --- | --- | --- |
| **API Key** (khuyến nghị cho tích hợp) | Header `Authorization: Bearer sk-otp-...` | **Mọi** `/api/*` đọc + ghi (trừ các ngoại lệ bên dưới). **Không cần CSRF**. |
| **Session cookie** | Cookie `otp_session` (HttpOnly, SameSite=Lax, Secure tùy `COOKIE_SECURE`) | Web UI |
| **CSRF** | Header `X-CSRF-Token: <csrf>` | Chỉ khi dùng **session cookie** trên `POST`/`PATCH`/`DELETE` |
| **Worker token** | Header `X-Worker-Token: <WORKER_INGEST_TOKEN>` | `POST /api/ingest` |

**Ngoại lệ không dùng API key user:**

| Endpoint | Auth |
| --- | --- |
| `POST /api/login`, `POST /api/register` | username/password (+ referral) |
| `POST /api/logout` | session cookie |
| `GET /api/auth/config`, `GET /health` | public |
| `POST /api/ingest` | worker token |

Khi gửi cả cookie và Bearer, **Bearer được ưu tiên**.

Rate limit API key mặc định: **120 req / 60s / key**.

### Tích hợp bằng API key

```http
GET /api/dashboard
Authorization: Bearer sk-otp-YOUR_KEY
```

```http
POST /api/mailboxes/create
Authorization: Bearer sk-otp-YOUR_KEY
Content-Type: application/json

{"address":"otp-test@example.com"}
```

Không gửi `X-CSRF-Token` khi dùng Bearer.

### Lấy session + CSRF (web UI)

```http
POST /api/login
Content-Type: application/json

{"username":"admin","password":"your-password"}
```

Response:

```json
{
  "user": { "...": "..." },
  "csrf": "<csrf-token>"
}
```

Cookie `otp_session` được set tự động. Lưu `csrf` và gửi lại ở mọi request ghi (chỉ với session).

### Lấy / xoay API key

```http
POST /api/me/regenerate-key
Authorization: Bearer sk-otp-OLD_KEY
```

hoặc với session + CSRF:

```http
POST /api/me/regenerate-key
X-CSRF-Token: <csrf>
```

```json
{ "api_key": "sk-otp-..." }
```

> Plaintext API key **chỉ hiện một lần**. Prefix (`api_key_prefix`, 16 ký tự đầu) lưu trong user object.

---

## 2. Quy ước chung

### Request

- Body JSON: `Content-Type: application/json`
- Timestamp: ISO-8601 UTC string (ví dụ `2026-09-29T04:00:00+00:00`)
- Email luôn được normalize (trim + lowercase)

### Response lỗi (FastAPI)

```json
{ "detail": "Human-readable message" }
```

Hoặc với 429:

```
Retry-After: <seconds>
```

### Object `user`

```json
{
  "id": 1,
  "public_id": "U-A1B2C3D4E5",
  "username": "admin",
  "role": "admin",
  "api_key_prefix": "sk-otp-xxxx....",
  "is_active": true,
  "can_generate_codes": false,
  "created_via": "admin",
  "created_at": "...",
  "updated_at": "..."
}
```

`role`: `"admin"` | `"user"`

### Quyền truy cập mailbox

- **Admin**: thấy mọi mailbox / message.
- **User**: chỉ mailbox có trong `mailbox_access`.
- **Owner** (`is_owner`): admin **hoặc** `mailbox.owner_user_id == user.id`. Cần owner để share / transfer / revoke / return-to-rules.

### Rate limit

| Hành động | Mặc định |
| --- | --- |
| Login | 10 lần / 900s / IP |
| Register | 5 lần / 3600s / IP |
| Generate referral code | 5 lần / 3600s / user |

### Credential rules

- Username: `3–40` ký tự, `[A-Za-z0-9_.-]`
- Password: tối thiểu `10` ký tự, tối đa `200`

---

## 3. Health & UI

### `GET /health`

Public. Không cần auth.

```json
{
  "status": "ok",
  "app": "OTP Inbox",
  "version": "abc123def456",
  "search": "fts",
  "registration": false,
  "code_generation": false
}
```

`search`: `"fts"` hoặc `"like"`.

### `GET /`

HTML UI.

### `GET /docs`

Swagger UI.

---

## 4. Auth

### `GET /api/auth/config`

Public. Bootstrap màn login.

```json
{
  "registration_open": true,
  "app": "OTP Inbox"
}
```

### `POST /api/login`

Body:

```json
{
  "username": "admin",
  "password": "change-me-now"
}
```

Response `200`:

```json
{
  "user": { "...": "..." },
  "csrf": "..."
}
```

Errors: `400`, `401` (`Invalid username or password`), `429`.

### `POST /api/logout`

Xóa session + cookie. Luôn trả `{ "ok": true }`.

### `POST /api/register`

Cần registration đang mở + referral code hợp lệ.

Body:

```json
{
  "username": "newbie",
  "password": "password-1234",
  "referral_code": "REF-XXXX-YYYY"
}
```

Response `200`: `{ "user", "csrf", "referral" }` — đồng thời đăng nhập.

Lưu ý: user mới **không** có mailbox và **không** có quyền tạo mã. Admin/rules phải gán sau.

Errors: `400`, `403` (registration off), `409` (username tồn tại), `429`.

### `GET /api/me`

Auth: session.

```json
{
  "user": { "...": "..." },
  "csrf": "...",
  "referral": { "...": "..." },
  "registration_open": true
}
```

---

## 5. Tài khoản (`/api/me`)

### `POST /api/me/password`

Auth: session + CSRF.

```json
{
  "old_password": "...",
  "new_password": "at-least-10-chars"
}
```

→ `{ "ok": true }` — **xóa mọi session** của user (phải login lại).

### `POST /api/me/regenerate-key`

Auth: session + CSRF.

```json
{ "api_key": "sk-otp-..." }
```

### `GET /api/users/lookup?public_id=U-XXXX`

Auth: session. Tra cứu user theo public ID.

```json
{
  "public_id": "U-XXXX",
  "username": "alice",
  "role": "user"
}
```

`404` nếu không tồn tại / inactive.

---

## 6. Referral

### Object `referral` (summary)

```json
{
  "global_enabled": true,
  "can_generate": true,
  "permission_active": true,
  "reason": null,
  "code": {
    "id": 12,
    "fingerprint": "REF-****-AB12",
    "status": "active",
    "max_uses": 1,
    "uses": 0,
    "remaining": 1,
    "expires_at": null,
    "created_at": "..."
  },
  "totals": { "codes": 1, "capacity": 1, "uses": 0 },
  "registrations": [
    {
      "id": 1,
      "code_fingerprint": "...",
      "created_at": "...",
      "username": "newbie",
      "public_id": "U-...",
      "is_active": 1
    }
  ]
}
```

`status`: `"active"` | `"disabled"` | `"expired"` | `"exhausted"`

Format mã: `REF-XXXX-YYYY` (alphabet không nhầm lẫn: bỏ I/O/0/1).

### `GET /api/referrals/me`

Auth: session → object `referral` ở trên.

### `POST /api/referrals/code`

Auth: session + CSRF. Cần quyền generate (global ON + `can_generate_codes`, admin luôn được).

Body (optional):

```json
{ "max_uses": 3 }
```

`max_uses` clamp `1..100` (mặc định env `REFERRAL_MAX_USES_DEFAULT`).

Response:

```json
{
  "code": "REF-XXXX-YYYY",
  "id": 12,
  "max_uses": 3,
  "expires_at": null,
  "warning": "This code is shown only once."
}
```

Mỗi lần gọi **vô hiệu hóa** code cũ của user và tạo code mới.

### `GET /api/me/referrals/code-history`

Auth: session.

```json
{
  "items": [
    {
      "id": 12,
      "code_fingerprint": "...",
      "max_uses": 1,
      "uses": 0,
      "is_active": 1,
      "expires_at": null,
      "note": "self-service",
      "created_at": "...",
      "status": "active"
    }
  ]
}
```

---

## 7. Ingest (Worker)

### `POST /api/ingest`

Auth: header `X-Worker-Token` phải khớp `WORKER_INGEST_TOKEN`.

Headers:

| Header | Bắt buộc | Mô tả |
| --- | --- | --- |
| `X-Worker-Token` | ✓ | Shared secret |
| `X-Envelope-To` | ✓ | Recipient email hợp lệ |
| `X-Envelope-From` | | Sender (mặc định `unknown@unknown.invalid`) |
| `X-Email-Subject` | | Fallback subject |
| `X-Email-Message-ID` | | Fallback Message-ID |
| `X-Email-Date` | | Received date |

Body: **raw MIME bytes** (`message/rfc822`), tối đa `MAX_EMAIL_BYTES` (mặc định 10MB).

Response `201`:

```json
{
  "ok": true,
  "message_id": 42,
  "mailbox": "user@example.com"
}
```

Duplicate (cùng `sha256` trong mailbox):

```json
{
  "ok": true,
  "duplicate": true,
  "message_id": 42,
  "mailbox": "user@example.com"
}
```

Server tự:

1. Tạo mailbox nếu chưa có
2. Sync routing rules (nếu không `exact_override`)
3. Parse OTP từ body
4. Lưu attachments
5. Gửi notification cho user có access

Errors: `401`, `400` (invalid recipient), `413` (too large).

---

## 8. Dashboard

### `GET /api/dashboard`

Auth: session.

```json
{
  "mailboxes": [
    {
      "id": 1,
      "address": "otp@example.com",
      "owner_username": "alice",
      "owner_user_id": 2,
      "exact_override": true,
      "override_reason": "manually created",
      "is_owner": true,
      "users": [
        {
          "public_id": "U-...",
          "username": "alice",
          "role": "user",
          "source": "manual",
          "rule_id": null
        }
      ],
      "message_count": 10,
      "unread_count": 2,
      "latest": {
        "id": 99,
        "mailbox_id": 1,
        "subject": "...",
        "envelope_from": "...",
        "received_at": "...",
        "otp": "123456",
        "has_attachments": 0
      }
    }
  ],
  "notifications": [
    {
      "id": 1,
      "kind": "email",
      "title": "Email mới",
      "body": "...",
      "mailbox_id": 1,
      "created_at": "...",
      "read_at": null
    }
  ],
  "referral": { "...": "..." },
  "rules": [],
  "users": [],
  "audit": [],
  "referral_stats": null
}
```

**Chỉ admin** nhận thêm:

- `users` — danh sách user
- `rules` — routing rules + `match_mailboxes` (số mailbox khớp)
- `audit` — 300 log gần nhất
- `referral_stats`:

```json
{
  "registration_enabled": true,
  "code_generation_enabled": true,
  "codes": 10,
  "uses": 5,
  "registrations": 5,
  "permitted_users": 2
}
```

---

## 9. Messages

### `GET /api/messages`

Auth: session.

Query params:

| Param | Default | Mô tả |
| --- | --- | --- |
| `mailbox_id` | `0` | Lọc 1 mailbox (`0` = tất cả visible) |
| `page` | `1` | Trang (≥1) |
| `page_size` | `30` | `1..100` |
| `q` | `""` | Search (max `MAX_SEARCH_QUERY`, mặc định 200) |
| `scope` | `"all"` | `"all"` = headers + body; `"headers"` = subject/from/to/otp |
| `unread` | `0` | `1` = chỉ chưa đọc |
| `otp_only` | `0` | `1` = chỉ message có OTP |

Response:

```json
{
  "items": [
    {
      "id": 99,
      "mailbox_id": 1,
      "mailbox_address": "otp@example.com",
      "envelope_from": "noreply@service.com",
      "envelope_to": "otp@example.com",
      "subject": "Your code",
      "received_at": "...",
      "otp": "123456",
      "has_attachments": 0,
      "is_read": 0,
      "preview": "Your verification code is 123456..."
    }
  ],
  "page": 1,
  "page_size": 30,
  "total": 42,
  "mailbox": 1,
  "query": "123456",
  "scope": "all",
  "engine": "fts"
}
```

### `GET /api/messages/{message_id}`

```json
{
  "id": 99,
  "mailbox_id": 1,
  "envelope_from": "...",
  "envelope_to": "...",
  "subject": "...",
  "message_id": "<mid@host>",
  "received_at": "...",
  "otp": "123456",
  "body_text": "...",
  "has_html": true,
  "is_read": false,
  "attachments": [
    {
      "id": 5,
      "filename": "a.png",
      "content_type": "image/png",
      "disposition": "inline",
      "size_bytes": 1234
    }
  ]
}
```

### `GET /api/messages/{message_id}/html`

Auth: session. Trả HTML đã sanitize (iframe).

Query:

- `remote=0|1` — cho phép ảnh remote
- `theme=light|dark`

### `GET /api/messages/{message_id}/raw`

Raw MIME (`message/rfc822`).

### `GET /api/messages/{message_id}/raw-text`

```json
{ "raw": "From: ...\n..." }
```

### `POST /api/messages/{message_id}/read`

Auth: session + CSRF → `{ "ok": true }`

### `POST /api/messages/{message_id}/unread`

Auth: session + CSRF → `{ "ok": true }`

### `POST /api/messages/bulk-read`

Auth: session + CSRF.

```json
{ "message_ids": [1, 2, 3] }
```

→ `{ "ok": true, "count": 3 }` (tối đa 200 id)

### `POST /api/messages/bulk-unread`

Tương tự bulk-read.

---

## 10. Attachments

### `GET /api/attachments/{attachment_id}`

Auth: session. Binary download / inline image.

Headers response: `Content-Disposition`, `X-Content-Type-Options: nosniff`.

---

## 11. Mailboxes

Tất cả mutating endpoint cần **session + CSRF**.

### `POST /api/mailboxes/create`

```json
{
  "address": "new@example.com",
  "owner_public_id": "U-XXXX",
  "target_public_ids": ["U-XXXX", "U-YYYY"]
}
```

- **User thường**: buộc owner = chính mình; `target_public_ids` bị bỏ qua; `exact_override=1`.
- **Admin**: có thể tạo mailbox “rule-eligible” (`exact_override=0`) nếu không chỉ owner/targets.

```json
{
  "ok": true,
  "mailbox_id": 10,
  "notice": "Đã tạo mailbox new@example.com."
}
```

`409` nếu address đã tồn tại.

### `POST /api/mailboxes/create-bulk`

```json
{
  "addresses": "a@x.com\nb@x.com"
}
```

hoặc `"addresses": ["a@x.com", "b@x.com"]`

Max **300** địa chỉ. Mỗi mailbox tạo với owner = caller, `exact_override=1`.

```json
{
  "ok": true,
  "created": 2,
  "existing": 0,
  "invalid": [],
  "notice": "..."
}
```

### `POST /api/mailboxes/{id}/share`

Owner only.

```json
{ "target_public_id": "U-XXXX" }
```

Bật exact override, thêm access cho target (owner giữ quyền).

### `POST /api/mailboxes/{id}/transfer`

Owner only.

```json
{ "target_public_id": "U-XXXX" }
```

Đổi owner sang target, **xóa toàn bộ access cũ**, chỉ target còn quyền.

### `POST /api/mailboxes/{id}/revoke`

Owner only.

```json
{ "target_public_id": "U-XXXX" }
```

Thu hồi access của 1 user (không thể revoke owner).

### `POST /api/mailboxes/{id}/return-to-rules`

Owner only. Xóa owner + exact override + access, re-apply routing rules.

### Bulk variants

| Endpoint | Body |
| --- | --- |
| `POST /api/mailboxes/bulk-share` | `{ "mailbox_ids": [1,2], "target_public_id": "U-..." }` |
| `POST /api/mailboxes/bulk-transfer` | tương tự |
| `POST /api/mailboxes/bulk-revoke` | tương tự |
| `POST /api/mailboxes/bulk-return-to-rules` | `{ "mailbox_ids": [1,2] }` |

Max **300** mailbox ids. Response dạng `{ "ok": true, "count": N, "notice": "..." }`.

---

## 12. Admin — Assign / Import

Auth: **admin** + CSRF.

### `POST /api/admin/assign`

```json
{
  "mailbox_ids": [1, 2],
  "target_public_ids": ["U-AAAA", "U-BBBB"]
}
```

Gán exact override + manual access cho các target.

### `POST /api/admin/import-mailboxes`

```json
{
  "addresses": "a@x.com\nb@x.com",
  "target_public_ids": ["U-AAAA"]
}
```

Max **1000** addresses. Tạo mới hoặc cập nhật; luôn `exact_override=1`.

```json
{
  "ok": true,
  "created": 5,
  "existing": 2,
  "assigned": 7,
  "already_assigned": 0,
  "invalid": [],
  "sample": ["a@x.com", "..."],
  "notice": "..."
}
```

### `GET /api/admin/mailboxes/search?q=otp&user_id=5&limit=30`

Admin. `q` tối thiểu 2 ký tự.

```json
{
  "items": [
    {
      "id": 1,
      "address": "otp@example.com",
      "owner_user_id": 2,
      "exact_override": true,
      "assigned": true
    }
  ]
}
```

`assigned` = user `user_id` đã có access hay chưa.

---

## 13. Admin — Routing rules

Auth: **admin** (+ CSRF cho mutating).

`rule_type`: `"regex"` | `"prefix"` | `"domain"` | `"catch_all"`

| Loại | Pattern | Mô tả / Ví dụ |
| --- | --- | --- |
| **`regex`** | Biểu thức Regex Python | So khớp regex trên toàn bộ email (case-insensitive). Ví dụ: `^otp-\d+@` hoặc `^vip-[a-z0-9]+@domain\.com$` |
| **`prefix`** | Tiền tố | So khớp phần đầu của địa chỉ email (case-insensitive). Ví dụ: `otp-` hoặc `team-*` |
| **`domain`** | Tên miền | So khớp email theo domain hoặc subdomain. Nhập `example.com` hoặc `@example.com` — khớp cả `user@example.com` lẫn `user@mail.example.com` |
| **`catch_all`** | `*` hoặc `*@domain.com` | Gom toàn bộ email chưa có quy tắc khớp riêng: `*` (Catch-All toàn cầu) hoặc `*@domain.com` (Catch-All riêng cho domain) |

### Thứ tự ưu tiên phân giải khi nhận email (Ingest)

1. **Exact Override (`exact_override = 1`)**: Mailbox đã được admin gán cứng hoặc user sở hữu trực tiếp qua share/transfer/import. **Không bao giờ bị routing rules ghi đè**.
2. **Regex rules**: Khớp theo biểu thức chính quy đang kích hoạt (`is_active = 1`).
3. **Prefix rules**: Khớp theo tiền tố đang kích hoạt.
4. **Domain rules**: Khớp theo tên miền đang kích hoạt.
5. **Catch-All rules**: Khớp toàn bộ email còn lại chưa thuộc 4 mức trên.

### Tự động tạo và cấp Mailbox (Auto-provisioning)

Khi email được chuyển tới thông qua `POST /api/ingest`:
- Nếu địa chỉ **đã tồn tại**:
  - Nếu `exact_override = 1`: giữ nguyên quyền truy cập hiện tại.
  - Nếu `exact_override = 0`: tự động cập nhật quyền truy cập theo routing rules đang active.
- Nếu địa chỉ **chưa tồn tại**:
  - Hệ thống tự động tạo mailbox mới trong database.
  - Nếu khớp một routing rule (hoặc catch-all): cấp quyền xem (`mailbox_access`) cho user mục tiêu với `source = 'rule'`.
  - Nếu **không khớp bất kỳ rule nào**: mailbox được tạo dưới dạng **mồ côi** (`owner_user_id = NULL`, không user nào có access). Trên giao diện web, Admin sẽ thấy thẻ cảnh báo **⚠️ Chưa gán (Catch-All)** để dễ dàng lọc và gán user.

### `POST /api/admin/rules`

```json
{
  "rule_type": "domain",
  "pattern": "company.com",
  "target_public_id": "U-XXXX",
  "description": "Route all company.com emails to Alice"
}
```

Response:
```json
{
  "ok": true,
  "rule_id": 3,
  "notice": "Đã tạo domain rule cho alice và reconcile mailbox hiện tại."
}
```

Sau khi tạo/sửa/xóa/đổi trạng thái rule: server tự động **reconcile** toàn bộ mailbox có `exact_override = 0`.

### `PATCH /api/admin/rules/{rule_id}`

Cùng định dạng body như khi tạo (`rule_type`, `pattern`, `target_public_id`, `description`).

### `POST /api/admin/rules/{rule_id}/status`

Bật hoặc tắt rule:
```json
{ "is_active": false }
```

### `DELETE /api/admin/rules/{rule_id}`

Xóa rule và tự động recalculate access cho các mailbox liên quan:
→ `{ "ok": true, "notice": "Routing rule đã xóa và access đã được reconcile." }`

### `GET /api/admin/rules/test?address=test@company.com`

Kiểm tra một địa chỉ email sẽ khớp với những rule nào trước khi áp dụng:

```json
{
  "address": "test@company.com",
  "exact_override": false,
  "owner": null,
  "matches": [
    {
      "id": 3,
      "rule_type": "domain",
      "pattern": "company.com",
      "target_username": "alice",
      "target_public_id": "U-XXXX",
      "description": "Route all company.com emails to Alice"
    }
  ]
}
```

Nếu mailbox đã có exact override, API sẽ trả về `exact_override: true` cùng tên `owner`, đồng thời `matches: []`.

---

## 14. Admin — Referrals

Auth: **admin** (+ CSRF cho mutating).

### `GET /api/admin/referrals?limit=100&owner_id=0`

```json
{
  "settings": {
    "registration_enabled": true,
    "referral_code_generation": true
  },
  "codes": [
    {
      "id": 1,
      "fingerprint": "REF-****-AB12",
      "owner_user_id": null,
      "owner_username": null,
      "created_by": 1,
      "max_uses": 10,
      "uses": 2,
      "remaining": 8,
      "expires_at": null,
      "is_active": true,
      "note": "...",
      "created_at": "...",
      "status": "active"
    }
  ],
  "registrations": [ "..." ],
  "stats": {
    "codes": 10,
    "uses": 5,
    "personal_codes": 3,
    "registrations": 5
  }
}
```

> Admin **không bao giờ** thấy plaintext code từ list — chỉ lúc create.

### `PATCH /api/admin/referrals/settings`

```json
{
  "registration_enabled": true,
  "referral_code_generation": false
}
```

### `POST /api/admin/referrals`

```json
{
  "max_uses": 5,
  "ttl_days": 30,
  "note": "batch invite",
  "owner_public_id": ""
}
```

- `ttl_days`: số ngày, `0` / `"never"` / omit = không hết hạn
- `owner_public_id`: optional — gắn code vào 1 user

```json
{
  "ok": true,
  "code": "REF-XXXX-YYYY",
  "id": 1,
  "max_uses": 5,
  "expires_at": "...",
  "warning": "This code is shown only once."
}
```

### `PATCH /api/admin/referrals/{code_id}`

Fields tùy chọn: `is_active`, `max_uses`, `ttl_days`, `note`.

### `DELETE /api/admin/referrals/{code_id}`

→ `{ "ok": true }`

### `POST /api/admin/users/{user_id}/code-permission`

```json
{ "can_generate_codes": true }
```

Khi tắt: retire mọi code active của user → `{ "ok", "user", "retired_codes" }`.

---

## 15. Admin — Users

Auth: **admin** (+ CSRF cho mutating).

### `POST /api/admin/users`

```json
{
  "username": "alice",
  "password": "password-1234"
}
```

```json
{
  "ok": true,
  "user": { "...": "..." },
  "api_key": "sk-otp-..."
}
```

Role luôn `"user"`. API key hiện **một lần**.

### `POST /api/admin/users/{user_id}/status`

```json
{ "is_active": false }
```

Chỉ áp dụng user role=`user`. Disable → xóa sessions.

### `PATCH /api/admin/users/{user_id}`

```json
{
  "username": "alice2",
  "role": "user",
  "new_password": ""
}
```

- Không thể tự hạ role admin của chính mình
- Không thể demote admin cuối cùng còn active
- Có `new_password` → reset password + xóa sessions

### `GET /api/admin/users/{user_id}/detail`

```json
{
  "user": { "...": "..." },
  "mailboxes": [ { "...mailbox + source/rule_id...": "..." } ],
  "rules": [ { "...routing_rules targeting this user...": "..." } ]
}
```

---

## 16. Notifications & Audit

### `POST /api/notifications/read`

Auth: session + CSRF. Đánh dấu tất cả notification của user là đã đọc.

→ `{ "ok": true }`

### `GET /api/admin/audit?limit=300`

Auth: admin. `limit` clamp `1..1000`.

```json
{
  "items": [
    {
      "id": 1,
      "actor_user_id": 1,
      "action": "mailbox.created",
      "mailbox_id": 10,
      "target_user_id": 2,
      "details": "...",
      "created_at": "...",
      "actor_username": "admin",
      "target_username": "alice",
      "mailbox_address": "a@x.com"
    }
  ]
}
```

---

## 17. Developer API (convenience `/api/v1/*`)

Các endpoint dưới đây là **shortcut cho automation** (poll OTP nhẹ). Toàn bộ `/api/*` còn lại cũng gọi được bằng cùng Bearer API key (xem §1).

Auth: `Authorization: Bearer <api_key>` (hoặc session). Không cần CSRF với Bearer.

Rate limit mặc định: **120 request / 60s / API key** (`API_KEY_MAX_REQUESTS`, `API_KEY_WINDOW_SECONDS`).

### `GET /api/v1/mailboxes`

```json
{ "items": [ { "id": 10, "address": "otp@example.com" } ] }
```

- Admin: mọi mailbox
- User: chỉ mailbox có access

### `GET /api/v1/inbox`

| Param | Default | Mô tả |
| --- | --- | --- |
| `limit` | `50` | `1..200` |
| `since` | `""` | ISO timestamp — chỉ message `received_at > since` |
| `address` | `""` | Lọc đúng 1 mailbox (email đã normalize) |
| `mailbox_id` | `0` | Lọc theo id (phải có access) |
| `otp_only` | `0` | `1` = chỉ message có OTP |
| `include_body` | `0` | `1` = kèm `body_text` đầy đủ |

```json
{
  "items": [
    {
      "id": 99,
      "mailbox_id": 10,
      "address": "otp@example.com",
      "envelope_from": "...",
      "envelope_to": "...",
      "subject": "...",
      "received_at": "...",
      "otp": "123456",
      "has_attachments": 0,
      "preview": "short preview…"
    }
  ]
}
```

Mặc định **không** trả `body_text` (nhẹ hơn khi poll). OTP dạng `123-456` được chuẩn hóa thành `123456`.

### `GET /api/v1/messages/{message_id}`

Chi tiết 1 message (OTP đã repair nếu trước đó bị bắt nhầm mã màu CSS):

```json
{
  "id": 99,
  "mailbox_id": 10,
  "address": "otp@example.com",
  "envelope_from": "...",
  "envelope_to": "...",
  "subject": "...",
  "message_id": "<...>",
  "received_at": "...",
  "otp": "123456",
  "body_text": "...",
  "has_html": true,
  "has_attachments": 0,
  "attachments": []
}
```

### `GET /api/v1/messages/{message_id}/raw`

Raw MIME (`message/rfc822`) nếu user có access.

---

## 18. Outbound send

Requires `WORKER_SEND_URL` (Cloudflare Email Sending + Workers Paid). Full guide: [en/SEND.md](en/SEND.md) · [vi/SEND.md](vi/SEND.md).

### `POST /api/mailboxes/{mailbox_id}/send`

Auth: session+CSRF or Bearer API key. **From** is always the mailbox address.

```json
{
  "to": "user@example.com",
  "cc": ["ops@example.com"],
  "bcc": [],
  "subject": "Hello",
  "text": "Plain body",
  "html": "<p>HTML</p>",
  "attachments": [
    {"filename": "a.txt", "type": "text/plain", "content": "SGVsbG8="}
  ]
}
```

```json
{
  "ok": true,
  "id": 41,
  "mailbox": "otp@example.com",
  "cf_message_id": "…",
  "status": "sent",
  "to": ["user@example.com"],
  "subject": "Hello"
}
```

`503` if send is not configured. Failed Worker delivery still inserts `outbound_messages` with `status=failed`.

### `GET /api/sent`

Query: `mailbox_id`, `page`, `page_size`. Returns outbound rows visible to the caller.

### `GET /api/sent/{id}`

Full outbound message (body + recipients).

---

## 19. Mã lỗi

| HTTP | Ý nghĩa |
| --- | --- |
| `400` | Input không hợp lệ |
| `401` | Chưa đăng nhập / sai token / sai API key / sai worker token |
| `403` | CSRF sai / không đủ quyền (admin/owner/permission) |
| `404` | Không tìm thấy / không có access (thường trả 404 thay vì 403 để tránh leak) |
| `409` | Conflict (username / mailbox đã tồn tại) |
| `413` | Email quá lớn |
| `429` | Rate limited (`Retry-After`) |
| `503` | Không allocate được referral code (retry) |

---

## 20. Ví dụ curl

### Login + lấy CSRF

```bash
curl -c cookies.txt -X POST https://HOST/api/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"your-password"}'
# lưu "csrf" từ JSON
```

### Dashboard

```bash
curl -b cookies.txt https://HOST/api/dashboard
```

### Search messages

```bash
curl -b cookies.txt \
  'https://HOST/api/messages?q=123456&scope=all&otp_only=1&page=1&page_size=30'
```

### Mark read (cần CSRF)

```bash
curl -b cookies.txt -X POST https://HOST/api/messages/99/read \
  -H "X-CSRF-Token: $CSRF"
```

### Tạo mailbox

```bash
curl -b cookies.txt -X POST https://HOST/api/mailboxes/create \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $CSRF" \
  -d '{"address":"otp-test@example.com"}'
```

### Developer inbox (API key)

```bash
curl https://HOST/api/v1/mailboxes \
  -H "Authorization: Bearer sk-otp-YOUR_KEY"

curl "https://HOST/api/v1/inbox?limit=20&otp_only=1" \
  -H "Authorization: Bearer sk-otp-YOUR_KEY"
```

### Ingest từ worker

```bash
curl -X POST https://HOST/api/ingest \
  -H "X-Worker-Token: $WORKER_INGEST_TOKEN" \
  -H "X-Envelope-To: otp@example.com" \
  -H "X-Envelope-From: noreply@service.com" \
  -H "Content-Type: application/octet-stream" \
  --data-binary @message.eml
```

### Poll OTP mới (script)

```bash
# Poll tin có OTP sau timestamp cho một địa chỉ cụ thể
SINCE="2026-09-29T00:00:00+00:00"
curl -s "https://HOST/api/v1/inbox?address=otp@example.com&otp_only=1&limit=10&since=$SINCE" \
  -H "Authorization: Bearer $API_KEY" \
  | jq '.items[] | {address, otp, received_at, subject}'
```

---

## Phụ lục: Notification `kind`

| kind | Khi nào |
| --- | --- |
| `email` | Email mới ingest |
| `mailbox_created` | Mailbox được tạo & gán |
| `shared` / `transfer_in` / `transfer_out` | Share / transfer |
| `revoke` | Thu hồi access |
| `routing_reset` | Return-to-rules |
| `admin_assign` | Admin assign / import |
| `referral` | Referral dùng / quyền code thay đổi |

---

## Phụ lục: Exact override vs routing

| Trạng thái | Ý nghĩa |
| --- | --- |
| `exact_override = 1` | Owner/access do manual/admin; **rules không đụng** |
| `exact_override = 0` | Access theo prefix/regex rules; sync mỗi lần ingest / đổi rule |

`return-to-rules` / `bulk-return-to-rules` chuyển mailbox về trạng thái rule-driven.

---

*Sinh từ source `app.py`. Khi API thay đổi, cập nhật file này hoặc đối chiếu `/docs`.*
