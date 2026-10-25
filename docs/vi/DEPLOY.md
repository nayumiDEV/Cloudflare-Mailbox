# Triển khai OTP Inbox lên Koyeb

**Ngôn ngữ:** [English](../en/DEPLOY.md) · [Tiếng Việt](DEPLOY.md) · [中文](../zh/DEPLOY.md) · [Русский](../ru/DEPLOY.md)

Backend hỗ trợ cả **SQLite** (với Koyeb Persistent Volume) và **PostgreSQL**.

---

## 1. Cấu hình build / chạy

| Trường Koyeb | Giá trị |
| --- | --- |
| Builder | `Nixpacks` (hoặc `Buildpacks`) |
| Build command | *(để trống — tự nhận diện `requirements.txt`)* |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| Instance size | `nano` (512 MB RAM, 2 000 MB Disk) |
| Health check path | `/health` |
| Port | Koyeb tự đặt `$PORT` (mặc định 8000) |
| Scale / Instances | **Fixed: 1** (Min 1, Max 1) |

### Tại sao chỉ chạy đúng 1 instance?

SQLite lưu dữ liệu trong một file duy nhất trên Persistent Volume (`/data/otp_inbox.db`).
Nếu có 2 instance ghi đồng thời sẽ gây lỗi `database is locked` hoặc corrupt dữ liệu.
Luôn dùng **Fixed: 1**, tắt Sleep/Idle, và giữ `--workers 1`.

### Rolling deploy hiển thị 2 instance trong chớp nhoáng

Koyeb triển khai zero-downtime: container mới khởi động và vượt `/health` trước khi
container cũ bị tắt. Bạn sẽ thấy 2 instance khoảng 30–60 giây trong mỗi lần redeploy —
đây là bình thường.

### Tắt Sleep / Scale-to-zero

Nếu bật Sleep, khi không có traffic Koyeb đưa container vào trạng thái ngủ.
Khi email OTP đến, Cloudflare Worker gọi `/api/ingest` nhưng cold-start (15–40 giây)
khiến Worker timeout và email bị mất. **Luôn giữ container thức** với Fixed: 1 (min ≥ 1).

---

## 2. Dùng SQLite trên Koyeb

### Bước A — Tạo & gắn Persistent Volume (bắt buộc)

Nếu không có volume, mỗi lần redeploy sẽ xóa sạch database SQLite.

1. Trong Koyeb console, vào **Volumes** → **Create Volume**.
2. Đặt tên `otp-data` (1 GB là đủ để bắt đầu).
3. Trong cài đặt service → **Volumes**:
   - Volume: `otp-data`
   - Mount path: `/data`

### Bước B — Biến môi trường

```bash
# Đường dẫn file SQLite (trong volume vừa mount)
DATABASE_PATH=/data/otp_inbox.db

# Token chia sẻ với Cloudflare Worker (phải khớp WORKER_INGEST_TOKEN trong wrangler.toml)
WORKER_INGEST_TOKEN=<chuỗi_ngẫu_nhiên_bí_mật>        # đánh dấu Secret

# Tài khoản Admin khởi tạo lần đầu
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<mật_khẩu_admin>                       # đánh dấu Secret

# Bảo mật cookie (Koyeb dùng HTTPS, đặt = 1)
COOKIE_SECURE=1

# Tên ứng dụng & đăng ký
APP_NAME=OTP Inbox
REGISTRATION_ENABLED=1
REFERRAL_CODE_GENERATION=1

# Tối ưu tài nguyên cho gói nano (512 MB RAM / 2 000 MB Disk)
STORE_RAW_MIME=0              # không lưu MIME thô trùng lặp — tiết kiệm 60–80% đĩa mỗi email
STORE_ATTACHMENTS=1           # đặt 0 nếu chỉ dùng nhận mã OTP
MAX_ATTACHMENT_BYTES=1048576  # giới hạn 1 MB mỗi file đính kèm
MAX_ATTACHMENTS_TOTAL=2097152 # giới hạn 2 MB tổng mỗi email

# Tự động xóa thư cũ
AUTO_DELETE_ENABLED=1         # tiến trình ngầm chạy mỗi 1 tiếng
AUTO_DELETE_DAYS=7            # xóa thư cũ hơn N ngày
```

