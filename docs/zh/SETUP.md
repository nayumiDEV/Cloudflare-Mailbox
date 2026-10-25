# 完整安装指南

**语言：** [English](../SETUP.md) · [Tiếng Việt](../vi/SETUP.md) · [中文](SETUP.md) · [Русский](../ru/SETUP.md)

本指南将逐步引导你从一台空白机器开始，完整运行 **OTP Inbox**（Cloudflare Mailbox）直至生产就绪的 catch-all OTP 收件箱。

相关文档：

| 文档 | 用途 |
| --- | --- |
| [DEPLOY](DEPLOY.md) | PaaS / Koyeb 调优、数据保留、保活 |
| [API](API.md) | REST 概述（完整参考：[API.md](../API.md)） |
| [SECURITY.md](../../SECURITY.md) | 漏洞报告与安全加固 |

---

## 0. 你将构建什么

```text
Internet mail  →  Cloudflare Email Routing (catch-all)
                         │
                         ▼
               Cloudflare Worker  (worker/)
                         │  POST /api/ingest + X-Worker-Token
                         ▼
               FastAPI app  (app/main.py)  +  SQLite 或 PostgreSQL
                         │
                         ▼
               Web UI  (static/)  +  Developer API
```

你需要：

1. 一个托管在 Cloudflare 上的域名（用于 Email Routing）
2. 运行 API 的地方（测试用本地电脑，生产用 Koyeb / 任意 Linux 主机）
3. 该主机上安装 Python 3.11+

---

## 1. 前置要求

### 1.1 软件

| 工具 | 版本 | 用途 |
| --- | --- | --- |
| Python | **3.11+**（推荐 3.12） | FastAPI 后端 |
| pip | Python 自带 | 安装依赖 |
| Git | 任意近期版本 | 克隆仓库 |
| Node.js | **18+**（可选） | 仅用于前端单元测试 |
| Cloudflare 账号 | 免费套餐即可 | Email Routing + Worker |
| Wrangler CLI | 通过 `npx wrangler` | 部署 Worker |

检查：

```bash
python --version    # >= 3.11
git --version
node --version      # 可选
```

### 1.2 你需要创建的账号与密钥

将这些存入密码管理器——永远不要提交它们：

| 密钥 | 用途 |
| --- | --- |
| `ADMIN_PASSWORD` | 首次管理员登录 |
| `WORKER_INGEST_TOKEN` | Worker ↔ API 共享密钥 |
| Admin API key `sk-otp-…` | 首次启动时**一次性**打印 |
| Cloudflare API / Wrangler 登录 | 部署 Worker |
| `DATABASE_URL`（可选） | 托管 Postgres |

生成强令牌：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

---

## 2. 获取代码

```bash
git clone https://github.com/nayumiDEV/Cloudflare-Mailbox.git
cd Cloudflare-Mailbox
```

### 2.1 仓库结构

```text
Cloudflare-Mailbox/
├── app/                 # Python 包（FastAPI）
│   ├── main.py          # HTTP API + 业务逻辑  →  uvicorn app.main:app
│   ├── db.py            # SQLite / PostgreSQL 适配器
│   ├── schema.py        # 两种后端的 DDL
│   └── seed.py          # 可选演示数据
├── static/              # Web UI（无构建步骤）
│   ├── index.html
│   ├── app.js / app.css
│   ├── i18n.js
│   └── locales/         # en, vi, zh, ru
├── worker/              # Cloudflare Email Worker
│   ├── worker.js
│   └── wrangler.toml
├── tests/               # pytest + JS 单元测试
├── docs/                # 本文档
├── deploy/koyeb.yaml    # Koyeb 服务配置示例
├── requirements.txt
├── requirements-dev.txt
└── .env.example         # 复制 → .env
```

---

## 3. 本地开发（SQLite — 零配置）

### 3.1 创建虚拟环境

**Windows（PowerShell）：**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt
```

**macOS / Linux：**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt
```

### 3.2 配置环境

```bash
# Windows
copy .env.example .env

# macOS / Linux
cp .env.example .env
```

编辑 `.env` — **本地 HTTP 最低配置：**

```bash
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-me-to-at-least-10-chars
WORKER_INGEST_TOKEN=paste-the-token-urlsafe-48-output-here

# SQLite 文件（自动创建）
DATABASE_PATH=./data/otp_inbox.db

# http://127.0.0.1 必需（浏览器拒绝普通 HTTP 上的 Secure cookie）
COOKIE_SECURE=0

APP_NAME=OTP Inbox
REGISTRATION_ENABLED=0
REFERRAL_CODE_GENERATION=0
```

