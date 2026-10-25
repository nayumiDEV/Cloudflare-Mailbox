# Продакшен-деплой

**Языки:** [English](../en/DEPLOY.md) · [Tiếng Việt](../vi/DEPLOY.md) · [中文](../zh/DEPLOY.md) · [Русский](DEPLOY.md)

Полное руководство: **[../DEPLOY.md](../DEPLOY.md)**

Запускайте на любом Linux-хосте, в контейнере или PaaS. Для SQLite — **1** worker
uvicorn и **1** реплика; примонтируйте том для `DATABASE_PATH`. Отключите sleep /
scale-to-zero, чтобы ingest не ловил cold start. Опциональный `DISK_LIMIT_MB`
задаёт лимит диска **вашего** хоста для полосы в Admin → System.
