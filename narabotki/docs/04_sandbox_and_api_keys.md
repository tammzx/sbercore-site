# 04. Песочница и ключи API Сбера (setup-гайд)

## 1. Песочница без внешних сервисов (работает сразу)

Полный сценарий системы на фейковой инфраструктуре (без сети, БД и ключей):

```bash
python3 -m pytest -q          # 28 тестов: изоляция, согласия, агент, GigaChat-моки
python3 -m sandbox.demo       # живая демонстрация всех сценариев
```

Что показывает demo: consent gate → единый контекст при переключении ролей → RBAC инструментов → ролевые анклавы (банковская тайна) → вывод анклава по согласию → ТК РФ для сотрудника → RAG-изоляция → аудит с хеш-цепочкой.

### Полный стенд (Docker, на машине с Docker Desktop / Engine)

```bash
docker-compose up -d postgres redis      # pgvector:pg16 + redis:7 (образы — на РФ-регистри в проде)
psql "postgresql://sberone:sberone@localhost:5432/sberone" -f db/init.sql
cp .env.example .env                     # вставить ключи (см. ниже)
uvicorn app.main:app --port 8000
# Swagger: http://localhost:8000/docs
```

---

## 2. Ключи API Сбера: что и куда вставлять

Все ключи — ТОЛЬКО в файл `.env` (в git не попадает, см. `.env.example`).

### 2.1. GigaChat API — обязателен (ядро LLM)

| Ключ | Куда | Где получить |
|---|---|---|
| `GIGACHAT_CLIENT_ID` | `.env` | https://developers.sber.ru → GigaChat → «Создать проект» → раздел «Авторизация» |
| `GIGACHAT_CLIENT_SECRET` | `.env` | там же (показывается один раз) |
| `GIGACHAT_SCOPE` | `.env` | `GIGACHAT_API_PERS` — физлица · `GIGACHAT_API_B2B` — юр. лица (по договору) |

Шаги: зарегистрироваться на **developers.sber.ru** (Сбер ID) → раздел **GigaChat API** → создать проект → скопировать **Client ID** и **Client Secret** в `.env`.

⚠️ **Сертификаты.** Эндпоинты GigaChat (`ngw.devices.sberbank.ru:9443`) работают с российским сертификатом НУЦ Минцифры:
1. Установить «Russian Trusted Root CA» (сайт Госуслуг, раздел сертификатов).
2. Либо скачать цепочку и указать в `.env`: `GIGACHAT_CA_BUNDLE=/path/to/russian_trusted_ca.pem`.
Код клиента уже поддерживает CA-bundle (`app/ai/gigachat_client.py`), `verify=False` в проде запрещён.

### 2.2. Sber ID (SberID OIDC) — обязателен (аутентификация)

| Ключ | Куда | Где получить |
|---|---|---|
| `SBERID_CLIENT_ID` | `.env` | Партнёрский кабинет: https://sberid.sberbank.ru → «Подключение» → регистрация приложения |
| `SBERID_CLIENT_SECRET` | `.env` | там же |
| `SBERID_REDIRECT_URI` | `.env` | ваш callback, напр. `http://localhost:8000/auth/sberid/callback` |

В кабинете выбрать scope: `openid profile email phone` (минимальные) и настроить выдачу **ролей в claims** (`roles: [client, business, employee]`) — их читает `app/auth/sberid.py`. Для дев-режима есть песочница Sber ID в партнёрском кабинете.

### 2.3. SberBusiness API — опционален (инструменты юр. лица)

| Ключ | Куда | Где получить |
|---|---|---|
| `SBERBUSINESS_CLIENT_ID` | `.env` | Только по договору со Сбером: заявление в СберБизнес / менеджер, подключение через Сбера |
| `SBERBUSINESS_CLIENT_SECRET` | `.env` | там же |

Без этих ключей MVP работает: инструменты `business_accounts` / `business_payment_draft` возвращают `status: not_configured`, остальное полноценно.

### 2.4. Локальные секреты (сгенерировать самому)

```bash
# PSEUDONYM_KEY  — псевдонимизация user_ref
python3 -c "import secrets; print(secrets.token_hex(32))"

# JWT_SECRET     — подпись сессий
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# ENCRYPTION_KEY — Fernet-шифрование истории (models/messages)
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 2.5. HR-портал — по внутренней интеграции Сбера (для роли employee)

Используется внутренний API HR-портала (доступ через внутренний каталог API Сбера); в `.env` добавьте переменные по требованиям конкретного API (обычно mTLS-клиентский сертификат + service-token).

---

## 3. Проверка, что всё работает

```bash
python3 -m pytest -q                                   # все тесты
python3 -m sandbox.demo                                # сценарий целиком
uvicorn app.main:app --port 8000                       # подъём API
curl http://localhost:8000/health                      # {"status":"ok",...}
curl -X GET http://localhost:8000/auth/sberid/login    # вернёт login_url SberID
```

Порядок живой проверки диалога (после реального SberID-логина): `POST /v1/consents/grant` (pdn_dialogue) → `POST /v1/chat` → `POST /v1/roles/switch` → снова `POST /v1/chat` — история общая.