> **Không đặt** `DATABASE_URL` hay `POSTGRES_URL` khi dùng SQLite — nếu có,
> ứng dụng sẽ chuyển sang chế độ PostgreSQL.

---

## 2.1 PostgreSQL (tùy chọn)

Thêm một biến để chuyển từ SQLite sang PostgreSQL:

```bash
DATABASE_URL=postgres://user:password@host/dbname
```

Khi có `DATABASE_URL`, app bỏ qua `DATABASE_PATH` hoàn toàn.

**Cài đặt khuyến nghị bổ sung:**

```bash
APP_NAME=OTP Inbox
SESSION_DAYS=7
REGISTRATION_ENABLED=1
REFERRAL_CODE_GENERATION=1
REFERRAL_CODE_TTL_DAYS=0
REFERRAL_MAX_USES_DEFAULT=1
```

Hãy đánh dấu `DATABASE_URL` là Secret trong dashboard Koyeb để mật khẩu không
hiện ra trong deploy logs.

---

## 3. Lần deploy đầu tiên

Schema được tạo (hoặc migrate) tự động mỗi lần khởi động — redeploy là an toàn.

1. Đặt đầy đủ biến môi trường và deploy.
2. Kiểm tra log:
   ```
   [OTP Inbox] Admin created
   [OTP Inbox] username: admin
   [OTP Inbox] API key (shown once): sk-otp-...
   ```
   **Sao chép API key ngay — không bao giờ hiển thị lại.**
3. Đăng nhập tại URL Koyeb bằng `ADMIN_USERNAME` / `ADMIN_PASSWORD`.
4. Kiểm tra `/health` trả về `{"status":"ok",...}`.

---

## 4. Cloudflare Email Routing & Worker

### 4.1 Deploy Worker

```bash
cd worker

# Đặt secrets (phải khớp biến môi trường ở trên)
npx wrangler secret put WORKER_INGEST_TOKEN
npx wrangler secret put APP_INGEST_URL
# Giá trị APP_INGEST_URL: https://<your-app>.koyeb.app/api/ingest

npx wrangler deploy
```

`wrangler.toml` có cron trigger (`*/5 * * * *`) ping `/health` mỗi 5 phút
để tránh cold start.

### 4.2 Cấu hình Email Routing trên Cloudflare

1. Dashboard → tên miền → **Email** → **Email Routing** → **Routing rules**.
2. Bật Email Routing (Cloudflare tự thêm bản ghi MX/SPF).
3. **Catch-all rule**:
   - Status: **Active**
   - Action: **Send to Worker**
   - Destination: worker đã deploy (`otp-inbox-worker`)

---

## 5. Hệ thống mã giới thiệu

Đăng ký mặc định bị đóng. Ba điều kiện độc lập kiểm soát:

| Điều kiện | Vị trí | Mặc định | Tác dụng |
| --- | --- | --- | --- |
| `registration_enabled` | Switch Dashboard / env | **tắt** | Hiển thị form đăng ký |
| `referral_code_generation` | Switch Dashboard / env | **tắt** | Cho phép tạo mã |
| `can_generate_codes` | Cài đặt per-user | **tắt** | Quyền thực sự mỗi người dùng |

Mã được tạo ra **chỉ hiển thị một lần** và lưu dưới dạng SHA-256 digest.
Nếu mất, hãy tạo mã mới — mã cũ sẽ bị vô hiệu hóa tự động.

---

## 6. Phát triển local

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
# Đặt ADMIN_PASSWORD, WORKER_INGEST_TOKEN, COOKIE_SECURE=0

uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Nếu không có `DATABASE_URL`, app dùng `./data/otp_inbox.db` — không cần server database.

```bash
python -m pytest tests -q                     # backend
cd tests/js && npm install && npm test        # frontend (Node 18+)
```

---

Tài liệu chi tiết đầy đủ: [../DEPLOY.md](../DEPLOY.md)
