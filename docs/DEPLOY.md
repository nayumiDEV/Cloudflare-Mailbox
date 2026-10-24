# Deploying to Koyeb

Backend hỗ trợ cả **SQLite** (với Koyeb Persistent Volume) và **PostgreSQL**.

---

## 1. Build / run settings

| Koyeb field | Value |
| --- | --- |
| Builder | `Nixpacks` (hoặc `Buildpacks`) |
| Build command | *(để trống — tự nhận diện `requirements.txt`)* |
| Start command | `uvicorn app:app --host 0.0.0.0 --port $PORT --workers 1` |
| Instance size | `nano` (512MB RAM, 2000MB Disk) |
| Health check path | `/health` |
| Port | Koyeb tự set `$PORT` (mặc định 8000) |
| Scale / Instances | **Fixed: 1** (Min 1, Max 1) |

---

## 1.1. Tại sao Koyeb chạy 2 instances / workers và cách khắc phục triệt để

Nếu bạn thấy Koyeb hiển thị 2 instances hoặc 2 workers đang chạy, nguyên nhân đến từ một trong ba trường hợp sau:

1. **Cấu hình Autoscaling mặc định của Koyeb:**
   - Khi tạo Web Service mới trên Koyeb qua giao diện Web UI, Koyeb thường đặt mặc định **Autoscaling: Min 1, Max 2** (hoặc Fixed: 2).
   - **Cách sửa:** Vào Service Settings -> **Scaling** -> Chọn **Fixed** -> Điền **1** (hoặc nếu để Autoscaling thì đặt **Min 1, Max 1**). File cấu hình `koyeb.yaml` đã được thiết lập sẵn `min: 1, max: 1`.

2. **Cơ chế Rolling Deployment (Không Downtime) của Koyeb:**
   - Mỗi khi bạn git push code mới lên hoặc Koyeb tiến hành Redeploy, Koyeb sẽ khởi động container mới (Instance 2).
   - Trong thời gian container mới đang boot và chờ vượt qua kiểm tra `/health`, container cũ (Instance 1) **vẫn tiếp tục chạy** để hứng traffic tạm thời.
   - Khi container mới báo healthy (status Healthy / 200 OK), Koyeb mới tắt và xoá container cũ. Do đó, trong khoảng 30s - 1 phút khi deploy, bạn sẽ thấy 2 instances hiển thị song song. Đây là cơ chế bình thường của PaaS.

3. **Tạo nhầm Worker Service bên cạnh Web Service:**
   - Nếu trong Koyeb App bạn vô tình tạo 1 Web Service (chạy FastAPI) và bấm tạo thêm 1 Worker Service riêng, Koyeb sẽ chạy 2 máy chủ độc lập liên tục. Chỉ cần giữ 1 Web Service duy nhất.

4. **Tính năng Idle Period / Sleep When Idle (Scale to Zero):**
   - Koyeb có thiết lập: *"Idle period: Number of seconds to wait before sleeping the instance if it receives no traffic"*.
   - **Tác hại nghiêm trọng nếu bật:**
     - Khi không có người truy cập, Koyeb đưa container vào trạng thái **Sleep (0 instance)**.
     - Khi có email gửi đến, Cloudflare Worker gọi webhook `/api/ingest`. Koyeb phải "đánh thức" (Cold Start) container dậy, mất từ **15 đến 40 giây**.
     - Trong lúc đang thức dậy, Cloudflare Worker chờ quá lâu sẽ bị **Timeout** và từ chối nhận email (báo lỗi `OTP inbox temporarily unavailable`), làm bạn bị **mất/chậm trễ mã OTP**!
     - Khi container đang boot dậy, nếu có thêm request đồng thời (ví dụ bạn vừa mở web xem), Koyeb autoscaler có thể khởi động thêm **Instance thứ 2** cùng lúc.
     - Đồng thời, Persistent Volume trên Koyeb là dạng đơn gắn kết (`ReadWriteOnce`). Khi ngủ và thức dậy, volume có thể bị kẹt mount lock.
   - **Cách xử lý:** **TẮT HOÀN TOÀN** chế độ Sleep / Scale to zero:
     - Chọn **Fixed: 1 instance** (Always-on).
     - Không đặt min instances = 0. Giữ container luôn luôn thức (`min: 1`) để mọi email OTP đến đều được nhận tức thì trong 50ms.

