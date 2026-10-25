# 生产部署

**语言：** [English](../en/DEPLOY.md) · [Tiếng Việt](../vi/DEPLOY.md) · [中文](DEPLOY.md) · [Русский](../ru/DEPLOY.md)

完整指南：**[../DEPLOY.md](../DEPLOY.md)**

可部署到任意 Linux 主机、容器或 PaaS。使用 SQLite 时保持 **1** 个 uvicorn worker
与 **1** 个副本，并为 `DATABASE_PATH` 挂载持久卷。关闭休眠 / 缩零，避免邮件接入
冷启动。可选 `DISK_LIMIT_MB` 按**你自己的**磁盘容量设置管理页用量条，无内置厂商限额。
