# Complete setup guide

**Languages:** [English](SETUP.md) · [Tiếng Việt](vi/SETUP.md) · [中文](zh/SETUP.md) · [Русский](ru/SETUP.md)

This is the full, step-by-step guide to run **OTP Inbox** (Cloudflare Mailbox) from a blank machine to a production catch-all OTP inbox.

Related docs:

| Doc | Purpose |
| --- | --- |
| [DEPLOY](en/DEPLOY.md) | PaaS / Koyeb tuning, retention, keep-alive |
| [API](en/API.md) | REST overview (full reference: [API.md](API.md)) |
| [SECURITY.md](../SECURITY.md) | Vulnerability reporting & hardening |

---

## 0. What you are building

```text
Internet mail  →  Cloudflare Email Routing (catch-all)
                         │
                         ▼
               Cloudflare Worker  (worker/)
                         │  POST /api/ingest + X-Worker-Token
                         ▼
               FastAPI app  (app/main.py)  +  SQLite or PostgreSQL
                         │
                         ▼
               Web UI  (static/)  +  Developer API
```

You need:

1. A domain on Cloudflare (for Email Routing)
2. Somewhere to run the API (laptop for test, or Koyeb / any Linux host for production)
3. Python 3.11+ on that host

---

## 1. Prerequisites

### 1.1 Software

| Tool | Version | Why |
| --- | --- | --- |
| Python | **3.11+** (3.12 recommended) | FastAPI backend |
| pip | bundled with Python | Install dependencies |
| Git | any recent | Clone the repo |
| Node.js | **18+** (optional) | Frontend unit tests only |
| Cloudflare account | free tier is enough | Email Routing + Worker |
| Wrangler CLI | via `npx wrangler` | Deploy the Worker |

Check:

```bash
python --version    # >= 3.11
git --version
node --version      # optional
```

### 1.2 Accounts & secrets you will create

Keep these in a password manager — never commit them:

| Secret | Used by |
| --- | --- |
| `ADMIN_PASSWORD` | First admin login |
| `WORKER_INGEST_TOKEN` | Shared between Worker ↔ API |
| Admin API key `sk-otp-…` | Printed **once** on first boot |
| Cloudflare API / Wrangler login | Deploy Worker |
| `DATABASE_URL` (optional) | Managed Postgres |

Generate a strong token:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

---

## 2. Get the code

```bash
git clone https://github.com/nayumiDEV/Cloudflare-Mailbox.git
cd Cloudflare-Mailbox
```

### 2.1 Repository layout

```text
Cloudflare-Mailbox/
├── app/                 # Python package (FastAPI)
│   ├── main.py          # HTTP API + business logic  →  uvicorn app.main:app
│   ├── db.py            # SQLite / PostgreSQL adapter
│   ├── schema.py        # DDL for both backends
│   └── seed.py          # Optional demo data
├── static/              # Web UI (no build step)
│   ├── index.html
│   ├── app.js / app.css
│   ├── i18n.js
│   └── locales/         # en, vi, zh, ru
├── worker/              # Cloudflare Email Worker
│   ├── worker.js
│   └── wrangler.toml
├── tests/               # pytest + JS unit tests
├── docs/                # This documentation
├── deploy/koyeb.yaml    # Example Koyeb service config
├── requirements.txt
├── requirements-dev.txt
└── .env.example         # Copy → .env
```

---

## 3. Local development (SQLite — zero config)

### 3.1 Create a virtualenv

**Windows (PowerShell):**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt
```

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt
```

### 3.2 Configure environment

```bash
# Windows
copy .env.example .env

# macOS / Linux
cp .env.example .env
```

Edit `.env` — **minimum for local HTTP:**

```bash
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-me-to-at-least-10-chars
WORKER_INGEST_TOKEN=paste-the-token-urlsafe-48-output-here

# SQLite file (created automatically)
DATABASE_PATH=./data/otp_inbox.db

# Required for http://127.0.0.1 (browsers reject Secure cookies on plain HTTP)
COOKIE_SECURE=0

APP_NAME=OTP Inbox
REGISTRATION_ENABLED=0
REFERRAL_CODE_GENERATION=0
```

Every variable is documented in `.env.example`. Do not commit `.env`.

### 3.3 Start the API