> [!WARNING]
> **Cực kỳ quan trọng khi dùng SQLite:**
> SQLite lưu trữ dữ liệu dạng single-file trên Persistent Volume (`/data/otp_inbox.db`). Nếu có 2 instances chạy cùng lúc truy cập ghi vào cùng 1 file database, sẽ dẫn đến lỗi `database is locked` hoặc xung đột corrupt dữ liệu. Do đó, **BẮT BUỘC chỉ chạy 1 instance duy nhất (`min: 1, max: 1`)**, tắt Sleep/Idle period, và dùng `--workers 1`.

---

## 2. Cấu hình dùng SQLite trên Koyeb

### Bước A: Tạo & Gắn Persistent Volume (BẮT BUỘC để không mất dữ liệu khi restart)
Mặc định container trên Koyeb là bộ nhớ tạm (ephemeral). Nếu không gắn Volume, mỗi lần redeploy hoặc restart container, file SQLite sẽ bị xoá sạch!

1. Trong Koyeb console, vào **Volumes** -> Nhấn **Create Volume**.
2. Đặt tên: `otp-data` (dung lượng 1GB hoặc tuỳ ý).
3. Trong cài đặt Service Koyeb của bạn -> mục **Volumes**:
   - Chọn volume: `otp-data`
   - **Mount path**: `/data`

### Bước B: Cấu hình Environment Variables trên Koyeb

1. **XOÁ HOẶC KHÔNG ĐẶT** `DATABASE_URL` và `POSTGRES_URL`. Khi 2 biến này trống, app sẽ tự động 100% sử dụng SQLite.
2. Thiết lập các biến môi trường sau:

```bash
# 1. Đường dẫn file SQLite (nằm trong volume /data vừa mount)
DATABASE_PATH=/data/otp_inbox.db

# 2. Token chia sẻ với Cloudflare Worker (khớp WORKER_INGEST_TOKEN trong wrangler.toml)
WORKER_INGEST_TOKEN=<chuỗi_ngẫu_nhiên_bí_mật>        # Đánh dấu Secret

# 3. Tài khoản Admin khởi tạo lần đầu
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<mật_khẩu_admin_của_bạn>              # Đánh dấu Secret

# 4. Bảo mật Cookie (Koyeb dùng HTTPS, đặt = 1)
COOKIE_SECURE=1

# 5. Tên ứng dụng & giới thiệu
APP_NAME=OTP Inbox
REGISTRATION_ENABLED=1
REFERRAL_CODE_GENERATION=1

# 6. TỐI ƯU HOÁ RAM 512MB & DISK 2000MB (KHUYẾN NGHỊ CHO KOYEB NANO)
STORE_RAW_MIME=0             # Không lưu trùng lặp MIME thô base64, tiết kiệm 70% đĩa
STORE_ATTACHMENTS=1          # Lưu file đính kèm (hoặc 0 nếu chỉ dùng nhận mã OTP)
MAX_ATTACHMENT_BYTES=1048576 # Giới hạn 1MB mỗi file đính kèm
MAX_ATTACHMENTS_TOTAL=2097152 # Giới hạn tối đa 2MB tổng đính kèm mỗi email

# 7. TỰ ĐỘNG XOÁ THƯ CŨ (DATA RETENTION)
AUTO_DELETE_ENABLED=1        # Bật tự động xoá thư cũ chạy ngầm (1 = Bật, 0 = Tắt)
AUTO_DELETE_DAYS=7           # Số ngày lưu trữ thư (mặc định xoá thư cũ hơn 7 ngày)
```

---

## 2.1 Cấu hình dùng PostgreSQL (Nếu muốn dùng PostgreSQL thay vì SQLite)

Nếu muốn dùng PostgreSQL thay vì SQLite, chỉ cần thêm biến `DATABASE_URL`:

```bash
# PostgreSQL DSN
DATABASE_URL=postgres://user:password@host/dbname
```
Khi có `DATABASE_URL`, app sẽ ưu tiên dùng PostgreSQL và bỏ qua SQLite.

### Recommended

```bash
APP_NAME=OTP Inbox
SESSION_DAYS=7

# Referral registration. Both default to 0, so set them to 1 explicitly.
REGISTRATION_ENABLED=1
REFERRAL_CODE_GENERATION=1
REFERRAL_CODE_TTL_DAYS=0
REFERRAL_MAX_USES_DEFAULT=1
```

