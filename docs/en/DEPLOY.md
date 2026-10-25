# Production deploy

**Languages:** [English](DEPLOY.md) · [Tiếng Việt](../vi/DEPLOY.md) · [中文](../zh/DEPLOY.md) · [Русский](../ru/DEPLOY.md)

Canonical guide: **[../DEPLOY.md](../DEPLOY.md)**

Run on any Linux host, container, or PaaS. Use **one** uvicorn worker and **one**
replica with SQLite; attach a persistent volume for `DATABASE_PATH`. Prefer
always-on (no sleep) so Cloudflare ingest does not cold-start. Optional
`DISK_LIMIT_MB` sets the Admin → System usage bar to your own disk size.
