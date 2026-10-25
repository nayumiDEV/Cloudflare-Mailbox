# OTP Inbox — API Overview (English)

**Languages:** [English](API.md) · [Tiếng Việt](../vi/API.md) · [中文](../zh/API.md) · [Русский](../ru/API.md)

Full reference (1 300+ lines): **[../API.md](../API.md)**
Interactive Swagger UI: `GET /docs` on a running instance.

---

## Authentication

Most `/api/*` endpoints accept either an **API key** or a **session cookie**.

| Mechanism | How to use | When to use |
| --- | --- | --- |
| **API key** (recommended for integrations) | `Authorization: Bearer sk-otp-...` header | All `/api/*` read + write (except the exceptions below). No CSRF required. |
| **Session cookie** | `otp_session` cookie (HttpOnly, SameSite=Lax) | Web UI |
| **CSRF token** | `X-CSRF-Token: <token>` header | Required only with session cookie on `POST` / `PATCH` / `DELETE` |
| **Worker token** | `X-Worker-Token: <WORKER_INGEST_TOKEN>` header | `POST /api/ingest` only |

**Endpoints that do not accept an API key:**

| Endpoint | Auth method |
| --- | --- |
| `POST /api/login`, `POST /api/register` | username + password (+ referral code) |
| `POST /api/logout` | session cookie |
| `GET /api/auth/config`, `GET /health` | public |
| `POST /api/ingest` | worker token |

When both a cookie and a Bearer token are sent, **Bearer takes priority**.
Default API key rate limit: **120 requests / 60 s / key**.

### Quick examples

```bash
# Health check (public)
curl -s https://your-host/health

# Dashboard via API key
curl -s -H "Authorization: Bearer sk-otp-YOUR_KEY" \
     https://your-host/api/dashboard

# Create a mailbox
curl -s -X POST \
     -H "Authorization: Bearer sk-otp-YOUR_KEY" \
     -H "Content-Type: application/json" \
     -d '{"address":"otp-test@example.com"}' \
     https://your-host/api/mailboxes/create
```

---

## Key endpoints at a glance

| Group | Example endpoint | Notes |
| --- | --- | --- |
| Health | `GET /health` | Returns app version, search mode, feature flags |
| Login / logout | `POST /api/login` · `POST /api/logout` | Session-based; returns CSRF token |
| Register | `POST /api/register` | Requires a valid referral code when enabled |
| Dashboard | `GET /api/dashboard` | Message counts, mailbox list |
| Messages | `GET /api/messages` | Paginated; supports `q=` full-text search |
| Single message | `GET /api/messages/{id}` | Includes OTP field if extracted |
| Attachments | `GET /api/attachments/{id}` | Binary download |
| Mailboxes | `GET /api/mailboxes` · `POST /api/mailboxes/create` | |
| API keys | `POST /api/keys/create` · `DELETE /api/keys/{id}` | Developer API keys |
| Admin — users | `GET /api/admin/users` | Admin only |
| Admin — routing | `GET /api/admin/routing-rules` | |
| Admin — referrals | `POST /api/admin/referral-codes/generate` | |
| Ingest | `POST /api/ingest` | Called by Cloudflare Worker; requires worker token |

---

## Error codes

All errors follow the format:

```json
{"detail": "human-readable message"}
```

Common HTTP status codes:

| Code | Meaning |
| --- | --- |
| `400` | Bad request / validation error |
| `401` | Missing or invalid credentials |
| `403` | Insufficient permissions |
| `404` | Resource not found |
| `409` | Conflict (e.g. duplicate mailbox address) |
| `429` | Rate limit exceeded |
| `500` | Server error |

---

For the complete endpoint reference including all request/response schemas,
query parameters, and curl examples, see **[../API.md](../API.md)**.
