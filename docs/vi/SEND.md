# Gửi email đi (Email Sending)

**Ngôn ngữ:** [English](../en/SEND.md) · [Tiếng Việt](SEND.md)

OTP Inbox có thể **gửi** thư *từ* bất kỳ mailbox nào user được phép truy cập,
dùng [Cloudflare Email Service → Email Sending](https://developers.cloudflare.com/email-service/)
qua binding Worker `send_email` (`env.EMAIL.send`). Giao diện soạn thư giống Gmail
(cửa sổ dock góc phải, Từ / Đến / Cc / Bcc, đính kèm, thư mục Đã gửi).

Nhận thư catch-all (Email Routing) vẫn miễn phí. **Gửi tới địa chỉ tuỳ ý cần
Workers Paid** (~$5/tháng, gồm 3 000 email đi/tháng; sau đó ~$0.35 / 1 000).
Gửi tới *verified destination* trong tài khoản Cloudflare vẫn miễn phí trên Free
và Paid. Xem [pricing](https://developers.cloudflare.com/email-service/platform/pricing/).

---

## Kiến trúc

```text
Web UI / API  →  POST /api/mailboxes/{id}/send
                      │
                      │  JSON + X-Worker-Token
                      ▼
              Cloudflare Worker  POST /send
                      │
                      │  env.EMAIL.send({ from, to, subject, text, html, … })
                      ▼
              Cloudflare Email Sending  →  MX người nhận
```

- **Từ (From)** luôn là địa chỉ mailbox. Client không spoof được identity khác.
- API ghi mọi lần thử vào `outbound_messages` (`sent` / `failed`) và audit log.
- Worker dùng chung `WORKER_INGEST_TOKEN` để xác thực `/send` (cùng secret với ingest).

---

## Điều kiện trước

1. Domain nằm trên **Cloudflare DNS**
2. **Workers Paid** (nếu gửi tới người nhận tuỳ ý)
3. Domain đã **onboard** tại Dashboard → **Compute → Email Service → Email Sending**
   (Cloudflare thêm SPF / DKIM / DMARC / bounce MX dưới `cf-bounce`)
4. Worker đã deploy với `[[send_email]] name = "EMAIL"` (đã có trong `worker/wrangler.toml`)
5. API env: `WORKER_SEND_URL=https://<worker-host>/send`

Tài liệu Cloudflare:

- [Send emails](https://developers.cloudflare.com/email-service/get-started/send-emails/)
- [Workers API](https://developers.cloudflare.com/email-service/api/send-emails/workers-api/)
- [Send bindings](https://developers.cloudflare.com/email-service/configuration/send-bindings/)
- [Limits](https://developers.cloudflare.com/email-service/platform/limits/) — 50 người nhận, 5 MiB/thư, …

---

## Checklist cấu hình

### 1. Nâng Workers Paid (nếu cần)

Dashboard → Workers & Pages → Plans → **Workers Paid**.

### 2. Onboard domain gửi

1. Dashboard → **Email Service → Email Sending → Onboard Domain**
2. Chọn cùng domain đang dùng Email Routing
3. Đợi DNS (thường vài phút; tối đa 24 giờ)

Chưa onboard: chỉ gửi được tới verified destination (miễn phí).
Đã onboard + Paid: gửi tới bất kỳ địa chỉ nào (tính quota).

### 3. Binding Worker

Trong `worker/wrangler.toml`:

```toml
[[send_email]]
name = "EMAIL"
```

Deploy lại:

```bash
cd worker
npx wrangler deploy
```

Secrets (giống bước ingest):

```bash
npx wrangler secret put WORKER_INGEST_TOKEN
npx wrangler secret put APP_INGEST_URL
```

### 4. Trỏ API tới `/send`

Trong `.env` / biến môi trường host:

```bash
WORKER_SEND_URL=https://otp-inbox-worker.<account>.workers.dev/send
```

Tuỳ chọn:

```bash
MAX_SEND_RECIPIENTS=50
MAX_SEND_ATTACHMENTS=10
MAX_SEND_ATTACHMENT_BYTES=3145728
MAX_SEND_BODY_CHARS=200000
```

Restart API, kiểm tra:

```bash
curl -s https://YOUR-API/health
# → "send_enabled": true
```

Khi `WORKER_SEND_URL` trống: nút **Soạn thư** ẩn, endpoint gửi trả **503**.

---

## Dùng trên UI

1. Đăng nhập → **Soạn thư** (nút xanh kiểu Gmail)
2. Chọn **Từ** (mailbox bạn có quyền)
3. Điền **Đến** / Cc / Bcc, chủ đề, nội dung; đính kèm nếu cần
4. **Gửi** — toast thành công; xem lại trong **Đã gửi**
5. Mở thư nhận → **Trả lời** điền sẵn Đến / chủ đề

Phím tắt: `C` mở soạn thư (hoặc copy OTP nếu đang có mã); `Esc` đóng nháp.

---

## API

### `POST /api/mailboxes/{mailbox_id}/send`

Auth: session + CSRF **hoặc** Bearer API key.

```bash
curl -s -X POST \
  -H "Authorization: Bearer sk-otp-YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "to": "user@example.com",
    "subject": "Xin chào",
    "text": "Nội dung"
  }' \
  https://YOUR-API/api/mailboxes/12/send
```

| Trường | Ghi chú |
| --- | --- |
| `to` / `cc` / `bcc` | Chuỗi hoặc mảng; tối đa 50 địa chỉ gộp |
| `subject` | Bắt buộc, ≤ 998 ký tự |
| `text` / `body` / `html` | Cần ít nhất một phần thân thư |
| `attachments[]` | `content` base64, `filename`, `type` tuỳ chọn |

### `GET /api/sent` · `GET /api/sent/{id}`

Danh sách / chi tiết thư đã gửi (theo ACL mailbox).

Worker `POST /send` chỉ dành cho API nội bộ — bắt buộc header `X-Worker-Token`.

---

## Giới hạn & lỗi thường gặp

| Giới hạn | Giá trị |
| --- | --- |
| Người nhận | 50 |
| Kích thước thư | 5 MiB (25 MiB nếu chỉ gửi verified destination) |
| Đính kèm (mặc định app) | 10 file, 3 MiB/file |

| Mã Cloudflare | Ý nghĩa |
| --- | --- |
| `E_SENDER_NOT_VERIFIED` | Domain chưa onboard / From không hợp lệ |
| `E_RATE_LIMIT_EXCEEDED` / `E_DAILY_LIMIT_EXCEEDED` | Hết quota — HTTP 429 |
| `E_SEND_NOT_CONFIGURED` | Worker thiếu binding `[[send_email]]` |

---

## Bảo mật

- From bị ép theo mailbox — không tin `from` từ client
- Kiểm tra ACL giống đọc mailbox
- CSRF áp dụng với session cookie
- Token Worker phải bí mật; xoay cùng token ingest
- Gửi lỗi ghi audit `send_failed`

---

## Xử lý sự cố

| Hiện tượng | Cách xử lý |
| --- | --- |
| Không thấy nút Soạn thư | Đặt `WORKER_SEND_URL`, restart; xem `/health` |
| API 503 | URL trống hoặc không tới được Worker |
| `E_SEND_NOT_CONFIGURED` | Thêm binding, `wrangler deploy` |
| `E_SENDER_NOT_VERIFIED` | Onboard domain Email Sending |
| 401 từ Worker | Sai `WORKER_INGEST_TOKEN` |
| Chỉ Free plan | Chỉ gửi được verified destination |

Chi tiết tiếng Anh đầy đủ hơn: [en/SEND.md](../en/SEND.md).
