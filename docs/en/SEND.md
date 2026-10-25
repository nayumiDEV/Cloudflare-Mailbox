# Outbound email (Email Sending)

**Languages:** [English](SEND.md) · [Tiếng Việt](../vi/SEND.md)

OTP Inbox can **send** mail *from* any mailbox the user can access, using
[Cloudflare Email Service → Email Sending](https://developers.cloudflare.com/email-service/)
via the Worker `send_email` binding (`env.EMAIL.send`). The Web UI compose window
mirrors Gmail (docked draft, From / To / Cc / Bcc, attachments, Sent folder).

Inbound catch-all (Email Routing) stays free. **Sending to arbitrary recipients
requires a Workers Paid plan** (~$5/mo, includes 3 000 outbound emails/month;
then ~$0.35 / 1 000). Sends to *verified destination addresses* in your Cloudflare
account remain free on Free and Paid. See [pricing](https://developers.cloudflare.com/email-service/platform/pricing/).

---

## Architecture

```text
Web UI / API  →  POST /api/mailboxes/{id}/send
                      │
                      │  JSON + X-Worker-Token
                      ▼
              Cloudflare Worker  POST /send
                      │
                      │  env.EMAIL.send({ from, to, subject, text, html, … })
                      ▼
              Cloudflare Email Sending  →  recipient MX
```

- **From** is always the mailbox address. Callers cannot spoof another identity.
- The API records every attempt in `outbound_messages` (`sent` or `failed`) and
  writes an audit log entry.
- The Worker reuses `WORKER_INGEST_TOKEN` for `/send` auth (same shared secret as ingest).

---

## Prerequisites

1. Domain on **Cloudflare DNS**
2. **Workers Paid** (for arbitrary recipients)
3. Domain **onboarded** under Dashboard → **Compute → Email Service → Email Sending**
   (Cloudflare adds SPF / DKIM / DMARC / bounce MX under `cf-bounce`)
4. Worker deployed with `[[send_email]] name = "EMAIL"` (already in `worker/wrangler.toml`)
5. API env: `WORKER_SEND_URL=https://<worker-host>/send`

Official references:

- [Send emails (get started)](https://developers.cloudflare.com/email-service/get-started/send-emails/)
- [Workers API](https://developers.cloudflare.com/email-service/api/send-emails/workers-api/)
- [Send bindings](https://developers.cloudflare.com/email-service/configuration/send-bindings/)
- [Limits](https://developers.cloudflare.com/email-service/platform/limits/) — 50 recipients, 5 MiB message size, …

---

## Setup checklist

### 1. Upgrade Workers (if needed)

Cloudflare Dashboard → Workers & Pages → Plans → **Workers Paid**.

### 2. Onboard the sending domain

1. Dashboard → **Email Service → Email Sending → Onboard Domain**
2. Pick the same domain you use for Email Routing
3. Wait for DNS (usually minutes; up to 24 h)

Until the domain is onboarded you can only send to **verified destination
addresses** (free). After onboarding you can send to any recipient (Paid quota).

### 3. Confirm Worker binding

`worker/wrangler.toml` must contain:

```toml
[[send_email]]
name = "EMAIL"
```

Redeploy:

```bash
cd worker
npx wrangler deploy
```

Secrets (unchanged from ingest setup):

```bash
npx wrangler secret put WORKER_INGEST_TOKEN   # same as API
npx wrangler secret put APP_INGEST_URL        # https://YOUR-API/api/ingest
```

### 4. Point the API at Worker `/send`

In the API `.env` / host env:

```bash
WORKER_SEND_URL=https://otp-inbox-worker.<account>.workers.dev/send
# or your custom Worker route
```

Optional tuning:

```bash
MAX_SEND_RECIPIENTS=50
MAX_SEND_ATTACHMENTS=10
MAX_SEND_ATTACHMENT_BYTES=3145728   # 3 MiB per file
MAX_SEND_BODY_CHARS=200000
```

Restart the API. Confirm:

```bash
curl -s https://YOUR-API/health
# → "send_enabled": true
```

`GET /api/auth/config` and `GET /api/me` also expose `send_enabled`.

When `WORKER_SEND_URL` is empty, the Compose button stays hidden and send
endpoints return **503**.

---

## Using the UI

1. Sign in → sidebar **Compose** (Gmail-style blue pill)
2. Pick **From** (any mailbox you can access)
3. Fill **To** / optional **Cc** / **Bcc**, subject, body; attach files if needed
4. **Send** — toast on success; copy appears under **Sent**
5. Open a received message → **Reply** pre-fills To / subject / quoted header

Shortcuts: `C` opens Compose (or copies OTP when a code is visible); `Esc` closes the draft.

---

## API

### `POST /api/mailboxes/{mailbox_id}/send`

Auth: session + CSRF **or** Bearer API key.

```bash
curl -s -X POST \
  -H "Authorization: Bearer sk-otp-YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "to": "user@example.com",
    "cc": ["ops@example.com"],
    "subject": "Hello",
    "text": "Plain body",
    "html": "<p>HTML body</p>",
    "attachments": [{
      "filename": "note.txt",
      "type": "text/plain",
      "content": "SGVsbG8="
    }]
  }' \
  https://YOUR-API/api/mailboxes/12/send
```

| Field | Notes |
| --- | --- |
| `to` / `cc` / `bcc` | String or array; commas / newlines OK in strings. Max 50 combined. |
| `subject` | Required, ≤ 998 chars |
| `text` / `body` / `html` | At least one body required; missing HTML is derived from text |
| `attachments[]` | Base64 `content`, optional `type`, `disposition`, `contentId` |

Success:

```json
{
  "ok": true,
  "id": 41,
  "mailbox": "otp@yourdomain.com",
  "cf_message_id": "…",
  "status": "sent",
  "to": ["user@example.com"],
  "subject": "Hello"
}
```

Failed Worker delivery still inserts an `outbound_messages` row with
`status=failed` and returns 4xx/5xx with `detail`.

### `GET /api/sent`

Query: `mailbox_id` (optional), `page`, `page_size`.

Lists outbound rows the caller can see (admin: all; user: accessible mailboxes).

### `GET /api/sent/{id}`

Full body + recipients for one outbound message.

### Worker `POST /send` (internal)

Called only by the API. Header `X-Worker-Token` required. Body mirrors the
Email Sending Workers API (`from`, `to`, `subject`, `text`, `html`, `cc`,
`bcc`, `reply_to`, `attachments`). Do **not** expose this URL without the token.

---

## Limits & errors

| Limit | Value |
| --- | --- |
| Recipients (to+cc+bcc) | 50 (Cloudflare hard limit) |
| Message size | 5 MiB (25 MiB only to verified destinations) |
| Attachments (app default) | 10 files, 3 MiB each |

Common Cloudflare error codes (surfaced in `detail`):

| Code | Meaning |
| --- | --- |
| `E_SENDER_NOT_VERIFIED` / `E_SENDER_DOMAIN_NOT_AVAILABLE` | Domain not onboarded / From not allowed |
| `E_RATE_LIMIT_EXCEEDED` / `E_DAILY_LIMIT_EXCEEDED` | Quota — HTTP 429 |
| `E_TOO_MANY_RECIPIENTS` / `E_CONTENT_TOO_LARGE` | Payload limits |
| `E_SEND_NOT_CONFIGURED` | Worker missing `[[send_email]]` binding |

---

## Security notes

- From address is forced to the mailbox row — never trust client-supplied `from`
- Access checks reuse mailbox ACL (`has_access`)
- Same CSRF rules as other mutating session endpoints
- Worker token must stay secret; rotate with ingest token together
- Failed sends are audited (`send_failed`) for forensics

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| No Compose button | Set `WORKER_SEND_URL` and restart; check `/health` → `send_enabled` |
| 503 from API | `WORKER_SEND_URL` empty or Worker unreachable |
| `E_SEND_NOT_CONFIGURED` | Add `[[send_email]]` and redeploy Worker |
| `E_SENDER_NOT_VERIFIED` | Onboard domain in Email Sending; wait for DNS |
| 401 from Worker | `WORKER_INGEST_TOKEN` mismatch |
| Free plan only | Can send only to verified destinations; upgrade for arbitrary To |

---

## Local development tip

With `wrangler.dev`, Email Sending simulates delivery unless the binding has
`remote = true` (sends **real** mail). Prefer staging recipients while testing.
See [local sending docs](https://developers.cloudflare.com/email-service/local-development/sending/).
