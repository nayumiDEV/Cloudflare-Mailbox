# Полное руководство по установке

**Язык:** [English](../SETUP.md) · [Tiếng Việt](../vi/SETUP.md) · [中文](../zh/SETUP.md) · [Русский](SETUP.md)

Пошаговое руководство по запуску **OTP Inbox** (Cloudflare Mailbox) с нуля до готового к production catch-all OTP-почтового ящика.

Связанная документация:

| Документ | Назначение |
| --- | --- |
| [DEPLOY](DEPLOY.md) | Настройка PaaS / Koyeb, хранение данных, keep-alive |
| [API](API.md) | Обзор REST (полный справочник: [API.md](../API.md)) |
| [SECURITY.md](../../SECURITY.md) | Отчёты об уязвимостях и рекомендации по безопасности |

---

## 0. Что вы создаёте

```text
Internet mail  →  Cloudflare Email Routing (catch-all)
                         │
                         ▼
               Cloudflare Worker  (worker/)
                         │  POST /api/ingest + X-Worker-Token
                         ▼
               FastAPI app  (app/main.py)  +  SQLite или PostgreSQL
                         │
                         ▼
               Web UI  (static/)  +  Developer API
```

Вам потребуется:

1. Домен на Cloudflare (для Email Routing)
2. Место для запуска API (ноутбук для тестирования или Koyeb / любой Linux-хост для production)
3. Python 3.11+ на этом хосте

---

## 1. Требования

### 1.1 Программное обеспечение

| Инструмент | Версия | Зачем |
| --- | --- | --- |
| Python | **3.11+** (рекомендуется 3.12) | Backend FastAPI |
| pip | входит в Python | Установка зависимостей |
| Git | любая свежая версия | Клонирование репозитория |
| Node.js | **18+** (опционально) | Только для frontend unit-тестов |
| Аккаунт Cloudflare | бесплатного тарифа достаточно | Email Routing + Worker |
| Wrangler CLI | через `npx wrangler` | Деплой Worker |

Проверка:

```bash
python --version    # >= 3.11
git --version
node --version      # опционально
```

### 1.2 Аккаунты и секреты, которые нужно создать

Храните их в менеджере паролей — никогда не коммитьте:

| Секрет | Используется |
| --- | --- |
| `ADMIN_PASSWORD` | Первый вход администратора |
| `WORKER_INGEST_TOKEN` | Общий между Worker ↔ API |
| Admin API key `sk-otp-…` | Выводится **один раз** при первом запуске |
| Cloudflare API / Wrangler login | Деплой Worker |
| `DATABASE_URL` (опционально) | Управляемый Postgres |

Сгенерировать надёжный токен:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

---

## 2. Получить код

```bash
git clone https://github.com/nayumiDEV/Cloudflare-Mailbox.git
cd Cloudflare-Mailbox
```

### 2.1 Структура репозитория

```text
Cloudflare-Mailbox/
├── app/                 # Python-пакет (FastAPI)
│   ├── main.py          # HTTP API + бизнес-логика  →  uvicorn app.main:app
│   ├── db.py            # Адаптер SQLite / PostgreSQL
│   ├── schema.py        # DDL для обоих бэкендов
│   └── seed.py          # Опциональные демо-данные
├── static/              # Web UI (без шага сборки)
│   ├── index.html
│   ├── app.js / app.css
│   ├── i18n.js
│   └── locales/         # en, vi, zh, ru
├── worker/              # Cloudflare Email Worker
│   ├── worker.js
│   └── wrangler.toml
├── tests/               # pytest + JS unit-тесты
├── docs/                # Данная документация
├── deploy/koyeb.yaml    # Пример конфигурации Koyeb
├── requirements.txt
├── requirements-dev.txt
└── .env.example         # Копировать → .env
```

---

## 3. Локальная разработка (SQLite — без конфигурации)

### 3.1 Создание виртуального окружения

**Windows (PowerShell):**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt
```

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt
```

### 3.2 Настройка окружения

```bash
# Windows
copy .env.example .env

# macOS / Linux
cp .env.example .env
```

Отредактируйте `.env` — **минимум для локального HTTP:**

```bash
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-me-to-at-least-10-chars
WORKER_INGEST_TOKEN=paste-the-token-urlsafe-48-output-here

# SQLite-файл (создаётся автоматически)
DATABASE_PATH=./data/otp_inbox.db

# Обязательно для http://127.0.0.1 (браузеры отклоняют Secure-cookie на обычном HTTP)
COOKIE_SECURE=0

APP_NAME=OTP Inbox
REGISTRATION_ENABLED=0
REFERRAL_CODE_GENERATION=0
```

Каждая переменная документирована в `.env.example`. Не коммитьте `.env`.

### 3.3 Запуск API

