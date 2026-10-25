# OTP Inbox

**Язык:** [English](README.md) · [Tiếng Việt](README.vi.md) · [中文](README.zh.md) · [Русский](README.ru.md)

Самостоятельно размещаемый **catch-all почтовый ящик** с автоматическим извлечением OTP, многопользовательским контролем доступа, регистрацией по реферальным кодам и API для разработчиков.

Входящая почта принимается [Cloudflare Email Routing](https://developers.cloudflare.com/email-routing/) Worker и сохраняется бэкендом на FastAPI (SQLite или PostgreSQL). Web-интерфейс в стиле Gmail позволяет просматривать почтовые ящики, искать сообщения и управлять правилами маршрутизации.

[![CI](https://github.com/nayumiDEV/Cloudflare-Mailbox/actions/workflows/ci.yml/badge.svg)](https://github.com/nayumiDEV/Cloudflare-Mailbox/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## Возможности

- **Catch-all приём** через Cloudflare Worker → `POST /api/ingest`
- **Извлечение OTP** из темы/тела с фильтрацией ложных срабатываний
- **Почтовые ящики и правила маршрутизации** (точные, по префиксу, домену, regex, catch-all)
- **Многопользовательский ACL** — владелец, общий доступ, переопределения администратора
- **Реферальная регистрация** — по умолчанию закрыта; коды хранятся в виде хеша
- **API для разработчиков** — Bearer API-ключи для чтения/записи без CSRF
- **Полнотекстовый поиск** (SQLite FTS5 / PostgreSQL `tsvector`)
- **Хранение с ограничением срока** — опциональное автоудаление старых сообщений
- **Нет шага сборки** — статические `app.css` / `app.js` с content-hash для инвалидации кеша
- **Интернационализация** — UI поддерживает EN / VI / ZH / RU через переключатель языков

## Архитектура

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
FastAPI  (app/main.py)  ·  SQLite или PostgreSQL
        │
        ▼
Web UI  (static/)  +  REST API  (/api/*)
```

## Быстрый старт (локально)

**Требования:** Python 3.11+, Node 18+ (только для тестов фронтенда)

Полное пошаговое руководство: **[docs/ru/SETUP.md](docs/ru/SETUP.md)**.

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Unix:     source .venv/bin/activate

pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
# Установите ADMIN_PASSWORD, WORKER_INGEST_TOKEN; COOKIE_SECURE=0 для обычного HTTP

uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Откройте http://127.0.0.1:8000 и войдите с `ADMIN_USERNAME` / `ADMIN_PASSWORD`.

При первом запуске в логах печатается одноразовый API-ключ администратора (`sk-otp-…`) —
скопируйте его немедленно; он больше не будет показан.

```bash
python -m pytest tests -q
```

## Развёртывание в production

Смотрите **[docs/ru/DEPLOY.md](docs/ru/DEPLOY.md)** для получения информации о:

- Koyeb (SQLite-том или управляемый PostgreSQL)
- Cloudflare Email Routing + секреты Worker
- Настройка ресурсов / хранения данных
- Переключатели реферальной функции

Точка входа Worker: [`worker/`](worker/) (`wrangler.toml` + `worker.js`).

## Конфигурация

Скопируйте [`.env.example`](.env.example). Важные переменные:

| Переменная | Назначение |
| --- | --- |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Начальный администратор (пароль применяется только при первом создании) |
| `WORKER_INGEST_TOKEN` | Общий секрет для `/api/ingest` |
| `DATABASE_URL` | PostgreSQL DSN — если не задан, используется SQLite по пути `DATABASE_PATH` |
| `APP_NAME` | Название бренда в UI и `/health` |
| `REGISTRATION_ENABLED` | Открыть форму регистрации (всё равно требует реферальный код) |
| `AUTO_DELETE_ENABLED` / `AUTO_DELETE_DAYS` | Фоновое автоматическое хранение сообщений |

## API

Полный REST-справочник: **[docs/ru/API.md](docs/ru/API.md)**

Интерактивный Swagger UI: `GET /docs` на работающем экземпляре.

```bash
# Проверка работоспособности
curl -s https://your-host/health

# API для разработчиков
curl -s -H "Authorization: Bearer sk-otp-YOUR_KEY" https://your-host/api/dashboard
```

## Структура проекта

```text
app/                 Python-пакет (FastAPI)
  main.py            HTTP API + бизнес-логика  →  uvicorn app.main:app
  db.py              Вспомогательные функции подключения SQLite / PostgreSQL
  schema.py          DDL схемы (оба бэкенда)
  seed.py            Опциональные демо-данные  →  python -m app.seed
static/              Web UI (index.html, app.js, app.css)
worker/              Cloudflare Email Worker
tests/               pytest + JS unit/e2e тесты
docs/                Документация (SETUP, DEPLOY, API)
deploy/
  koyeb.yaml         Пример конфигурации сервиса Koyeb
.env.example         Копировать → .env
```

## Безопасность

- Сообщайте об уязвимостях конфиденциально — см. [SECURITY.md](SECURITY.md)
- Никогда не коммитьте `.env`, `data/*.db` или секреты Wrangler
- Держите регистрацию и генерацию реферальных кодов отключёнными до тех пор, пока они не нужны

## Участие в разработке

Смотрите [CONTRIBUTING.md](CONTRIBUTING.md). Пожалуйста, соблюдайте
[Кодекс поведения](CODE_OF_CONDUCT.md).

## Лицензия

[MIT](LICENSE)