每个变量都在 `.env.example` 中有说明。不要提交 `.env`。

### 3.3 启动 API

始终从**仓库根目录**运行命令：

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000

在**全新**数据库上，服务器日志会打印：

```text
[OTP Inbox] Admin created
[OTP Inbox] username: admin
[OTP Inbox] API key (shown once): sk-otp-................
```

**立即复制 API 密钥** — 它不会再次显示。之后可以从界面的账号设置中轮换。

使用 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 登录。

### 3.4 健康检查

```bash
curl -s http://127.0.0.1:8000/health
```

预期结果：

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

### 3.5 可选演示数据

```bash
python -m app.seed
```

创建示例用户 / 邮箱 / OTP 消息（脚本输出中可查看密码）。

### 3.6 运行测试

```bash
# 后端
python -m pytest tests -q

# 可选：通过进程内 shim 测试 PostgreSQL SQL 方言（无需真实 Postgres）
OTP_TEST_BACKEND=pg python -m pytest tests -q

# 前端单元测试
cd tests/js && npm install && npm run test:unit
```

---

## 4. 生产部署（API）

你可以在任何 Linux 容器 / VPS 上托管 API。以下介绍：**Koyeb**（文档最完整）以及通用的无 Docker systemd 方案。

### 4.1 Koyeb — 推荐配置

| 字段 | 值 |
| --- | --- |
| Builder | Nixpacks / Buildpacks |
| Build command | *（留空 — 自动识别 `requirements.txt`）* |
| **Start command** | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| Health check | `GET /health` |
| Instances | **Fixed: 1**（使用 SQLite 时尤其重要） |
| Idle / sleep | **关闭** — 冷启动会导致 OTP 邮件丢失 |

示例服务文件：[`deploy/koyeb.yaml`](../../deploy/koyeb.yaml)。

#### 4.1.1 SQLite + 持久化卷

1. 创建一个卷（例如 `otp-data`，1 GB+）
2. 挂载到 `/data`
3. 环境变量：

```bash
DATABASE_PATH=/data/otp_inbox.db
# 不要设置 DATABASE_URL
WORKER_INGEST_TOKEN=<secret>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<secret>
COOKIE_SECURE=1
APP_NAME=OTP Inbox
STORE_RAW_MIME=0
AUTO_DELETE_ENABLED=1
AUTO_DELETE_DAYS=7
```

#### 4.1.2 托管 PostgreSQL

```bash
DATABASE_URL=postgres://user:pass@host:port/dbname
WORKER_INGEST_TOKEN=<secret>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<secret>
COOKIE_SECURE=1
```

设置 `DATABASE_URL` 后，`DATABASE_PATH` 将被忽略。架构在启动时创建。

### 4.2 通用 VPS（方案草图）

```bash
sudo useradd -r -m -d /opt/otp-inbox otp
sudo -u otp -i
cd /opt/otp-inbox
git clone https://github.com/nayumiDEV/Cloudflare-Mailbox.git .
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 填写 secrets；HTTPS 时设 COOKIE_SECURE=1
```

systemd 单元示例：

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

在前面配置 HTTPS（Caddy / nginx / Cloudflare Tunnel）。

---

## 5. Cloudflare Email Routing + Worker

### 5.1 部署 Worker

```bash
cd worker
npx wrangler login
npx wrangler secret put WORKER_INGEST_TOKEN
# 粘贴与 API 的 WORKER_INGEST_TOKEN 相同的值

npx wrangler secret put APP_INGEST_URL
# 粘贴：https://YOUR-API-HOST/api/ingest

npx wrangler deploy
```

确认 `worker/wrangler.toml` 中的名称（`otp-inbox-worker`）和 cron（`*/5 * * * *` 向 `/health` 发送 keep-alive ping）。

手动 ping 测试：打开 `https://<worker>.<account>.workers.dev/ping`

### 5.2 在域名上启用 Email Routing

在 Cloudflare Dashboard → 你的域名 → **Email** → **Email Routing**：

1. 启用 Email Routing（Cloudflare 自动配置 MX / SPF）
2. **Routing rules** → **Catch-all**：
   - Status: Active
   - Action: **Send to Worker**
   - Destination: `otp-inbox-worker`（或你部署的名称）
3. 可选：同时添加特定地址规则

### 5.3 应用内的域名

首次启动空 DB 时，应用会 seed `example.com`（或 `DEFAULT_MAIL_DOMAIN`）。

1. 以管理员身份登录 → **Domains**
2. 添加真实域名，设置访问级别（`public` / `approval_only` / `vip`）
3. 创建邮箱或依靠 **Routing rules**（前缀 / 域名 / 正则 / catch-all）自动分配新地址

