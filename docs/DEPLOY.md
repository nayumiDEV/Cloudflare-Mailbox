# Production deploy

**Languages:** [English](en/DEPLOY.md) · [Tiếng Việt](vi/DEPLOY.md) · [中文](zh/DEPLOY.md) · [Русский](ru/DEPLOY.md)

Run the FastAPI app on any Linux host, container, or PaaS. The backend supports
**SQLite** (with a persistent volume) and **PostgreSQL**.

---

## 1. Process settings

| Setting | Recommended value |
| --- | --- |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| Health check | `GET /health` |
| Instances / replicas | **1** when using SQLite |
| Sleep / scale-to-zero | **Off** for mail ingest (cold starts drop OTP mail) |

### Why one instance with SQLite?

SQLite is a single file. Two writers at once cause `database is locked` or
corruption. Keep **one** process (`--workers 1`) and one replica. With PostgreSQL
you may scale out later, but the in-process rate limiter is still per-instance.

### Keep the process awake

If the platform sleeps idle containers, Cloudflare Worker `/api/ingest` will
timeout on cold start and OTP mail is lost. Disable sleep, or rely on the
Worker cron `/ping` keep-alive (still prefer always-on).

---

## 2. SQLite + persistent volume

Ephemeral container disks wipe the database on redeploy. Mount a volume and point
`DATABASE_PATH` at it:

```bash
DATABASE_PATH=/data/otp_inbox.db
WORKER_INGEST_TOKEN=<secret>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<secret>
COOKIE_SECURE=1
APP_NAME=OTP Inbox

# Optional: soft budget for Admin → System (% used bar)
DISK_LIMIT_MB=2048

# Retention / size knobs (tune to your disk)
STORE_RAW_MIME=0
AUTO_DELETE_ENABLED=1
AUTO_DELETE_DAYS=7
```

---

## 3. Managed PostgreSQL

```bash
DATABASE_URL=postgres://user:pass@host:port/dbname
WORKER_INGEST_TOKEN=<secret>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<secret>
COOKIE_SECURE=1
```

When `DATABASE_URL` is set, `DATABASE_PATH` is ignored. Schema is created on boot.

---

## 4. Generic VPS (systemd sketch)

```bash
sudo useradd -r -m -d /opt/otp-inbox otp
sudo -u otp -i
cd /opt/otp-inbox
git clone https://github.com/nayumiDEV/Cloudflare-Mailbox.git .
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit secrets; COOKIE_SECURE=1 behind HTTPS
```

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

Terminate TLS with Caddy / nginx / Cloudflare Tunnel.

---

## 5. Cloudflare Worker

```bash
cd worker
npx wrangler secret put WORKER_INGEST_TOKEN
npx wrangler secret put APP_INGEST_URL
# APP_INGEST_URL = https://YOUR-API-HOST/api/ingest
npx wrangler deploy
```

Catch-all Email Routing → **Send to Worker**. For outbound compose see
[en/SEND.md](en/SEND.md).

---

## 6. Disk & memory tuning

Set limits to match **your** host — there is no built-in vendor quota.

| Variable | Purpose |
| --- | --- |
| `DISK_LIMIT_MB` | Soft budget for Admin → System progress bar (`0` = unset) |
| `STORE_RAW_MIME` | `0` skips storing raw MIME (saves a lot of disk) |
| `AUTO_DELETE_*` | Retention policy |
| `MAX_EMAIL_BYTES` / `MAX_ATTACHMENT_*` | Cap inbound message size |
| `SQLITE_CACHE_SIZE_KB` | SQLite page cache |

Full setup walkthrough: [SETUP.md](SETUP.md).
