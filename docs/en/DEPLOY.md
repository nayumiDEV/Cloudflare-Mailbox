# Deploying OTP Inbox to Koyeb

**Languages:** [English](DEPLOY.md) · [Tiếng Việt](../vi/DEPLOY.md) · [中文](../zh/DEPLOY.md) · [Русский](../ru/DEPLOY.md)

The backend supports both **SQLite** (with a Koyeb Persistent Volume) and **PostgreSQL**.

---

## 1. Build / run settings

| Koyeb field | Value |
| --- | --- |
| Builder | `Nixpacks` (or `Buildpacks`) |
| Build command | *(leave empty — auto-detects `requirements.txt`)* |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| Instance size | `nano` (512 MB RAM, 2 000 MB Disk) |
| Health check path | `/health` |
| Port | Koyeb sets `$PORT` automatically (default 8000) |
| Scale / Instances | **Fixed: 1** (Min 1, Max 1) |

### Why must I run exactly 1 instance?

SQLite stores data in a single file on a Persistent Volume (`/data/otp_inbox.db`).
Two instances writing simultaneously cause `database is locked` errors or
data corruption. Always use **Fixed: 1**, disable Sleep/Idle, and keep `--workers 1`.

Even with PostgreSQL you should avoid multiple instances unless you replace the
in-process rate limiter with a shared one (e.g. Redis).

### Rolling deploys show 2 instances briefly

Koyeb performs zero-downtime rolling deployments: the new container starts and
passes `/health` before the old one is stopped. You will see two instances for
about 30–60 seconds during every redeploy — that is expected and harmless.

### Disable Sleep / Scale-to-zero

If Sleep is enabled and no traffic arrives, Koyeb puts the container to sleep.
When the next email arrives the Cloudflare Worker calls `/api/ingest`, but the
cold-start (15–40 s) causes a Worker timeout and the OTP email is dropped.
**Always keep the container awake** with Fixed: 1 (min ≥ 1).

---

## 2. SQLite on Koyeb

### Step A — Create and attach a Persistent Volume (required)

Without a volume every redeploy wipes the SQLite database.

1. In the Koyeb console go to **Volumes** → **Create Volume**.
2. Name it `otp-data` (1 GB is sufficient to start).
3. In your service settings → **Volumes**:
   - Volume: `otp-data`
   - Mount path: `/data`

### Step B — Environment variables

```bash
# Path to the SQLite file (inside the mounted volume)
DATABASE_PATH=/data/otp_inbox.db

# Shared secret with the Cloudflare Worker (matches WORKER_INGEST_TOKEN in wrangler.toml)
WORKER_INGEST_TOKEN=<random-secret>          # mark as Secret

# Admin account (applied only on first boot)
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<your-admin-password>         # mark as Secret

# Cookie security (Koyeb uses HTTPS, so set to 1)
COOKIE_SECURE=1

# App identity and registration
APP_NAME=OTP Inbox
REGISTRATION_ENABLED=1
REFERRAL_CODE_GENERATION=1

# Resource optimisation for nano (512 MB RAM / 2 000 MB Disk)
STORE_RAW_MIME=0             # skip duplicate base64 MIME blob — saves 60–80 % disk per email
STORE_ATTACHMENTS=1          # set to 0 if you only need OTP codes
MAX_ATTACHMENT_BYTES=1048576 # 1 MB per attachment
MAX_ATTACHMENTS_TOTAL=2097152 # 2 MB total per email

# Automatic message retention
AUTO_DELETE_ENABLED=1        # background worker runs every hour
AUTO_DELETE_DAYS=7           # delete messages older than N days
```

> **Do not set** `DATABASE_URL` or `POSTGRES_URL` when using SQLite — their
> presence switches the app to PostgreSQL mode.

---

## 2.1 PostgreSQL (optional)

Add a single variable to switch from SQLite to PostgreSQL:

```bash
DATABASE_URL=postgres://user:password@host/dbname
```

When `DATABASE_URL` is present the app ignores `DATABASE_PATH` entirely.

**Recommended additional settings:**

