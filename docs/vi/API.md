# OTP Inbox — Tổng quan API (Tiếng Việt)

**Ngôn ngữ:** [English](../en/API.md) · [Tiếng Việt](API.md) · [中文](../zh/API.md) · [Русский](../ru/API.md)

Tài liệu đầy đủ (hơn 1 300 dòng): **[../API.md](../API.md)**
Swagger UI tương tác: `GET /docs` trên instance đang chạy.

---

## Xác thực

Hầu hết endpoint `/api/*` chấp nhận **API key** hoặc **session cookie**.

| Cơ chế | Cách dùng | Dùng cho |
| --- | --- | --- |
| **API key** (khuyến nghị cho tích hợp) | Header `Authorization: Bearer sk-otp-...` | Mọi `/api/*` đọc + ghi (trừ ngoại lệ bên dưới). Không cần CSRF. |
| **Session cookie** | Cookie `otp_session` (HttpOnly, SameSite=Lax) | Web UI |
| **CSRF token** | Header `X-CSRF-Token: <token>` | Chỉ khi dùng session cookie trên `POST` / `PATCH` / `DELETE` |
| **Worker token** | Header `X-Worker-Token: <WORKER_INGEST_TOKEN>` | Chỉ `POST /api/ingest` |

**Endpoint không dùng API key:**

| Endpoint | Xác thực |
| --- | --- |
| `POST /api/login`, `POST /api/register` | username + password (+ mã giới thiệu) |
| `POST /api/logout` | session cookie |
| `GET /api/auth/config`, `GET /health` | công khai |
| `POST /api/ingest` | worker token |

Khi gửi cả cookie lẫn Bearer, **Bearer được ưu tiên**.
Rate limit API key mặc định: **120 yêu cầu / 60 giây / key**.

### Ví dụ nhanh

```bash
# Health check (công khai)
curl -s https://your-host/health

# Dashboard qua API key
curl -s -H "Authorization: Bearer sk-otp-YOUR_KEY" \
     https://your-host/api/dashboard

# Tạo mailbox
curl -s -X POST \
     -H "Authorization: Bearer sk-otp-YOUR_KEY" \
     -H "Content-Type: application/json" \
     -d '{"address":"otp-test@example.com"}' \
     https://your-host/api/mailboxes/create
```

---

## Tổng quan các endpoint chính

| Nhóm | Ví dụ endpoint | Ghi chú |
| --- | --- | --- |
| Health | `GET /health` | Trả về version, chế độ tìm kiếm, feature flags |
| Đăng nhập / đăng xuất | `POST /api/login` · `POST /api/logout` | Session-based; trả về CSRF token |
| Đăng ký | `POST /api/register` | Yêu cầu mã giới thiệu hợp lệ khi được bật |
| Dashboard | `GET /api/dashboard` | Số lượng tin nhắn, danh sách mailbox |
| Tin nhắn | `GET /api/messages` | Phân trang; hỗ trợ `q=` full-text search |
| Tin nhắn đơn | `GET /api/messages/{id}` | Bao gồm trường OTP nếu đã trích xuất |
| Đính kèm | `GET /api/attachments/{id}` | Tải xuống nhị phân |
| Mailbox | `GET /api/mailboxes` · `POST /api/mailboxes/create` | |
| API key | `POST /api/keys/create` · `DELETE /api/keys/{id}` | API key cho lập trình viên |
| Admin — người dùng | `GET /api/admin/users` | Chỉ Admin |
| Admin — routing | `GET /api/admin/routing-rules` | |
| Admin — referral | `POST /api/admin/referral-codes/generate` | |
| Ingest | `POST /api/ingest` | Gọi bởi Cloudflare Worker; cần worker token |
| Gửi thư | `POST /api/mailboxes/{id}/send` | Outbound Email Sending (cần `WORKER_SEND_URL`) |
| Đã gửi | `GET /api/sent` · `GET /api/sent/{id}` | Lịch sử thư đi |

Hướng dẫn gửi thư: **[SEND.md](SEND.md)**

---

## Mã lỗi

Mọi lỗi đều theo định dạng:

```json
{"detail": "thông báo lỗi"}
```

Các mã HTTP phổ biến:

| Mã | Ý nghĩa |
| --- | --- |
| `400` | Yêu cầu không hợp lệ / lỗi xác thực |
| `401` | Thiếu hoặc sai thông tin xác thực |
| `403` | Không đủ quyền |
| `404` | Không tìm thấy tài nguyên |
| `409` | Xung đột (ví dụ địa chỉ mailbox trùng lặp) |
| `429` | Vượt quá giới hạn tốc độ |
| `500` | Lỗi server |

---

Để xem đầy đủ tài liệu endpoint bao gồm schema request/response, tham số query
và ví dụ curl, xem **[../API.md](../API.md)**.