### Optional

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

### Do not set

- `DATABASE_PATH` — ignored as soon as `DATABASE_URL` is present.
- `ASSET_VERSION` — the app hashes `app.css`/`app.js` itself, which is what
  prevents browsers from serving a stale stylesheet after a deploy.

---

## 2.2. Hướng dẫn tối ưu tài nguyên cực hạn (512MB RAM & 2000MB Disk) và Tự động xoá thư

Gói dịch vụ Koyeb Nano chỉ cung cấp **512MB RAM** và **2GB (2000MB) Persistent Volume Disk**. Cấu hình sau đã được thiết kế riêng nhằm đảm bảo ứng dụng chạy mượt mà, không bao giờ bị OOM (Out Of Memory) và không bị đầy ổ cứng.

### 1. Thực hư việc ứng dụng "tự download ảnh về đĩa"
- **Backend KHÔNG tự tải ảnh từ xa:** Ứng dụng không có cơ chế background tải ảnh trên mạng lưu vào đĩa.
- **Lý do đĩa và RAM bị phình to trước đây:**
  1. *Trùng lặp dữ liệu MIME:* Mặc định, email thô RFC822 nhận từ Cloudflare Worker được lưu nguyên vẹn vào cột `messages.raw_mime`. Toàn bộ ảnh inline và file đính kèm được encode base64 bên trong MIME chiếm dung lượng rất lớn.
  2. *Nhân đôi file đính kèm:* Cột `attachments.data` lại trích xuất và lưu thêm một bản nhị phân độc lập của các file/ảnh đó. Một file ảnh 2MB đính kèm sẽ tốn ~2.7MB trong `raw_mime` cộng thêm 2MB trong `attachments` (tổng ~4.7MB cho 1 email).
  3. *Trình duyệt tải ảnh:* Khi mở email HTML, chính trình duyệt web của bạn tải các ảnh từ internet (hoặc qua proxy bảo mật DuckDuckGo).
- **Giải pháp tối ưu đã áp dụng:**
  - `STORE_RAW_MIME=0`: Không lưu khối `raw_mime` thô nữa. Khi bạn hoặc API cần tải file `.eml` hoặc xem raw email, ứng dụng sẽ tự động tổng hợp (synthesize) lại định dạng RFC822 chuẩn on-the-fly. Tiết kiệm **60% - 80%** dung lượng đĩa cho mỗi bức thư!
  - `MAX_ATTACHMENT_BYTES=1048576` (1MB) & `MAX_ATTACHMENTS_TOTAL=2097152` (2MB): Chặn các file đính kèm quá khổ gây đầy đĩa.
  - Nếu chỉ dùng ứng dụng để hứng mã OTP/xác thực tài khoản, bạn có thể đặt `STORE_ATTACHMENTS=0` để bỏ qua lưu file đính kèm hoàn toàn.

### 2. Tối ưu SQLite cho môi trường 512MB RAM
- `PRAGMA cache_size = -4000`: Giới hạn bộ đệm trang (page cache) của SQLite tối đa 4MB RAM (thay vì để SQLite tự mở rộng).
- `PRAGMA auto_vacuum = INCREMENTAL`: Cho phép dọn dẹp các trang trống và trả dung lượng đĩa về cho hệ điều hành khi thư cũ bị xoá.
- `PRAGMA wal_autocheckpoint = 1000` & `wal_checkpoint(TRUNCATE)`: Tự động gom file Write-Ahead Log (WAL), tránh việc file WAL âm thầm phình to hàng trăm MB.
- Giới hạn render ảnh CID inline trong email: Tối đa 512KB/ảnh và tổng cộng không quá 2MB/email, tránh tràn RAM khi parse email HTML phức tạp.

### 3. Tự động xoá thư cũ (Auto-Delete & Data Retention)
Ứng dụng tích hợp hệ thống dọn dẹp thư tự động hoàn chỉnh:
- **Cấu hình:**
  - Bật/tắt qua giao diện Web: Đăng nhập Admin -> vào tab **Mã giới thiệu & Cài đặt hệ thống** -> bảng **Cài đặt lưu trữ & Dọn dẹp tự động**. Bạn có thể bật switch và chỉnh số ngày lưu trữ tuỳ ý (ví dụ: 3 ngày, 7 ngày, 30 ngày).
  - Hoặc cấu hình qua biến môi trường Koyeb: `AUTO_DELETE_ENABLED=1` và `AUTO_DELETE_DAYS=7`.