```bash
APP_NAME=OTP Inbox
SESSION_DAYS=7
REGISTRATION_ENABLED=1
REFERRAL_CODE_GENERATION=1
REFERRAL_CODE_TTL_DAYS=0
REFERRAL_MAX_USES_DEFAULT=1
```

**Optional tuning:**

```bash
MAX_EMAIL_BYTES=10485760
MAX_BODY_CHARS=200000
MAX_ATTACHMENTS_TOTAL=16777216
MAX_ATTACHMENT_BYTES=8388608
FTS_BODY_CHARS=4000
LOGIN_MAX_ATTEMPTS=10
LOGIN_WINDOW_SECONDS=900
REGISTER_MAX_ATTEMPTS=5
REGISTER_WINDOW_SECONDS=3600
```

Treat `DATABASE_URL` as a secret — mark it secret in the Koyeb dashboard so the
password does not appear in deploy logs.

---

## 3. First deploy

The schema is created (or migrated) automatically on every boot — redeploying is safe.

1. Set all environment variables and deploy.
2. Check the logs for:
   ```
   [OTP Inbox] Admin created
   [OTP Inbox] username: admin
   [OTP Inbox] API key (shown once): sk-otp-...
   ```
   **Copy the API key immediately — it is never shown again.**
3. Sign in at your Koyeb URL with `ADMIN_USERNAME` / `ADMIN_PASSWORD`.
4. Verify `/health` returns `{"status":"ok",...}`.

---

## 4. Cloudflare Email Routing & Worker

### 4.1 Deploy the Worker

```bash
cd worker

# Set secrets (must match your environment variables above)
npx wrangler secret put WORKER_INGEST_TOKEN
npx wrangler secret put APP_INGEST_URL
# Value for APP_INGEST_URL: https://<your-app>.koyeb.app/api/ingest

npx wrangler deploy
```

`wrangler.toml` includes a cron trigger (`*/5 * * * *`) that pings `/health`
every 5 minutes to prevent cold starts.

### 4.2 Configure Email Routing in Cloudflare

1. Dashboard → your domain → **Email** → **Email Routing** → **Routing rules**.
2. Enable Email Routing (Cloudflare will add MX/SPF records automatically).
3. **Catch-all rule**:
   - Status: **Active**
   - Action: **Send to Worker**
   - Destination: your deployed worker (`otp-inbox-worker`)

---

## 5. The referral system

Registration is closed by default. Three independent controls gate it:

| Control | Location | Default | Effect |
| --- | --- | --- | --- |
| `registration_enabled` | Dashboard switch / env | **off** | Show the signup form |
| `referral_code_generation` | Dashboard switch / env | **off** | Allow users to generate codes |
| `can_generate_codes` | Per-user in the Users table | **off** | The actual per-user grant |

A generated code is shown **once** and stored only as a SHA-256 digest.
If lost, generate a new one — the old code is automatically retired.

---

## 6. Local development

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
# Set ADMIN_PASSWORD, WORKER_INGEST_TOKEN, COOKIE_SECURE=0

uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Without `DATABASE_URL` the app uses `./data/otp_inbox.db` — no database server needed.

```bash
python -m pytest tests -q                     # backend
cd tests/js && npm install && npm test        # frontend (Node 18+)
```

---

## 7. Operational notes

**Migrating SQLite → PostgreSQL.** There is no automatic migration. Start with
a fresh PostgreSQL database (the schema is created on boot). If you need your
existing rows, export each table and load with `COPY` in dependency order:
`users`, `mailboxes`, `routing_rules`, `mailbox_access`, `messages`,
`attachments`, `message_reads`, `notifications`, `audit_logs`,
`referral_codes`, `registrations`, `app_settings`. Reset identity sequences
after import:

```sql
SELECT setval(pg_get_serial_sequence('messages','id'),
              COALESCE(MAX(id), 1)) FROM messages;
```

**Static assets.** No build step. `app.css` / `app.js` URLs include a content
hash, so browsers automatically pick up changes after a deploy.

---

Full canonical reference (Vietnamese/English): [../DEPLOY.md](../DEPLOY.md)
