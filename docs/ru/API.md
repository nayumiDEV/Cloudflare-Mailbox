# OTP Inbox — Обзор API (Русский)

**Язык:** [English](../en/API.md) · [Tiếng Việt](../vi/API.md) · [中文](../zh/API.md) · [Русский](API.md)

Полный справочник (1 300+ строк): **[../API.md](../API.md)**
Интерактивный Swagger UI: `GET /docs` на работающем экземпляре.

---

## Аутентификация

Большинство `/api/*` эндпоинтов принимают **API-ключ** или **сессионный cookie**.

| Механизм | Способ использования | Когда применять |
| --- | --- | --- |
| **API-ключ** (рекомендуется для интеграций) | Заголовок `Authorization: Bearer sk-otp-...` | Все `/api/*` чтение + запись (кроме исключений ниже). CSRF не требуется. |
| **Сессионный cookie** | Cookie `otp_session` (HttpOnly, SameSite=Lax) | Web UI |
| **CSRF-токен** | Заголовок `X-CSRF-Token: <token>` | Только при использовании сессионного cookie на `POST` / `PATCH` / `DELETE` |
| **Worker-токен** | Заголовок `X-Worker-Token: <WORKER_INGEST_TOKEN>` | Только `POST /api/ingest` |

**Эндпоинты, не принимающие API-ключ:**

| Эндпоинт | Аутентификация |
| --- | --- |
| `POST /api/login`, `POST /api/register` | имя пользователя + пароль (+ реферальный код) |
| `POST /api/logout` | сессионный cookie |
| `GET /api/auth/config`, `GET /health` | публичный доступ |
| `POST /api/ingest` | worker-токен |

При наличии и cookie, и Bearer-токена **Bearer имеет приоритет**.
Лимит API-ключа по умолчанию: **120 запросов / 60 с / ключ**.

### Быстрые примеры

```bash
# Проверка работоспособности (публичная)
curl -s https://your-host/health

# Dashboard через API-ключ
curl -s -H "Authorization: Bearer sk-otp-YOUR_KEY" \
     https://your-host/api/dashboard

# Создание почтового ящика
curl -s -X POST \
     -H "Authorization: Bearer sk-otp-YOUR_KEY" \
     -H "Content-Type: application/json" \
     -d '{"address":"otp-test@example.com"}' \
     https://your-host/api/mailboxes/create
```

---

## Обзор ключевых эндпоинтов

| Группа | Пример эндпоинта | Примечания |
| --- | --- | --- |
| Проверка работоспособности | `GET /health` | Версия приложения, режим поиска, флаги функций |
| Вход / выход | `POST /api/login` · `POST /api/logout` | Сессионная аутентификация; возвращает CSRF-токен |
| Регистрация | `POST /api/register` | При включённой регистрации требует реферальный код |
| Dashboard | `GET /api/dashboard` | Статистика сообщений, список ящиков |
| Сообщения | `GET /api/messages` | Постраничная выдача; поддерживает `q=` полнотекстовый поиск |
| Одно сообщение | `GET /api/messages/{id}` | Включает OTP-поле, если извлечён код |
| Вложения | `GET /api/attachments/{id}` | Двоичная загрузка |
| Почтовые ящики | `GET /api/mailboxes` · `POST /api/mailboxes/create` | |
| API-ключи | `POST /api/keys/create` · `DELETE /api/keys/{id}` | API-ключи для разработчиков |
| Администрирование — пользователи | `GET /api/admin/users` | Только администратор |
| Администрирование — маршрутизация | `GET /api/admin/routing-rules` | |
| Администрирование — рефералы | `POST /api/admin/referral-codes/generate` | |
| Приём почты | `POST /api/ingest` | Вызывается Cloudflare Worker; требует worker-токен |

---

## Коды ошибок

Все ошибки возвращаются в формате:

```json
{"detail": "читаемое сообщение об ошибке"}
```

Часто встречающиеся HTTP-статусы:

| Код | Значение |
| --- | --- |
| `400` | Некорректный запрос / ошибка валидации |
| `401` | Отсутствуют или неверны учётные данные |
| `403` | Недостаточно прав |
| `404` | Ресурс не найден |
| `409` | Конфликт (например, адрес ящика уже занят) |
| `429` | Превышен лимит запросов |
| `500` | Ошибка сервера |

---

Полный справочник эндпоинтов со всеми схемами запросов/ответов, параметрами запросов
и примерами curl см. в **[../API.md](../API.md)**.
