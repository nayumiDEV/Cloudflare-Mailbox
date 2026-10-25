# OTP Inbox — API 概览（中文）

**语言：** [English](../en/API.md) · [Tiếng Việt](../vi/API.md) · [中文](API.md) · [Русский](../ru/API.md)

完整参考文档（1 300+ 行）：**[../API.md](../API.md)**
交互式 Swagger UI：在运行中的实例上访问 `GET /docs`。

---

## 认证

大多数 `/api/*` 端点同时接受 **API 密钥**或 **Session Cookie**。

| 机制 | 使用方式 | 适用场景 |
| --- | --- | --- |
| **API 密钥**（推荐用于集成） | `Authorization: Bearer sk-otp-...` 请求头 | 所有 `/api/*` 读写操作（下方列出的例外除外）。无需 CSRF。 |
| **Session Cookie** | `otp_session` Cookie（HttpOnly，SameSite=Lax） | Web UI |
| **CSRF Token** | `X-CSRF-Token: <token>` 请求头 | 仅在使用 Session Cookie 进行 `POST` / `PATCH` / `DELETE` 时需要 |
| **Worker Token** | `X-Worker-Token: <WORKER_INGEST_TOKEN>` 请求头 | 仅限 `POST /api/ingest` |

**不接受 API 密钥的端点：**

| 端点 | 认证方式 |
| --- | --- |
| `POST /api/login`，`POST /api/register` | 用户名 + 密码（+ 邀请码） |
| `POST /api/logout` | Session Cookie |
| `GET /api/auth/config`，`GET /health` | 公开访问 |
| `POST /api/ingest` | Worker Token |

同时发送 Cookie 和 Bearer Token 时，**Bearer Token 优先**。
API 密钥默认速率限制：**120 请求 / 60 秒 / 密钥**。

### 快速示例

```bash
# 健康检查（公开）
curl -s https://your-host/health

# 通过 API 密钥访问 Dashboard
curl -s -H "Authorization: Bearer sk-otp-YOUR_KEY" \
     https://your-host/api/dashboard

# 创建邮箱
curl -s -X POST \
     -H "Authorization: Bearer sk-otp-YOUR_KEY" \
     -H "Content-Type: application/json" \
     -d '{"address":"otp-test@example.com"}' \
     https://your-host/api/mailboxes/create
```

---

## 主要端点概览

| 分组 | 示例端点 | 说明 |
| --- | --- | --- |
| 健康检查 | `GET /health` | 返回应用版本、搜索模式、功能开关 |
| 登录 / 登出 | `POST /api/login` · `POST /api/logout` | 基于 Session；返回 CSRF Token |
| 注册 | `POST /api/register` | 启用时需要有效邀请码 |
| 控制台 | `GET /api/dashboard` | 消息统计、邮箱列表 |
| 消息列表 | `GET /api/messages` | 分页；支持 `q=` 全文搜索 |
| 单条消息 | `GET /api/messages/{id}` | 若已提取 OTP，包含 OTP 字段 |
| 附件 | `GET /api/attachments/{id}` | 二进制下载 |
| 邮箱 | `GET /api/mailboxes` · `POST /api/mailboxes/create` | |
| API 密钥 | `POST /api/keys/create` · `DELETE /api/keys/{id}` | 开发者 API 密钥 |
| 管理 — 用户 | `GET /api/admin/users` | 仅限管理员 |
| 管理 — 路由规则 | `GET /api/admin/routing-rules` | |
| 管理 — 邀请码 | `POST /api/admin/referral-codes/generate` | |
| 邮件摄取 | `POST /api/ingest` | 由 Cloudflare Worker 调用；需要 Worker Token |

---

## 错误码

所有错误均遵循以下格式：

```json
{"detail": "可读错误信息"}
```

常见 HTTP 状态码：

| 状态码 | 含义 |
| --- | --- |
| `400` | 请求无效 / 验证错误 |
| `401` | 凭证缺失或无效 |
| `403` | 权限不足 |
| `404` | 资源未找到 |
| `409` | 冲突（例如邮箱地址重复） |
| `429` | 超出速率限制 |
| `500` | 服务器错误 |

---

如需查看完整的端点文档（包含所有请求 / 响应 Schema、查询参数及 curl 示例），
请参阅 **[../API.md](../API.md)**。
