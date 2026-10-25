# Hướng dẫn cài đặt đầy đủ

**Ngôn ngữ:** [English](../SETUP.md) · [Tiếng Việt](SETUP.md) · [中文](../zh/SETUP.md) · [Русский](../ru/SETUP.md)

Hướng dẫn từng bước đầy đủ để chạy **OTP Inbox** (Cloudflare Mailbox) từ một máy tính trống đến hộp thư OTP catch-all sẵn sàng cho production.

Các tài liệu liên quan:

| Tài liệu | Mục đích |
| --- | --- |
| [DEPLOY](DEPLOY.md) | Tinh chỉnh PaaS / Koyeb, lưu trữ có thời hạn, keep-alive |
| [API](API.md) | Tổng quan REST (tài liệu đầy đủ: [API.md](../API.md)) |
| [SECURITY.md](../../SECURITY.md) | Báo cáo lỗ hổng & hướng dẫn bảo mật |

---

## 0. Bạn đang xây dựng gì

```text
Internet mail  →  Cloudflare Email Routing (catch-all)
                         │
                         ▼
               Cloudflare Worker  (worker/)
                         │  POST /api/ingest + X-Worker-Token
                         ▼
               FastAPI app  (app/main.py)  +  SQLite hoặc PostgreSQL
                         │
                         ▼
               Web UI  (static/)  +  Developer API
```

Bạn cần:

1. Một tên miền trên Cloudflare (để dùng Email Routing)
2. Nơi để chạy API (máy tính cá nhân để test, hoặc Koyeb / bất kỳ máy chủ Linux nào để production)
3. Python 3.11+ trên máy chủ đó

---

## 1. Yêu cầu trước khi bắt đầu

### 1.1 Phần mềm

| Công cụ | Phiên bản | Lý do |
| --- | --- | --- |
| Python | **3.11+** (khuyến nghị 3.12) | Backend FastAPI |
| pip | đi kèm Python | Cài đặt dependencies |
| Git | bất kỳ phiên bản gần đây | Clone repo |
| Node.js | **18+** (tùy chọn) | Chỉ cho frontend unit tests |
| Tài khoản Cloudflare | gói miễn phí là đủ | Email Routing + Worker |
| Wrangler CLI | qua `npx wrangler` | Deploy Worker |

Kiểm tra:

```bash
python --version    # >= 3.11
git --version
node --version      # tùy chọn
```

### 1.2 Tài khoản & secrets bạn cần tạo

Lưu những thông tin này trong trình quản lý mật khẩu — không bao giờ commit chúng:

| Secret | Dùng bởi |
| --- | --- |
| `ADMIN_PASSWORD` | Đăng nhập admin lần đầu |
| `WORKER_INGEST_TOKEN` | Chia sẻ giữa Worker ↔ API |
| Admin API key `sk-otp-…` | In ra **một lần** khi khởi động lần đầu |
| Cloudflare API / Wrangler login | Deploy Worker |
| `DATABASE_URL` (tùy chọn) | Managed Postgres |

Tạo token mạnh:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

---

## 2. Lấy mã nguồn

```bash
git clone https://github.com/nayumiDEV/Cloudflare-Mailbox.git
cd Cloudflare-Mailbox
```

### 2.1 Cấu trúc repository

```text
Cloudflare-Mailbox/
├── app/                 # Python package (FastAPI)
│   ├── main.py          # HTTP API + business logic  →  uvicorn app.main:app
│   ├── db.py            # SQLite / PostgreSQL adapter
│   ├── schema.py        # DDL cho cả hai backends
│   └── seed.py          # Dữ liệu demo tùy chọn
├── static/              # Web UI (không cần bước build)
│   ├── index.html
│   ├── app.js / app.css
│   ├── i18n.js
│   └── locales/         # en, vi, zh, ru
├── worker/              # Cloudflare Email Worker
│   ├── worker.js
│   └── wrangler.toml
├── tests/               # pytest + JS unit tests
├── docs/                # Tài liệu này
├── deploy/koyeb.yaml    # Cấu hình Koyeb mẫu
├── requirements.txt
├── requirements-dev.txt
└── .env.example         # Copy → .env
```

---

## 3. Phát triển local (SQLite — không cần cấu hình)

### 3.1 Tạo virtualenv

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

### 3.2 Cấu hình môi trường

```bash
# Windows
copy .env.example .env

# macOS / Linux
cp .env.example .env
```

Chỉnh sửa `.env` — **tối thiểu cho HTTP local:**

```bash
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-me-to-at-least-10-chars
WORKER_INGEST_TOKEN=paste-the-token-urlsafe-48-output-here

# SQLite file (tự động tạo)
DATABASE_PATH=./data/otp_inbox.db

# Bắt buộc cho http://127.0.0.1 (trình duyệt từ chối Secure cookies trên HTTP thông thường)
COOKIE_SECURE=0

APP_NAME=OTP Inbox
REGISTRATION_ENABLED=0
REFERRAL_CODE_GENERATION=0
```