- **Cơ chế hoạt động:**
  - Có tiến trình chạy ngầm (Background Worker) tự động quét và dọn dẹp mỗi 1 tiếng một lần.
  - Khi xoá thư, hệ thống cascade xoá sạch các bảng liên quan: `attachments`, `message_reads`, `notifications`, và chỉ mục tìm kiếm `messages_fts`.
  - Sau khi xoá, hệ thống tự động kích hoạt `PRAGMA incremental_vacuum` và `PRAGMA wal_checkpoint(TRUNCATE)` để ngay lập tức thu hồi dung lượng đĩa.
  - Có nút **"Dọn dẹp ngay"** (Manual Purge) trên giao diện Admin để bạn bấm dọn sạch thư quá hạn bất cứ khi nào cần.

### 4. Sửa lỗi hiển thị Preview HTML và cuộn trang khi có OTP
- **Thu gọn banner OTP:** Banner mã OTP nổi bật phía trên nay có nút mũi tên thu gọn (collapse). Khi thu gọn chỉ còn 1 dòng huy hiệu nhỏ gọn gàng, trả lại toàn bộ chiều cao cho nội dung thư.
- **Iframe tự co giãn theo nội dung:** Iframe hiển thị email tự động giao tiếp chiều cao thực tế (`postMessage`) với trang ngoài, chiều cao iframe co giãn tương ứng với email (`height = scrollHeight`). Người dùng có thể cuộn trang một cách tự nhiên bằng con lăn chuột hoặc vuốt cảm ứng mà không bị kẹt thanh cuộn bên trong khung bé tí.
- **Toolbar tiện ích:** Bổ sung thanh công cụ trên đầu thư gồm:
  - Nút **"Mở tab mới ↗"**: Mở email trên tab trình duyệt riêng biệt, hiển thị không giới hạn.
  - Nút **"Phóng to / Thu nhỏ"**: Phóng to khung đọc email thành modal toàn màn hình để đọc các bức thư dài hoặc định dạng bảng phức tạp.

---

## 3. Security notes on the database URL

- Treat `DATABASE_URL` as a secret. Mark it secret in your host’s dashboard so
  the password is not printed in deploy logs.
- Managed Postgres from many PaaS providers is reachable from the public
  internet by design. If that is not acceptable, use a private network or an
  external provider with IP allow-listing.
- Rotate credentials immediately if they were ever shared in chat, screenshots,
  or commit history.

---

## 4. First deploy

The app is idempotent about schema, so a redeploy is safe.

1. Set the environment variables and deploy.
2. Read the logs. On a fresh database you should see:

   ```
   [OTP Inbox] Admin created
   [OTP Inbox] username: admin
   [OTP Inbox] API key (shown once): sk-otp-...
   ```

   **Copy that API key immediately** — it is never shown again.

3. Open the site and log in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`.
4. Check `/health`:

   ```json
   {"status":"ok","app":"OTP Inbox","version":"...","search":"fts",
    "registration":true,"code_generation":true}
   ```

   `search: "fts"` means the full-text index is active. On PostgreSQL this is
   a generated `tsvector` column with a GIN index, maintained by the database.

5. In **Mã giới thiệu** (Referral), confirm both global switches reflect what
   you set, then create your first code with the "Tạo mã thủ công" panel.

---

## 5. Setting up Cloudflare Email Routing & Worker (Catch-All)

The application handles dynamic email ingestion through a Cloudflare Worker and supports full domain-wide catch-all routing.

### 5.1. Deploying the Cloudflare Worker

The worker script lives in [`worker/`](../worker/).

1. Open a terminal in the project directory:
   ```bash
   cd worker
   ```
2. Set the secrets required by the worker:
   ```bash
   # Shared secret with the API (must match WORKER_INGEST_TOKEN)
   npx wrangler secret put WORKER_INGEST_TOKEN

   # Ingest endpoint on your API service
   npx wrangler secret put APP_INGEST_URL
   # Value: https://<your-app>.example.com/api/ingest
   ```
3. Deploy the worker:
   ```bash
   npx wrangler deploy
   ```

### 5.1.1. Keep-alive cron

`worker/wrangler.toml` includes a Cron Trigger (`*/5 * * * *`) that pings the
backend `/health` endpoint so PaaS instances do not cold-start when mail arrives.

Manual check: open `https://<worker-name>.<account>.workers.dev/ping`.


