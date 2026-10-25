# 部署 OTP Inbox 到 Koyeb

**语言：** [English](../en/DEPLOY.md) · [Tiếng Việt](../vi/DEPLOY.md) · [中文](DEPLOY.md) · [Русский](../ru/DEPLOY.md)

后端同时支持 **SQLite**（配合 Koyeb 持久化卷）和 **PostgreSQL**。

---

## 1. 构建 / 运行配置

| Koyeb 字段 | 值 |
| --- | --- |
| Builder | `Nixpacks`（或 `Buildpacks`） |
| Build command | *（留空 — 自动识别 `requirements.txt`）* |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| Instance size | `nano`（512 MB RAM，2 000 MB 磁盘） |
| Health check path | `/health` |
| Port | Koyeb 自动设置 `$PORT`（默认 8000） |
| Scale / Instances | **Fixed: 1**（Min 1，Max 1） |

### 为什么只能运行 1 个实例？

SQLite 将数据存储在持久化卷上的单一文件（`/data/otp_inbox.db`）中。
两个实例同时写入会导致 `database is locked` 错误或数据损坏。
请始终使用 **Fixed: 1**，禁用 Sleep/Idle，并保持 `--workers 1`。

### 滚动部署时短暂出现 2 个实例

Koyeb 执行零停机滚动部署：新容器启动并通过 `/health` 检查后，旧容器才会关闭。
每次重新部署时，约有 30–60 秒会看到 2 个实例 — 这是正常现象。

### 禁用 Sleep / Scale-to-zero

若启用 Sleep，无流量时 Koyeb 会让容器进入休眠。当新邮件到达时，Cloudflare Worker
调用 `/api/ingest`，但冷启动（15–40 秒）会导致 Worker 超时，OTP 邮件因此丢失。
**始终保持容器唤醒状态**，使用 Fixed: 1（min ≥ 1）。

---

## 2. 在 Koyeb 上使用 SQLite

### 步骤 A — 创建并挂载持久化卷（必须）

若不挂载卷，每次重新部署都会清空 SQLite 数据库。

1. 在 Koyeb 控制台进入 **Volumes** → **Create Volume**。
2. 命名为 `otp-data`（1 GB 作为起点即可）。
3. 在 Service 设置 → **Volumes**：
   - Volume: `otp-data`
   - Mount path: `/data`

### 步骤 B — 环境变量

```bash
# SQLite 文件路径（位于已挂载的卷中）
DATABASE_PATH=/data/otp_inbox.db

# 与 Cloudflare Worker 共享的密钥（须与 wrangler.toml 中的 WORKER_INGEST_TOKEN 一致）
WORKER_INGEST_TOKEN=<随机密钥>               # 标记为 Secret

# 初始管理员账号（仅首次启动时生效）
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<管理员密码>                  # 标记为 Secret

# Cookie 安全（Koyeb 使用 HTTPS，设为 1）
COOKIE_SECURE=1

# 应用名称与注册
APP_NAME=OTP Inbox
REGISTRATION_ENABLED=1
REFERRAL_CODE_GENERATION=1

# nano 套餐资源优化（512 MB RAM / 2 000 MB 磁盘）
STORE_RAW_MIME=0              # 不存储重复的 MIME 原始数据 — 每封邮件节省 60–80% 磁盘
STORE_ATTACHMENTS=1           # 若只需接收 OTP 可设为 0
MAX_ATTACHMENT_BYTES=1048576  # 单个附件上限 1 MB
MAX_ATTACHMENTS_TOTAL=2097152 # 每封邮件附件总上限 2 MB

# 自动删除旧邮件
AUTO_DELETE_ENABLED=1         # 后台任务每小时运行一次
AUTO_DELETE_DAYS=7            # 删除超过 N 天的邮件
```

> **不要设置** `DATABASE_URL` 或 `POSTGRES_URL` — 若存在，应用将切换为 PostgreSQL 模式。

---

## 2.1 PostgreSQL（可选）

添加一个变量即可从 SQLite 切换到 PostgreSQL：

```bash
DATABASE_URL=postgres://user:password@host/dbname
```

当 `DATABASE_URL` 存在时，应用完全忽略 `DATABASE_PATH`。

**推荐附加配置：**

```bash
APP_NAME=OTP Inbox
SESSION_DAYS=7
REGISTRATION_ENABLED=1
REFERRAL_CODE_GENERATION=1
REFERRAL_CODE_TTL_DAYS=0
REFERRAL_MAX_USES_DEFAULT=1
```

请在 Koyeb 控制台将 `DATABASE_URL` 标记为 Secret，以防密码出现在部署日志中。

---

## 3. 首次部署

每次启动时都会自动创建（或迁移）数据库结构 — 重新部署是安全的。

1. 设置所有环境变量并部署。
2. 查看日志：
   ```
   [OTP Inbox] Admin created
   [OTP Inbox] username: admin
   [OTP Inbox] API key (shown once): sk-otp-...
   ```
   **立即复制 API 密钥 — 之后不会再显示。**
3. 使用 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 登录 Koyeb 提供的 URL。
4. 验证 `/health` 返回 `{"status":"ok",...}`。

---

## 4. Cloudflare 邮件路由与 Worker

### 4.1 部署 Worker

```bash
cd worker

# 设置密钥（须与上方环境变量一致）
npx wrangler secret put WORKER_INGEST_TOKEN
npx wrangler secret put APP_INGEST_URL
# APP_INGEST_URL 的值：https://<your-app>.koyeb.app/api/ingest

npx wrangler deploy
```

`wrangler.toml` 包含一个 cron 触发器（`*/5 * * * *`），每 5 分钟 ping 一次
`/health`，防止冷启动。

### 4.2 在 Cloudflare 配置邮件路由

1. 控制台 → 域名 → **Email** → **Email Routing** → **Routing rules**。
2. 开启 Email Routing（Cloudflare 将自动添加 MX/SPF 记录）。
3. **Catch-all 规则**：
   - Status: **Active**
   - Action: **Send to Worker**
   - Destination: 已部署的 Worker（`otp-inbox-worker`）

---

## 5. 邀请码系统

注册默认为关闭状态，由三个独立开关控制：

| 控制项 | 位置 | 默认 | 作用 |
| --- | --- | --- | --- |
| `registration_enabled` | 控制台开关 / 环境变量 | **关** | 显示注册表单 |
| `referral_code_generation` | 控制台开关 / 环境变量 | **关** | 允许用户生成邀请码 |
| `can_generate_codes` | 用户单独设置 | **关** | 实际赋予每位用户的权限 |

生成的邀请码**仅显示一次**，仅以 SHA-256 摘要形式存储。
若丢失，请生成新码 — 旧码将自动失效。

---

## 6. 本地开发

```bash
python -m venv .venv
# Windows：
.venv\Scripts\activate
# macOS / Linux：
source .venv/bin/activate

pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
# 设置 ADMIN_PASSWORD、WORKER_INGEST_TOKEN、COOKIE_SECURE=0

uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

若未设置 `DATABASE_URL`，应用将使用 `./data/otp_inbox.db` — 无需数据库服务器。

```bash
python -m pytest tests -q                     # 后端
cd tests/js && npm install && npm test        # 前端（需要 Node 18+）
```

---

完整权威参考文档：[../DEPLOY.md](../DEPLOY.md)