Mọi biến đều được ghi chú trong `.env.example`. Không commit `.env`.

### 3.3 Khởi động API

Luôn chạy lệnh từ **thư mục gốc repository**:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Mở http://127.0.0.1:8000

Trên **database mới**, log máy chủ sẽ in ra:

```text
[OTP Inbox] Admin created
[OTP Inbox] username: admin
[OTP Inbox] API key (shown once): sk-otp-................
```

**Sao chép API key ngay** — nó không bao giờ được hiển thị lại. Bạn có thể rotate nó sau từ phần Cài đặt tài khoản trong giao diện.

Đăng nhập bằng `ADMIN_USERNAME` / `ADMIN_PASSWORD`.

### 3.4 Kiểm tra health

```bash
curl -s http://127.0.0.1:8000/health
```

Kết quả mong đợi:

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

### 3.5 Dữ liệu demo tùy chọn

```bash
python -m app.seed
```

Tạo các user / mailbox / tin nhắn OTP mẫu (xem output script để biết mật khẩu).

### 3.6 Chạy tests

```bash
# Backend
python -m pytest tests -q

# Tùy chọn: PostgreSQL SQL dialect qua in-process shim (không cần Postgres thật)
OTP_TEST_BACKEND=pg python -m pytest tests -q

# Frontend unit tests
cd tests/js && npm install && npm run test:unit
```

---

## 4. Deploy production (API)

Bạn có thể host API trên bất kỳ container Linux / VPS nào. Dưới đây: **Koyeb** (được ghi chép đầy đủ) và phác thảo systemd generic không dùng Docker.

### 4.1 Koyeb — cài đặt khuyến nghị

| Trường | Giá trị |
| --- | --- |
| Builder | Nixpacks / Buildpacks |
| Build command | *(để trống — tự nhận diện `requirements.txt`)* |
| **Start command** | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| Health check | `GET /health` |
| Instances | **Fixed: 1** (đặc biệt khi dùng SQLite) |
| Idle / sleep | **Tắt** — cold starts làm mất mail OTP |

File cấu hình mẫu: [`deploy/koyeb.yaml`](../../deploy/koyeb.yaml).

#### 4.1.1 SQLite + persistent volume

1. Tạo một Volume (ví dụ `otp-data`, 1 GB+)
2. Mount tại `/data`
3. Env:

```bash
DATABASE_PATH=/data/otp_inbox.db
# KHÔNG đặt DATABASE_URL
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

Khi `DATABASE_URL` được đặt, `DATABASE_PATH` bị bỏ qua. Schema được tạo khi khởi động.

### 4.2 VPS generic (phác thảo)

```bash
sudo useradd -r -m -d /opt/otp-inbox otp
sudo -u otp -i
cd /opt/otp-inbox
git clone https://github.com/nayumiDEV/Cloudflare-Mailbox.git .
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # chỉnh secrets; COOKIE_SECURE=1 đằng sau HTTPS
```

Ví dụ unit systemd:

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

Đặt HTTPS phía trước (Caddy / nginx / Cloudflare Tunnel).

---

## 5. Cloudflare Email Routing + Worker

### 5.1 Deploy Worker

```bash
cd worker
npx wrangler login
npx wrangler secret put WORKER_INGEST_TOKEN
# paste CÙNG giá trị với WORKER_INGEST_TOKEN của API

npx wrangler secret put APP_INGEST_URL
# paste: https://YOUR-API-HOST/api/ingest

npx wrangler deploy
```

Xác nhận tên trong `worker/wrangler.toml` (`otp-inbox-worker`) và cron (`*/5 * * * *` keep-alive ping đến `/health`).

Kiểm tra thủ công: mở `https://<worker>.<account>.workers.dev/ping`

### 5.2 Bật Email Routing trên tên miền

Trong Cloudflare Dashboard → tên miền → **Email** → **Email Routing**:

1. Bật Email Routing (Cloudflare tự cấu hình MX / SPF)
2. **Routing rules** → **Catch-all**:
   - Status: Active
   - Action: **Send to Worker**
   - Destination: `otp-inbox-worker` (hoặc tên bạn đã deploy)
3. Tùy chọn: thêm các quy tắc địa chỉ cụ thể

### 5.3 Tên miền trong app

Khi khởi động lần đầu với DB trống, app seeded `example.com` (hoặc `DEFAULT_MAIL_DOMAIN`).

1. Đăng nhập admin → **Domains**
2. Thêm tên miền thực của bạn, đặt mức truy cập (`public` / `approval_only` / `vip`)
3. Tạo mailbox hoặc dựa vào **Routing rules** (prefix / domain / regex / catch-all) để tự động gán địa chỉ mới

### 5.4 Test mail end-to-end