### 5.2. Configuring Cloudflare Email Routing

In your Cloudflare dashboard for your domain:

1. Navigate to **Email** → **Email Routing** → **Routing rules**.
2. Enable Email Routing if not already active (Cloudflare will configure MX and SPF records automatically).
3. Under **Catch-all rule**:
   - Status: **Active**
   - Action: **Send to Worker**
   - Destination: Select your deployed worker (`otp-inbox-worker`).
4. (Optional) Custom Address rules: You can also route specific addresses or subdomains to the Worker if you prefer not to catch all emails.

### 5.3. How Ingestion & Routing Rules Work Together

```text
User sends email to anything@yourdomain.com
                    │
                    ▼
     Cloudflare Email Routing (Catch-all)
                    │
                    ▼
     Cloudflare Worker (worker.js)
                    │ (POST /api/ingest + X-Worker-Token)
                    ▼
          FastAPI App (app.py)
                    │
  ┌─────────────────┴─────────────────┐
  ▼                                   ▼
Mailbox exists?                     Mailbox is new
  ├── exact_override = 1:             ├── Check Routing Rules:
  │   Keep current owner/access       │   Regex > Prefix > Domain > Catch-All
  └── exact_override = 0:             ├── Match found:
      Sync access with active rules   │   Auto-provisions & assigns to rule target
                                      └── No match:
                                          Creates orphan mailbox (admin alerted)
```

---

## 6. The referral feature, end to end

Both switches and the per-user permission are independent, and all three
default to off:

| Control | Where | Default | Effect |
| --- | --- | --- | --- |
| `registration_enabled` | Dashboard switch, or env on first boot | **off** | Whether the signup form is reachable. A valid code is required either way. |
| `referral_code_generation` | Dashboard switch, or env on first boot | **off** | Global gate. When off, *no* user can create a code, even one with the permission. |
| `can_generate_codes` | Per user, in the Người dùng table | **off for every new account** | The actual grant. |

A code you generate is shown **once** and stored only as a SHA-256 digest, so it
cannot be displayed again. If it is lost, generate a new one — the previous code
is retired automatically.

Codes minted before a permission is revoked stop working: turning
`can_generate_codes` off also deactivates the codes that user already holds.

A self-registered account gets **no mailbox access** and no code permission. It
can read nothing until an admin grants access or a routing rule matches. That is
deliberate: a leaked or brute-forced referral code must not be a path to mail.

---

## 7. Local development

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt -r requirements-dev.txt
copy .env.example .env          # Windows
uvicorn app:app --reload
```

Without `DATABASE_URL` the app uses `./data/otp_inbox.db`, so you can develop
with no database server at all.

### Tests

```bash
python -m pytest tests -q                     # backend
cd tests/js && npm install && npm test        # frontend (needs Node 18+)
```

The backend suite uses a throwaway SQLite database in a temp directory and never
touches your real data.

---

## 8. Operational notes

**Migrating from SQLite to PostgreSQL.** The two schemas are not copied
automatically. For a fresh start, just point `DATABASE_URL` at an empty
database and redeploy — the schema is created on boot. If you need your existing
rows, export the tables and load them with `COPY`, in this order (parents
first): `users`, `mailboxes`, `routing_rules`, `mailbox_access`, `messages`,
`attachments`, `message_reads`, `notifications`, `audit_logs`, `referral_codes`,
`registrations`, `app_settings`. Keep `id` values so existing foreign keys stay
valid; the identity sequences then need a reset:

```sql
SELECT setval(pg_get_serial_sequence('messages','id'),
              COALESCE(MAX(id), 1)) FROM messages;
```

**Single instance.** Rate limiting is in-process, so it only protects a single
instance. The application data itself lives in the database, so running two
instances is safe for reads and writes; only the login/registration throttles
become per-instance. Use one instance unless you outgrow that.

**Static assets.** There is no build step. `app.css` and `app.js` are served
directly and their URLs carry a hash of their own contents, so a deploy always
reaches the browser. After changing them locally, a hard reload is enough.