Всегда запускайте команды из **корня репозитория**:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Откройте http://127.0.0.1:8000

На **чистой** базе данных лог сервера выведет:

```text
[OTP Inbox] Admin created
[OTP Inbox] username: admin
[OTP Inbox] API key (shown once): sk-otp-................
```

**Немедленно скопируйте API-ключ** — он больше никогда не будет показан. Позже его можно сменить в настройках аккаунта в интерфейсе.

Войдите с `ADMIN_USERNAME` / `ADMIN_PASSWORD`.

### 3.4 Проверка работоспособности

```bash
curl -s http://127.0.0.1:8000/health
```

Ожидаемый ответ:

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

### 3.5 Опциональные демо-данные

```bash
python -m app.seed
```

Создаёт тестовых пользователей / почтовые ящики / OTP-сообщения (пароли см. в выводе скрипта).

### 3.6 Запуск тестов

```bash
# Backend
python -m pytest tests -q

# Опционально: тестирование диалекта PostgreSQL через внутрипроцессный shim (реальный Postgres не нужен)
OTP_TEST_BACKEND=pg python -m pytest tests -q

# Frontend unit-тесты
cd tests/js && npm install && npm run test:unit
```

---

## 4. Деплой в production (API)

API можно разместить на любом Linux-контейнере / VPS. Ниже описаны **Koyeb** (подробная документация) и общий sketch на systemd без Docker.

### 4.1 Koyeb — рекомендуемые настройки

| Поле | Значение |
| --- | --- |
| Builder | Nixpacks / Buildpacks |
| Build command | *(оставить пустым — автоматически определяет `requirements.txt`)* |
| **Start command** | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| Health check | `GET /health` |
| Instances | **Fixed: 1** (особенно при использовании SQLite) |
| Idle / sleep | **Выключить** — холодный старт приводит к потере OTP-писем |

Пример файла сервиса: [`deploy/koyeb.yaml`](../../deploy/koyeb.yaml).

#### 4.1.1 SQLite + постоянный том

1. Создайте том (например `otp-data`, 1 ГБ+)
2. Примонтируйте в `/data`
3. Переменные окружения:

```bash
DATABASE_PATH=/data/otp_inbox.db
# НЕ устанавливайте DATABASE_URL
WORKER_INGEST_TOKEN=<secret>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<secret>
COOKIE_SECURE=1
APP_NAME=OTP Inbox
STORE_RAW_MIME=0
AUTO_DELETE_ENABLED=1
AUTO_DELETE_DAYS=7
```

#### 4.1.2 Управляемый PostgreSQL

```bash
DATABASE_URL=postgres://user:pass@host:port/dbname
WORKER_INGEST_TOKEN=<secret>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<secret>
COOKIE_SECURE=1
```

При установленном `DATABASE_URL` переменная `DATABASE_PATH` игнорируется. Схема создаётся при запуске.

### 4.2 Универсальный VPS (набросок)

```bash
sudo useradd -r -m -d /opt/otp-inbox otp
sudo -u otp -i
cd /opt/otp-inbox
git clone https://github.com/nayumiDEV/Cloudflare-Mailbox.git .
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # заполните секреты; COOKIE_SECURE=1 за HTTPS
```

Пример юнита systemd:

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

Поместите HTTPS перед сервисом (Caddy / nginx / Cloudflare Tunnel).

---

## 5. Cloudflare Email Routing + Worker

### 5.1 Деплой Worker

```bash
cd worker
npx wrangler login
npx wrangler secret put WORKER_INGEST_TOKEN
# вставьте ТО ЖЕ значение, что и WORKER_INGEST_TOKEN у API

npx wrangler secret put APP_INGEST_URL
# вставьте: https://YOUR-API-HOST/api/ingest

npx wrangler deploy
```

Убедитесь, что в `worker/wrangler.toml` указано правильное имя (`otp-inbox-worker`) и cron (`*/5 * * * *` — keep-alive ping на `/health`).

Ручной тест: откройте `https://<worker>.<account>.workers.dev/ping`

### 5.2 Включение Email Routing для домена

В Cloudflare Dashboard → ваш домен → **Email** → **Email Routing**:

1. Включите Email Routing (Cloudflare автоматически настроит MX / SPF)
2. **Routing rules** → **Catch-all**:
   - Status: Active
   - Action: **Send to Worker**
   - Destination: `otp-inbox-worker` (или имя, под которым вы задеплоили)
3. Опционально: добавьте правила для конкретных адресов

### 5.3 Домены внутри приложения

При первом запуске с пустой БД приложение создаёт `example.com` (или `DEFAULT_MAIL_DOMAIN`).