1. Gửi email đến `anything@your-domain.com` với nội dung như `Your code is 482193`
2. Xem Worker logs: `npx wrangler tail`
3. Xem API logs để kiểm tra ingest
4. Mở Web UI → Inbox — OTP phải được trích xuất

Nếu Worker trả về reject / timeout: API đang tắt, ngủ, hoặc `WORKER_INGEST_TOKEN` / `APP_INGEST_URL` không khớp.

---

## 6. Checklist admin lần đầu

- [ ] Đã sao chép API key một lần `sk-otp-…`
- [ ] Đã đổi mật khẩu admin trong **Account** (mật khẩu env chỉ áp dụng khi tạo lần đầu)
- [ ] Đã thêm tên miền thực trong **Domains**
- [ ] Đã tạo ít nhất một mailbox hoặc routing rule
- [ ] Giữ registration **tắt** cho đến khi bạn cố ý mở đăng ký có mã giới thiệu
- [ ] Đã xác minh `/health` trả về `"status":"ok"`
- [ ] Language switcher hoạt động (EN / VI / 中文 / RU)

---

## 7. Developer API (khởi đầu nhanh)

```bash
export API_KEY='sk-otp-...'
curl -s -H "Authorization: Bearer $API_KEY" https://YOUR-HOST/api/dashboard
```

Tài liệu đầy đủ: [docs/API.md](../API.md). Interactive Swagger: `https://YOUR-HOST/docs`.

Session cookie + CSRF chỉ dành cho giao diện trình duyệt. Ưu tiên dùng Bearer keys cho tích hợp.

---

## 8. Tham chiếu biến môi trường

Xem [`.env.example`](../../.env.example) để có danh sách đầy đủ với chú thích. Những biến quan trọng:

| Biến | Bắt buộc | Ghi chú |
| --- | --- | --- |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | có (lần khởi động đầu) | Mật khẩu tối thiểu 10 ký tự; chỉ áp dụng khi admin được tạo |
| `WORKER_INGEST_TOKEN` | có | Phải khớp Worker secret |
| `DATABASE_URL` | một trong hai | Postgres DSN |
| `DATABASE_PATH` | một trong hai | Đường dẫn SQLite (mặc định `./data/otp_inbox.db`) |
| `COOKIE_SECURE` | có | `0` HTTP local, `1` HTTPS production |
| `APP_NAME` | không | Thương hiệu trong UI / `/health` |
| `DEFAULT_MAIL_DOMAIN` | không | Chỉ seed khi DB trống |
| `REGISTRATION_ENABLED` | không | Mặc định tắt |
| `REFERRAL_CODE_GENERATION` | không | Mặc định tắt |
| `AUTO_DELETE_ENABLED` / `AUTO_DELETE_DAYS` | không | Lưu trữ nền |
| `STORE_RAW_MIME` | không | `0` tiết kiệm đĩa (khuyến nghị trên volume nhỏ) |

---

## 9. Khắc phục sự cố

| Triệu chứng | Nguyên nhân | Cách sửa |
| --- | --- | --- |
| Cookie không lưu trữ local | `COOKIE_SECURE=1` trên HTTP | Đặt `COOKIE_SECURE=0` cho local |
| Worker từ chối mail | Token / URL sai hoặc API ngủ | Khớp secrets; tắt PaaS sleep; kiểm tra `/ping` |
| `database is locked` | Nhiều instances trên cùng một file SQLite | Scale xuống **1** worker/instance |
| UI trống sau deploy | CDN cache `index.html` cũ | Hard refresh; asset hash cập nhật theo static files |
| Đổi mật khẩu admin trong `.env` không có tác dụng | Tài khoản đã tồn tại | Dùng chức năng đổi mật khẩu trong app |
| Form đăng ký không hiện | Tính năng tắt | Bật trong admin UI hoặc đặt `REGISTRATION_ENABLED=1` rồi restart (lần khởi động đầu) / toggle trong dashboard |
| ImportError `app.main` | Thư mục làm việc sai | Chạy uvicorn từ thư mục gốc repo |
| Tests chạm vào DB thật | Env chưa được cô lập | Suite dùng DB tạm; đừng đặt `DATABASE_PATH` vào production trong cùng shell |

---

## 10. Cập nhật

```bash
git pull
source .venv/bin/activate   # hoặc tương đương Windows
pip install -r requirements.txt
# restart uvicorn / redeploy PaaS
cd worker && npx wrangler deploy   # nếu worker/ thay đổi
```

Schema migrations có tính additive và chạy khi khởi động — redeploy là an toàn.

---

## 11. Gỡ cài đặt / xóa dữ liệu local

```bash
# Dừng server, sau đó:
rm -rf data/ .venv/
# Windows: Remove-Item -Recurse -Force data, .venv
```

Production: xóa volume / database và Cloudflare Worker theo nhu cầu.