### 5.4 端到端邮件测试

1. 发送邮件到 `anything@your-domain.com`，正文包含如 `Your code is 482193`
2. 查看 Worker 日志：`npx wrangler tail`
3. 查看 API 日志确认 ingest
4. 打开 Web UI → Inbox — OTP 应被提取显示

如果 Worker 返回 reject / timeout：API 已关闭、进入休眠，或 `WORKER_INGEST_TOKEN` / `APP_INGEST_URL` 不匹配。

---

## 6. 首次管理员检查清单

- [ ] 已复制一次性 `sk-otp-…` API 密钥
- [ ] 已在 **Account** 中更改管理员密码（env 密码仅在首次创建时生效）
- [ ] 已在 **Domains** 下添加真实域名
- [ ] 已创建至少一个邮箱或路由规则
- [ ] 在有意开放推荐注册之前保持注册**关闭**
- [ ] 已确认 `/health` 返回 `"status":"ok"`
- [ ] 语言切换器正常工作（EN / VI / 中文 / RU）

---

## 7. Developer API（快速上手）

```bash
export API_KEY='sk-otp-...'
curl -s -H "Authorization: Bearer $API_KEY" https://YOUR-HOST/api/dashboard
```

完整参考：[docs/API.md](../API.md)。交互式 Swagger：`https://YOUR-HOST/docs`。

Session cookie + CSRF 仅供浏览器 UI 使用。集成时优先使用 Bearer 密钥。

---

## 8. 环境变量参考

查看 [`.env.example`](../../.env.example) 获取带注释的完整列表。关键变量：

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | 是（首次启动） | 密码最少 10 个字符；仅在创建管理员时生效 |
| `WORKER_INGEST_TOKEN` | 是 | 必须与 Worker secret 匹配 |
| `DATABASE_URL` | 二选一 | Postgres DSN |
| `DATABASE_PATH` | 二选一 | SQLite 路径（默认 `./data/otp_inbox.db`） |
| `COOKIE_SECURE` | 是 | `0` 本地 HTTP，`1` 生产 HTTPS |
| `APP_NAME` | 否 | UI / `/health` 中的品牌名称 |
| `DEFAULT_MAIL_DOMAIN` | 否 | 仅在空 DB 时 seed |
| `REGISTRATION_ENABLED` | 否 | 默认关闭 |
| `REFERRAL_CODE_GENERATION` | 否 | 默认关闭 |
| `AUTO_DELETE_ENABLED` / `AUTO_DELETE_DAYS` | 否 | 后台数据保留 |
| `STORE_RAW_MIME` | 否 | `0` 节省磁盘（小卷推荐） |

---

## 9. 故障排查

| 症状 | 可能原因 | 修复方法 |
| --- | --- | --- |
| Cookie 未在本地存储 | `COOKIE_SECURE=1` 在 HTTP 上 | 本地设置 `COOKIE_SECURE=0` |
| Worker 拒绝邮件 | Token / URL 错误或 API 休眠 | 匹配 secrets；禁用 PaaS 休眠；检查 `/ping` |
| `database is locked` | 多个实例共用同一 SQLite 文件 | 缩减到 **1** 个 worker/实例 |
| 部署后 UI 为空 | CDN 缓存了旧的 `index.html` | 强制刷新；asset hash 随 static 文件更新 |
| 修改 `.env` 中的管理员密码无效 | 账号已存在 | 使用应用内修改密码功能 |
| 注册表单不显示 | 功能已关闭 | 在 admin UI 中启用或设置 `REGISTRATION_ENABLED=1` 后重启（首次启动）/ 在 dashboard 中切换 |
| ImportError `app.main` | 工作目录错误 | 从仓库根目录运行 uvicorn |
| 测试触及真实 DB | 环境未隔离 | Suite 使用临时 DB；同一 shell 中不要将 `DATABASE_PATH` 指向生产环境 |

---

## 10. 更新

```bash
git pull
source .venv/bin/activate   # 或 Windows 对应命令
pip install -r requirements.txt
# 重启 uvicorn / 重新部署 PaaS
cd worker && npx wrangler deploy   # 如果 worker/ 有变更
```

Schema migrations 是增量式的，在启动时运行 — 重新部署是安全的。

---

## 11. 卸载 / 清除本地数据

```bash
# 停止服务器，然后：
rm -rf data/ .venv/
# Windows: Remove-Item -Recurse -Force data, .venv
```

生产环境：按需删除卷 / 数据库和 Cloudflare Worker。
