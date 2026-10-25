# OTP Inbox

**语言：** [English](../../README.md) · [Tiếng Việt](../vi/README.md) · [中文](README.md) · [Русский](../ru/README.md)

自托管的 **catch-all 邮件收件箱**，具备自动 OTP 提取、多用户访问控制、邀请码注册以及开发者 API 功能。

收到的邮件由 [Cloudflare Email Routing](https://developers.cloudflare.com/email-routing/) Worker 接收，并由 FastAPI 后端（SQLite 或 PostgreSQL）存储。受 Gmail 启发的 Web UI 让你轻松浏览邮箱、搜索邮件并管理路由规则。

[![CI](https://github.com/nayumiDEV/Cloudflare-Mailbox/actions/workflows/ci.yml/badge.svg)](https://github.com/nayumiDEV/Cloudflare-Mailbox/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](../../LICENSE)

## 功能特性

- **Catch-all 邮件接收** 通过 Cloudflare Worker → `POST /api/ingest`
- **外发邮件**（可选）Cloudflare Email Sending + Gmail 风格撰写 — [en/SEND.md](../en/SEND.md)
- **OTP 自动提取** 从主题/正文中提取，带误报过滤
- **邮箱与路由规则**（精确、前缀、域名、正则、catch-all）
- **多用户访问控制** — 所有者、共享访问、管理员覆盖
- **邀请码注册** — 默认关闭；邀请码以哈希形式存储
- **开发者 API** — Bearer API 密钥用于读写，无需 CSRF
- **全文搜索**（SQLite FTS5 / PostgreSQL `tsvector`）
- **消息保留** — 可选自动删除旧邮件
- **零构建步骤** — 静态 `app.css` / `app.js`，带内容哈希缓存清除
- **国际化** — UI 通过语言切换器支持 EN / VI / ZH / RU

## 架构

```text
anything@your-domain.com
        │
        ▼
Cloudflare Email Routing（catch-all）
        │
        ▼
Cloudflare Worker  (worker/)
        │  POST /api/ingest + X-Worker-Token
        ▼
FastAPI  (app/main.py)  ·  SQLite 或 PostgreSQL
        │
        ▼
Web UI  (static/)  +  REST API  (/api/*)
```

## 快速开始（本地）

**要求：** Python 3.11+，Node 18+（仅用于前端测试）

完整的逐步指南请参阅 **[docs/zh/SETUP.md](../zh/SETUP.md)**。

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Unix:     source .venv/bin/activate

pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
# 设置 ADMIN_PASSWORD、WORKER_INGEST_TOKEN；本地 HTTP 时设置 COOKIE_SECURE=0

uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000 并用 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 登录。

首次启动时，日志会打印一次性管理员 API 密钥（`sk-otp-…`）— 请立即复制，
它不会再次显示。

```bash
python -m pytest tests -q
```

## 生产部署

参阅 **[docs/zh/DEPLOY.md](../zh/DEPLOY.md)**，内容包括：

- 任意 VPS / PaaS（SQLite 卷或托管 PostgreSQL）
- Cloudflare Email Routing + Worker 密钥配置
- 资源 / 数据保留调优
- 邀请功能开关

Worker 入口：[`worker/`](../../worker/)（`wrangler.toml` + `worker.js`）。

## 配置

复制 [`.env.example`](../../.env.example)。重要变量：

| 变量 | 用途 |
| --- | --- |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | 初始管理员（密码仅在首次创建时生效） |
| `WORKER_INGEST_TOKEN` | `/api/ingest` 共享密钥 |
| `DATABASE_URL` | PostgreSQL DSN — 若未设置，使用 `DATABASE_PATH` 处的 SQLite |
| `APP_NAME` | UI 和 `/health` 中的品牌名称 |
| `REGISTRATION_ENABLED` | 开放注册表单（仍需邀请码） |
| `AUTO_DELETE_ENABLED` / `AUTO_DELETE_DAYS` | 后台自动消息保留 |

## API

完整 REST 参考：**[docs/zh/API.md](../zh/API.md)**

交互式 Swagger UI：在运行中的实例上访问 `GET /docs`。

```bash
# 健康检查
curl -s https://your-host/health

# 开发者 API
curl -s -H "Authorization: Bearer sk-otp-YOUR_KEY" https://your-host/api/dashboard
```

## 项目结构

```text
app/                 Python 包（FastAPI）
  main.py            HTTP API + 业务逻辑  →  uvicorn app.main:app
  db.py              SQLite / PostgreSQL 连接辅助
  schema.py          Schema DDL（两种后端通用）
  seed.py            可选演示数据  →  python -m app.seed
static/              Web UI (index.html, app.js, app.css)
worker/              Cloudflare Email Worker
tests/               pytest + JS 单元 / E2E 测试
docs/                文档（SETUP、DEPLOY、API）
.env.example         复制 → .env
```

## 安全

- 私下报告漏洞 — 参见 [SECURITY.md](../../SECURITY.md)
- 永远不要提交 `.env`、`data/*.db` 或 Wrangler 密钥
- 在需要之前保持注册和邀请码生成关闭

## 贡献

参见 [CONTRIBUTING.md](../../CONTRIBUTING.md)。请遵守
[行为准则](../../CODE_OF_CONDUCT.md)。

## Community links

[LINUX DO Community](https://linux.do/)

## 许可证

[MIT](../../LICENSE)
