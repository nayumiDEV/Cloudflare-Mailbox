# OTP Inbox

Self-hosted **catch-all email inbox** with automatic OTP extraction, multi-user
access control, referral-gated registration, and a Developer API.

Incoming mail is received by a [Cloudflare Email Routing](https://developers.cloudflare.com/email-routing/)
Worker and stored by a FastAPI backend (SQLite or PostgreSQL). A Gmail-inspired
web UI lets you browse mailboxes, search messages, and manage routing rules.

[![CI](https://github.com/nayumiDEV/Cloudflare-Mailbox/actions/workflows/ci.yml/badge.svg)](https://github.com/nayumiDEV/Cloudflare-Mailbox/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## Features

- **Catch-all ingest** via Cloudflare Worker → `POST /api/ingest`
- **OTP extraction** from subject/body with false-positive scrubbing
- **Mailboxes & routing rules** (exact, prefix, domain, regex, catch-all)
- **Multi-user ACL** — owner, shared access, admin overrides
- **Referral registration** — closed by default; codes are hashed at rest
- **Developer API** — Bearer API keys for read/write without CSRF
- **Full-text search** (SQLite FTS5 / PostgreSQL `tsvector`)
- **Retention** — optional auto-delete of old messages
- **Zero build step** — static `app.css` / `app.js` with content-hash cache busting

## Architecture

```text
anything@your-domain.com
        │
        ▼
Cloudflare Email Routing (catch-all)
        │
        ▼
Cloudflare Worker  (worker/)
        │  POST /api/ingest + X-Worker-Token
        ▼
FastAPI  (app.py)  ·  SQLite or PostgreSQL
        │
        ▼
Web UI  (static/)  +  REST API  (/api/*)
```

## Quick start (local)

**Requirements:** Python 3.11+, Node 18+ (only for frontend tests)

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Unix:     source .venv/bin/activate

pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
# Set ADMIN_PASSWORD, WORKER_INGEST_TOKEN; COOKIE_SECURE=0 for plain HTTP

uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 and sign in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`.

On first boot the logs print a one-time admin API key (`sk-otp-…`) — copy it
immediately; it is never shown again.

```bash
python -m pytest tests -q
```

## Production deploy

See **[docs/DEPLOY.md](docs/DEPLOY.md)** for:

- Koyeb (SQLite volume or managed PostgreSQL)
- Cloudflare Email Routing + Worker secrets
- Resource / retention tuning
- Referral feature switches

Worker entrypoint: [`worker/`](worker/) (`wrangler.toml` + `worker.js`).

## Configuration

Copy [`.env.example`](.env.example). Important variables:

| Variable | Purpose |
| --- | --- |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Seeded admin (password only applied on first create) |
| `WORKER_INGEST_TOKEN` | Shared secret for `/api/ingest` |
| `DATABASE_URL` | PostgreSQL DSN — if unset, uses SQLite at `DATABASE_PATH` |
| `APP_NAME` | Brand string in UI + `/health` |
| `REGISTRATION_ENABLED` | Open the signup form (still needs a referral code) |
| `AUTO_DELETE_ENABLED` / `AUTO_DELETE_DAYS` | Background message retention |

## API

Full REST reference: **[docs/API.md](docs/API.md)**

Interactive Swagger UI: `GET /docs` on a running instance.

```bash
# Health
curl -s https://your-host/health

# Developer API
curl -s -H "Authorization: Bearer sk-otp-YOUR_KEY" https://your-host/api/dashboard
```

## Project layout

```text
app.py              FastAPI application
db.py               SQLite / PostgreSQL connection helpers
schema.py           Schema DDL (both backends)
static/             Web UI (index.html, app.js, app.css)
worker/             Cloudflare Email Worker
seed_demo.py        Optional local demo data
tests/              pytest + JS unit/e2e tests
docs/               Deploy & API documentation
koyeb.yaml          Example Koyeb service config
```

## Security

- Report vulnerabilities privately — see [SECURITY.md](SECURITY.md)
- Never commit `.env`, `data/*.db`, or Wrangler secrets
- Keep registration and referral generation off until you need them

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Please follow the
[Code of Conduct](CODE_OF_CONDUCT.md).

## License

[MIT](LICENSE)
