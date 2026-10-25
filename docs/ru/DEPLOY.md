# Развёртывание OTP Inbox на Koyeb

**Язык:** [English](../en/DEPLOY.md) · [Tiếng Việt](../vi/DEPLOY.md) · [中文](../zh/DEPLOY.md) · [Русский](DEPLOY.md)

Бэкенд поддерживает как **SQLite** (с постоянным томом Koyeb), так и **PostgreSQL**.

---

## 1. Настройки сборки / запуска

| Поле Koyeb | Значение |
| --- | --- |
| Builder | `Nixpacks` (или `Buildpacks`) |
| Build command | *(оставить пустым — автоматически определяет `requirements.txt`)* |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| Instance size | `nano` (512 МБ RAM, 2 000 МБ диска) |
| Health check path | `/health` |
| Port | Koyeb автоматически устанавливает `$PORT` (по умолчанию 8000) |
| Scale / Instances | **Fixed: 1** (Min 1, Max 1) |

### Почему нужно запускать ровно 1 экземпляр?

SQLite хранит данные в единственном файле на постоянном томе (`/data/otp_inbox.db`).
При одновременной записи двумя экземплярами возникнут ошибки `database is locked`
или повреждение данных. Всегда используйте **Fixed: 1**, отключайте Sleep/Idle
и сохраняйте `--workers 1`.

### Rolling-деплой кратковременно показывает 2 экземпляра

Koyeb выполняет деплой без простоя: новый контейнер запускается и проходит
проверку `/health` прежде, чем старый будет остановлен. Во время каждого
передеплоя около 30–60 секунд видны 2 экземпляра — это штатное поведение.

### Отключите Sleep / Scale-to-zero

Если включён Sleep и трафика нет, Koyeb переводит контейнер в режим сна.
При поступлении письма Cloudflare Worker вызывает `/api/ingest`, но холодный
старт (15–40 с) приводит к таймауту Worker и потере OTP-письма.
**Всегда держите контейнер включённым** с Fixed: 1 (min ≥ 1).

---

## 2. SQLite на Koyeb

### Шаг А — Создание и подключение постоянного тома (обязательно)

Без тома при каждом передеплое база данных SQLite будет удаляться.

1. В консоли Koyeb перейдите в **Volumes** → **Create Volume**.
2. Назовите том `otp-data` (1 ГБ для начала достаточно).
3. В настройках сервиса → **Volumes**:
   - Volume: `otp-data`
   - Mount path: `/data`

### Шаг Б — Переменные окружения

```bash
# Путь к файлу SQLite (внутри подключённого тома)
DATABASE_PATH=/data/otp_inbox.db

# Общий секрет с Cloudflare Worker (должен совпадать с WORKER_INGEST_TOKEN в wrangler.toml)
WORKER_INGEST_TOKEN=<случайный-секрет>       # отметить как Secret

# Учётная запись администратора (применяется только при первом запуске)
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<пароль-администратора>       # отметить как Secret

# Безопасность cookie (Koyeb использует HTTPS, ставьте 1)
COOKIE_SECURE=1

# Название приложения и регистрация
APP_NAME=OTP Inbox
REGISTRATION_ENABLED=1
REFERRAL_CODE_GENERATION=1

# Оптимизация ресурсов для nano (512 МБ RAM / 2 000 МБ диска)
STORE_RAW_MIME=0              # не хранить дублирующий сырой MIME — экономит 60–80 % диска на письмо
STORE_ATTACHMENTS=1           # установите 0, если нужны только OTP-коды
MAX_ATTACHMENT_BYTES=1048576  # ограничение на вложение: 1 МБ
MAX_ATTACHMENTS_TOTAL=2097152 # суммарный лимит вложений на письмо: 2 МБ

# Автоматическое удаление старых писем
AUTO_DELETE_ENABLED=1         # фоновая задача запускается каждый час
AUTO_DELETE_DAYS=7            # удалять письма старше N дней
```

> **Не устанавливайте** `DATABASE_URL` или `POSTGRES_URL` при использовании SQLite —
> их наличие переключает приложение в режим PostgreSQL.

---

## 2.1 PostgreSQL (опционально)

Добавьте одну переменную, чтобы переключиться с SQLite на PostgreSQL:

```bash
DATABASE_URL=postgres://user:password@host/dbname
```

При наличии `DATABASE_URL` приложение полностью игнорирует `DATABASE_PATH`.

**Рекомендуемые дополнительные настройки:**

```bash
APP_NAME=OTP Inbox
SESSION_DAYS=7
REGISTRATION_ENABLED=1
REFERRAL_CODE_GENERATION=1
REFERRAL_CODE_TTL_DAYS=0
REFERRAL_MAX_USES_DEFAULT=1
```

Пометьте `DATABASE_URL` как Secret в дашборде Koyeb, чтобы пароль не отображался
в логах деплоя.

---

## 3. Первый деплой

Схема базы данных создаётся (или мигрирует) автоматически при каждом запуске —
передеплой безопасен.

1. Установите все переменные окружения и запустите деплой.
2. Проверьте логи:
   ```
   [OTP Inbox] Admin created
   [OTP Inbox] username: admin
   [OTP Inbox] API key (shown once): sk-otp-...
   ```
   **Немедленно скопируйте API-ключ — он больше никогда не будет показан.**
3. Войдите в систему по URL Koyeb с `ADMIN_USERNAME` / `ADMIN_PASSWORD`.
4. Убедитесь, что `/health` возвращает `{"status":"ok",...}`.

---

## 4. Cloudflare Email Routing и Worker

### 4.1 Деплой Worker

```bash
cd worker

# Установите секреты (должны совпадать с переменными окружения выше)
npx wrangler secret put WORKER_INGEST_TOKEN
npx wrangler secret put APP_INGEST_URL
# Значение APP_INGEST_URL: https://<your-app>.koyeb.app/api/ingest

npx wrangler deploy
```

`wrangler.toml` содержит cron-триггер (`*/5 * * * *`), который каждые 5 минут
делает ping на `/health`, предотвращая холодный старт.

### 4.2 Настройка Email Routing в Cloudflare

1. Дашборд → ваш домен → **Email** → **Email Routing** → **Routing rules**.
2. Включите Email Routing (Cloudflare автоматически добавит MX/SPF-записи).
3. **Catch-all правило**:
   - Status: **Active**
   - Action: **Send to Worker**
   - Destination: ваш задеплоенный Worker (`otp-inbox-worker`)

---

## 5. Реферальная система

По умолчанию регистрация закрыта. Тремя независимыми параметрами управляется доступ:

| Параметр | Расположение | По умолчанию | Действие |
| --- | --- | --- | --- |
| `registration_enabled` | Переключатель / env | **выкл.** | Показывать форму регистрации |
| `referral_code_generation` | Переключатель / env | **выкл.** | Разрешить создание кодов |
| `can_generate_codes` | Настройки пользователя | **выкл.** | Разрешение для конкретного пользователя |

Сгенерированный код отображается **только один раз** и хранится только в виде
SHA-256-дайджеста. Если код потерян — создайте новый; старый автоматически
деактивируется.

---

## 6. Локальная разработка

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
# Установите ADMIN_PASSWORD, WORKER_INGEST_TOKEN, COOKIE_SECURE=0

uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Без `DATABASE_URL` приложение использует `./data/otp_inbox.db` — сервер базы данных не нужен.

```bash
python -m pytest tests -q                     # бэкенд
cd tests/js && npm install && npm test        # фронтенд (нужен Node 18+)
```

---

Полная каноническая документация: [../DEPLOY.md](../DEPLOY.md)