Always run commands from the **repository root**:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000

On a **fresh** database the server log prints:

```text
[OTP Inbox] Admin created
[OTP Inbox] username: admin
[OTP Inbox] API key (shown once): sk-otp-................
```

**Copy the API key immediately** — it is never shown again. You can rotate it later from Account settings in the UI.

Sign in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`.

### 3.4 Health check

```bash
curl -s http://127.0.0.1:8000/health
```

Expected shape:

```json
{
  "status": "ok",
  "app": "OTP Inbox",
  "version": "…",
  "search": "fts",
  "registration": false,
  "code_generation": false
}
```

### 3.5 Optional demo data

```bash
python -m app.seed
```

Creates sample users / mailboxes / OTP messages (see script output for passwords).

### 3.6 Run tests

```bash
# Backend
python -m pytest tests -q

# Optional: PostgreSQL SQL dialect via in-process shim (no real Postgres needed)
OTP_TEST_BACKEND=pg python -m pytest tests -q

# Frontend unit tests
cd tests/js && npm install && npm run test:unit
```

---

## 4. Production deploy (API)

You can host the API on any Linux container/VPS. Below: **Koyeb** (documented thoroughly) and a generic Docker-less systemd sketch.

### 4.1 Koyeb — recommended settings

| Field | Value |
| --- | --- |
| Builder | Nixpacks / Buildpacks |
| Build command | *(empty — detects `requirements.txt`)* |
| **Start command** | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| Health check | `GET /health` |
| Instances | **Fixed: 1** (especially with SQLite) |
| Idle / sleep | **Off** — cold starts drop OTP mail |

Example service file: [`deploy/koyeb.yaml`](../deploy/koyeb.yaml).

#### 4.1.1 SQLite + persistent volume

1. Create a Volume (e.g. `otp-data`, 1 GB+)
2. Mount it at `/data`
3. Env:

```bash
DATABASE_PATH=/data/otp_inbox.db
# Do NOT set DATABASE_URL
WORKER_INGEST_TOKEN=<secret>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<secret>
COOKIE_SECURE=1
APP_NAME=OTP Inbox
STORE_RAW_MIME=0
AUTO_DELETE_ENABLED=1
AUTO_DELETE_DAYS=7
```

#### 4.1.2 Managed PostgreSQL

```bash
DATABASE_URL=postgres://user:pass@host:port/dbname
WORKER_INGEST_TOKEN=<secret>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<secret>
COOKIE_SECURE=1
```

When `DATABASE_URL` is set, `DATABASE_PATH` is ignored. Schema is created on boot.

### 4.2 Generic VPS (sketch)

```bash
sudo useradd -r -m -d /opt/otp-inbox otp
sudo -u otp -i
cd /opt/otp-inbox
git clone https://github.com/nayumiDEV/Cloudflare-Mailbox.git .
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit secrets; COOKIE_SECURE=1 behind HTTPS
```

systemd unit example:

```ini
[Unit]
Description=OTP Inbox
After=network.target

[Service]
User=otp
WorkingDirectory=/opt/otp-inbox
EnvironmentFile=/opt/otp-inbox/.env
ExecStart=/opt/otp-inbox/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=always

[Install]
WantedBy=multi-user.target
```

Put HTTPS in front (Caddy / nginx / Cloudflare Tunnel).

---

## 5. Cloudflare Email Routing + Worker

### 5.1 Deploy the Worker

```bash
cd worker
npx wrangler login
npx wrangler secret put WORKER_INGEST_TOKEN
# paste the SAME value as the API's WORKER_INGEST_TOKEN

npx wrangler secret put APP_INGEST_URL
# paste: https://YOUR-API-HOST/api/ingest

npx wrangler deploy
```

Confirm `worker/wrangler.toml` name (`otp-inbox-worker`) and cron (`*/5 * * * *` keep-alive ping to `/health`).

Manual ping test: open `https://<worker>.<account>.workers.dev/ping`

### 5.2 Enable Email Routing on your domain

In Cloudflare Dashboard → your domain → **Email** → **Email Routing**:

1. Enable Email Routing (Cloudflare sets MX / SPF)
2. **Routing rules** → **Catch-all**:
   - Status: Active
   - Action: **Send to Worker**
   - Destination: `otp-inbox-worker` (or the name you deployed)