1. Войдите как admin → **Domains**
2. Добавьте реальные домены, установите уровень доступа (`public` / `approval_only` / `vip`)
3. Создайте почтовые ящики или используйте **Routing rules** (prefix / domain / regex / catch-all) для автоматического назначения новых адресов

### 5.4 Сквозной тест почты

1. Отправьте письмо на `anything@your-domain.com` с телом вроде `Your code is 482193`
2. Следите за логами Worker: `npx wrangler tail`
3. Проверяйте логи API на предмет ingest
4. Откройте Web UI → Inbox — OTP должен быть извлечён

Если Worker возвращает reject / timeout: API выключен, спит или `WORKER_INGEST_TOKEN` / `APP_INGEST_URL` не совпадают.

---

## 6. Чеклист первого администратора

- [ ] Скопирован одноразовый API-ключ `sk-otp-…`
- [ ] Пароль администратора изменён в **Account** (пароль из env применяется только при первом создании)
- [ ] Добавлены реальные домены в **Domains**
- [ ] Создан хотя бы один почтовый ящик или правило маршрутизации
- [ ] Регистрация оставлена **выключенной** до момента, когда вы намеренно откроете реферальную регистрацию
- [ ] Подтверждено, что `/health` возвращает `"status":"ok"`
- [ ] Переключатель языков работает (EN / VI / 中文 / RU)

---

## 7. Developer API (быстрый старт)

```bash
export API_KEY='sk-otp-...'
curl -s -H "Authorization: Bearer $API_KEY" https://YOUR-HOST/api/dashboard
```

Полный справочник: [docs/API.md](../API.md). Интерактивный Swagger: `https://YOUR-HOST/docs`.

Session-cookie + CSRF — только для браузерного UI. Для интеграций используйте Bearer-ключи.

---

## 8. Справочник переменных окружения

Полный список с комментариями: [`.env.example`](../../.env.example). Критически важные:

| Переменная | Обязательно | Примечания |
| --- | --- | --- |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | да (первый запуск) | Минимум 10 символов; применяется только при создании admin |
| `WORKER_INGEST_TOKEN` | да | Должен совпадать с секретом Worker |
| `DATABASE_URL` | одно из двух | Postgres DSN |
| `DATABASE_PATH` | одно из двух | Путь SQLite (по умолчанию `./data/otp_inbox.db`) |
| `COOKIE_SECURE` | да | `0` для локального HTTP, `1` для production HTTPS |
| `APP_NAME` | нет | Брендинг в UI / `/health` |
| `DEFAULT_MAIL_DOMAIN` | нет | Создаётся только на пустой БД |
| `REGISTRATION_ENABLED` | нет | По умолчанию выключено |
| `REFERRAL_CODE_GENERATION` | нет | По умолчанию выключено |
| `AUTO_DELETE_ENABLED` / `AUTO_DELETE_DAYS` | нет | Фоновое хранение данных |
| `STORE_RAW_MIME` | нет | `0` экономит диск (рекомендуется на малых томах) |

---

## 9. Устранение неполадок

| Симптом | Вероятная причина | Решение |
| --- | --- | --- |
| Cookie не сохраняется локально | `COOKIE_SECURE=1` на HTTP | Установите `COOKIE_SECURE=0` для локальной разработки |
| Worker отклоняет почту | Неверный токен / URL или API спит | Сверьте секреты; отключите PaaS sleep; проверьте `/ping` |
| `database is locked` | Несколько экземпляров на одном SQLite-файле | Уменьшите до **1** worker/экземпляра |
| UI пустой после деплоя | CDN закешировал старый `index.html` | Принудительное обновление; хеш ассетов обновляется вместе со статическими файлами |
| Смена пароля admin в `.env` не работает | Аккаунт уже существует | Воспользуйтесь сменой пароля внутри приложения |
| Форма регистрации отсутствует | Функция отключена | Включите в admin UI или установите `REGISTRATION_ENABLED=1`, затем перезапустите (первый запуск) / переключите в dashboard |
| ImportError `app.main` | Неверная рабочая директория | Запускайте uvicorn из корня репозитория |
| Тесты трогают реальную БД | Окружение не изолировано | Suite использует временную БД; не задавайте `DATABASE_PATH` production в той же оболочке |

---

## 10. Обновление

```bash
git pull
source .venv/bin/activate   # или Windows-аналог
pip install -r requirements.txt
# перезапустить uvicorn / передеплоить PaaS
cd worker && npx wrangler deploy   # если изменялся worker/
```

Миграции схемы являются аддитивными и выполняются при запуске — передеплой безопасен.

---

## 11. Удаление / очистка локальных данных

```bash
# Остановите сервер, затем:
rm -rf data/ .venv/
# Windows: Remove-Item -Recurse -Force data, .venv
```

Production: удалите том / базу данных и Cloudflare Worker по необходимости.
