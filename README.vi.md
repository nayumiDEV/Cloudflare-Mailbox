# OTP Inbox

**Ngôn ngữ:** [English](README.md) · [Tiếng Việt](README.vi.md) · [中文](README.zh.md) · [Русский](README.ru.md)

**Catch-all email inbox** tự lưu trữ với tính năng tự động trích xuất OTP, kiểm soát truy cập đa người dùng, đăng ký có mã giới thiệu, và Developer API.

Email đến được nhận bởi [Cloudflare Email Routing](https://developers.cloudflare.com/email-routing/) Worker và lưu trữ bởi backend FastAPI (SQLite hoặc PostgreSQL). Giao diện web theo phong cách Gmail giúp bạn duyệt mailbox, tìm kiếm tin nhắn và quản lý quy tắc định tuyến.

[![CI](https://github.com/nayumiDEV/Cloudflare-Mailbox/actions/workflows/ci.yml/badge.svg)](https://github.com/nayumiDEV/Cloudflare-Mailbox/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## Tính năng

- **Catch-all ingest** qua Cloudflare Worker → `POST /api/ingest`
- **Trích xuất OTP** từ tiêu đề/nội dung với lọc false-positive
- **Mailbox & quy tắc định tuyến** (chính xác, tiền tố, domain, regex, catch-all)
- **Kiểm soát truy cập đa người dùng** — owner, shared access, admin overrides
- **Đăng ký có mã giới thiệu** — mặc định đóng; mã được hash khi lưu trữ
- **Developer API** — Bearer API key cho đọc/ghi không cần CSRF
- **Tìm kiếm toàn văn** (SQLite FTS5 / PostgreSQL `tsvector`)
- **Lưu trữ có thời hạn** — tự động xóa tin nhắn cũ tùy chọn
- **Không cần build** — static `app.css` / `app.js` với content-hash cache busting
- **Đa ngôn ngữ** — UI hỗ trợ EN / VI / ZH / RU qua bộ chuyển đổi ngôn ngữ

## Kiến trúc

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
FastAPI  (app/main.py)  ·  SQLite hoặc PostgreSQL
        │
        ▼
Web UI  (static/)  +  REST API  (/api/*)
```

## Khởi động nhanh (local)

**Yêu cầu:** Python 3.11+, Node 18+ (chỉ cho frontend tests)

Xem hướng dẫn từng bước đầy đủ tại **[docs/vi/SETUP.md](docs/vi/SETUP.md)**.

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Unix:     source .venv/bin/activate

pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
# Đặt ADMIN_PASSWORD, WORKER_INGEST_TOKEN; COOKIE_SECURE=0 cho plain HTTP

uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Mở http://127.0.0.1:8000 và đăng nhập bằng `ADMIN_USERNAME` / `ADMIN_PASSWORD`.

Khi khởi động lần đầu, log sẽ in ra một admin API key (`sk-otp-…`) — hãy sao chép ngay;
nó không bao giờ được hiển thị lại.

```bash
python -m pytest tests -q
```

## Deploy lên production

Xem **[docs/vi/DEPLOY.md](docs/vi/DEPLOY.md)** để biết:

- Koyeb (SQLite volume hoặc managed PostgreSQL)
- Cloudflare Email Routing + Worker secrets
- Tối ưu tài nguyên / lưu trữ có thời hạn
- Các tùy chọn tính năng giới thiệu

Worker entrypoint: [`worker/`](worker/) (`wrangler.toml` + `worker.js`).

## Cấu hình

Sao chép [`.env.example`](.env.example). Các biến quan trọng:

| Biến | Mục đích |
| --- | --- |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Admin khởi tạo (mật khẩu chỉ áp dụng khi tạo lần đầu) |
| `WORKER_INGEST_TOKEN` | Shared secret cho `/api/ingest` |
| `DATABASE_URL` | PostgreSQL DSN — nếu không đặt, dùng SQLite tại `DATABASE_PATH` |
| `APP_NAME` | Chuỗi thương hiệu trong UI + `/health` |
| `REGISTRATION_ENABLED` | Mở form đăng ký (vẫn cần mã giới thiệu) |
| `AUTO_DELETE_ENABLED` / `AUTO_DELETE_DAYS` | Lưu trữ tin nhắn tự động nền |

## API

Tài liệu REST đầy đủ: **[docs/vi/API.md](docs/vi/API.md)**

Swagger UI tương tác: `GET /docs` trên instance đang chạy.

```bash
# Health
curl -s https://your-host/health

# Developer API
curl -s -H "Authorization: Bearer sk-otp-YOUR_KEY" https://your-host/api/dashboard
```

## Cấu trúc dự án

```text
app/                 Python package (FastAPI)
  main.py            HTTP API + business logic  →  uvicorn app.main:app
  db.py              SQLite / PostgreSQL connection helpers
  schema.py          Schema DDL (cả hai backends)
  seed.py            Dữ liệu demo tùy chọn  →  python -m app.seed
static/              Web UI (index.html, app.js, app.css)
worker/              Cloudflare Email Worker
tests/               pytest + JS unit/e2e tests
docs/                Tài liệu (SETUP, DEPLOY, API)
deploy/
  koyeb.yaml         Ví dụ cấu hình Koyeb service
.env.example         Copy → .env
```

## Bảo mật

- Báo cáo lỗ hổng bảo mật một cách riêng tư — xem [SECURITY.md](SECURITY.md)
- Không bao giờ commit `.env`, `data/*.db`, hoặc Wrangler secrets
- Giữ đăng ký và tạo mã giới thiệu tắt cho đến khi bạn cần

## Đóng góp

Xem [CONTRIBUTING.md](CONTRIBUTING.md). Vui lòng tuân theo
[Code of Conduct](CODE_OF_CONDUCT.md).

## Giấy phép

[MIT](LICENSE)