3. Optional: add specific address rules as well

### 5.3 Domains inside the app

On first boot with an empty DB the app seeds `example.com` (or `DEFAULT_MAIL_DOMAIN`).

1. Sign in as admin → **Domains**
2. Add your real domain(s), set access level (`public` / `approval_only` / `vip`)
3. Create mailboxes or rely on **Routing rules** (prefix / domain / regex / catch-all) to auto-assign new addresses

### 5.4 End-to-end mail test

1. Send an email to `anything@your-domain.com` with a body like `Your code is 482193`
2. Watch Worker logs: `npx wrangler tail`
3. Watch API logs for ingest
4. Open the Web UI → Inbox — OTP should be extracted

If Worker returns reject / timeout: API is down, sleeping, or `WORKER_INGEST_TOKEN` / `APP_INGEST_URL` mismatch.

---

## 6. First-admin checklist

- [ ] Copied one-time `sk-otp-…` API key
- [ ] Changed admin password in **Account** (env password only applies on first create)
- [ ] Added real domain(s) under **Domains**
- [ ] Created at least one mailbox or routing rule
- [ ] Left registration **off** until you intentionally open referral signup
- [ ] Verified `/health` returns `"status":"ok"`
- [ ] Language switcher works (EN / VI / 中文 / RU)

---

## 7. Developer API (quick start)

```bash
export API_KEY='sk-otp-...'
curl -s -H "Authorization: Bearer $API_KEY" https://YOUR-HOST/api/dashboard
```

Full reference: [docs/API.md](API.md). Interactive Swagger: `https://YOUR-HOST/docs`.

Session cookie + CSRF is for the browser UI only. Prefer Bearer keys for integrations.

---

## 8. Environment variable reference

See [`.env.example`](../.env.example) for the complete list with comments. Critical ones:

| Variable | Required | Notes |
| --- | --- | --- |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | yes (first boot) | Password min 10 chars; only applied when admin is created |
| `WORKER_INGEST_TOKEN` | yes | Must match Worker secret |
| `DATABASE_URL` | one of | Postgres DSN |
| `DATABASE_PATH` | one of | SQLite path (default `./data/otp_inbox.db`) |
| `COOKIE_SECURE` | yes | `0` local HTTP, `1` production HTTPS |
| `APP_NAME` | no | Brand in UI / `/health` |
| `DEFAULT_MAIL_DOMAIN` | no | Seeded only on empty DB |
| `REGISTRATION_ENABLED` | no | Default off |
| `REFERRAL_CODE_GENERATION` | no | Default off |
| `AUTO_DELETE_ENABLED` / `AUTO_DELETE_DAYS` | no | Background retention |
| `STORE_RAW_MIME` | no | `0` saves disk (recommended on small volumes) |

---

## 9. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Cookie not stored locally | `COOKIE_SECURE=1` on HTTP | Set `COOKIE_SECURE=0` for local |
| Worker rejects mail | Token / URL wrong or API asleep | Match secrets; disable PaaS sleep; check `/ping` |
| `database is locked` | Multiple instances on one SQLite file | Scale to **1** worker/instance |
| Empty UI after deploy | CDN cached old `index.html` | Hard refresh; asset hash updates with static files |
| Admin password change in `.env` does nothing | Account already exists | Use in-app change password |
| Registration form missing | Feature off | Enable in admin UI or set `REGISTRATION_ENABLED=1` then restart (first boot) / toggle in dashboard |
| ImportError `app.main` | Wrong working directory | Run uvicorn from repo root |
| Tests touch real DB | Env not isolated | Suite uses temp DB; don't set `DATABASE_PATH` to production in the same shell |

---

## 10. Updating

```bash
git pull
source .venv/bin/activate   # or Windows equivalent
pip install -r requirements.txt
# restart uvicorn / redeploy PaaS
cd worker && npx wrangler deploy   # if worker/ changed
```

Schema migrations are additive and run on boot — redeploy is safe.

---

## 11. Uninstall / wipe local data

```bash
# Stop the server, then:
rm -rf data/ .venv/
# Windows: Remove-Item -Recurse -Force data, .venv
```

Production: delete the volume / database and the Cloudflare Worker as needed.
