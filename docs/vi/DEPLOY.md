# Triển khai production

**Ngôn ngữ:** [English](../en/DEPLOY.md) · [Tiếng Việt](DEPLOY.md) · [中文](../zh/DEPLOY.md) · [Русский](../ru/DEPLOY.md)

Hướng dẫn đầy đủ: **[../DEPLOY.md](../DEPLOY.md)**

Chạy trên mọi Linux host / container / PaaS. Với SQLite: **1** worker uvicorn và
**1** replica; gắn volume bền cho `DATABASE_PATH`. Tắt sleep/scale-to-zero để
ingest không bị cold-start. `DISK_LIMIT_MB` (tuỳ chọn) đặt hạn mức đĩa của
**bạn** cho thanh % ở Admin → Hệ thống — không gắn cứng nhà cung cấp nào.
