# CHANGELOG

## v2.3 — финальная версия для передачи (текущая версия)

- Версия проекта, архива и health-эндпоинта поднята до **2.3**: `sberone-assistant-v23.zip`.
- Релиз включает все изменения v2.2: цифровая папка, кольцевая сеть, проактивность, A/B-эксперименты, ROI, explainability, guardrails.
- Финальная приёмка: **111 тестов**, retrieval **100%**, точность отказа **100%**, утечки **0**; лендинг и презентация на **15 слайдов**.
- Синхронизированы `README.md`, `MANIFEST.md`, `sandbox/demo.py`, `download.html` и `assistant/api.py`.

---

## v2.2 — «СберОдин» с цифровой папкой, кольцевой сетью и проактивностью

### Новое
- **`assistant/digital_folder.py`** — цифровая папка пользователя: Fact-модель (категория, ключ, значение, источник, уверенность, TTL, версия), CRUD через `DigitalFolderService` (add_fact, get_facts_by_category, update_confidence, archive_expired), хранение в ролевом анклаве
- **`assistant/ring_network.py`** — кольцевая нейросеть (RingNetwork): фоновое сканирование диалогов, извлечение фактов (суммы, даты, намерения, риски), суммаризация при превышении порога, конфигурация RING_ENABLED / RING_BATCH_SIZE / RING_SUMMARY_THRESHOLD
- **`assistant/proactive.py`** — проактивный помощник: стандартные триггеры (истекает страховка, большая трата, налоги как физлицо, сезонный вклад), rate-limit (не чаще 1 раза в день), интеграция с воронкой (переход на самозанятость), эндпоинты GET /v1/proactive/pending, POST /v1/proactive/{id}/dismiss, POST /v1/proactive/{id}/act
- **`assistant/experiments.py`** — A/B-эксперименты: детерминированное назначение в группу (хеш user_ref % 100), метрики (retention_7d, retention_30d, cost_per_contact, avg_dialogue_length, cross_sell_rate, support_tickets_avoided), причинный анализ (t-Стьюдента), эндпоинты GET /v1/experiments, POST /v1/experiments, GET /v1/experiments/{id}/report
- **`assistant/economics.py`** — ROI-калькулятор: CostModel (токены, инфраструктура, поддержка), BenefitModel (снижение churn, повторные обращения, cross-sell), Scenario (sensitivity-анализ ±20%), эндпоинт GET /v1/economics/roi (NPV, payback_period, ROI)
- **`assistant/explainability.py`** — объяснимость: justification с источниками данных, top-3 фактов из папки, ссылкой на согласие, эндпоинт GET /v1/explain/{message_id}
- **`assistant/guardrails.py`** — защита от prompt injection: детектор инъекций (паттерны + эвристики), блокировка с ответом "Я не могу выполнить эту просьбу", аудит, счётчик подозрительной активности, эндпоинт GET /v1/guardrails/stats
- **`assistant/scenario_day.py`** — сквозной сценарий «Один день пользователя» (retail → self_employed → business)
- **`sandbox/demo.py`** — обновлён: включает все новые модули + сквозной сценарий «Один день», переработан как учебный пример с пошаговыми инструкциями (каждый шаг — независимый фрагмент кода для копирования в свой проект)
- **API:** новые эндпоинты `GET /v1/folder`, `POST /v1/folder`, `DELETE /v1/folder/{id}`, `GET /v1/proactive/next`, `POST /v1/proactive/dismiss`, `POST /v1/proactive/{id}/act`, `GET /v1/experiments`, `POST /v1/experiments`, `GET /v1/experiments/{id}/report`, `POST /v1/economics/calculate`, `GET /v1/economics/roi`, `GET /v1/explain/{id}`, `GET /v1/guardrails/stats`, `GET /v1/health` (сохранены и старые формы: `/folder/facts`, `/proactive/pending`, `/proactive/{id}/dismiss`)

### Изменения
- Терминология ролей приведена к `retail` / `business` / `employee` (было `client` / `business` / `employee`)
- `tests/demo.py` удалён, песочница сконцентрирована в `sandbox/demo.py`
- `app/api/deps.py`, `app/api/roles.py`, `app/api/chat.py`, `app/ai/operator_agent.py` мигрированы на `assistant.role_context.RoleContextManager`

### Сохранено (обратная совместимость)
- Все 6 старых тестов остаются зелёными
- API RoleContextManager не сломан
- Cross-namespace доступ только через ConsentService
- GigaChat в offline-режиме для песочницы

### Исправления
- `assistant/experiments.py` — добавлен `MetricRecord.from_dict`; без него отчёт A/B падал при чтении метрик из хранилища
- `assistant/ring_network.py` — суммаризация больше не теряет сводку: архивные факты сохраняются до записи нового summary-факта
- `assistant/guardrails.py` — аудит инцидентов теперь реально пишется: `RoleContextManager` хранит `_audit_log`
- `assistant/role_context.py` — `check_consent` для `CROSS_RETRIEVAL_RETAIL` идёт напрямую в ConsentPort (legacy-перечисление не содержит этот scope); добавлено хранение `_consents`
- `assistant/profiling.py`, `assistant/proactive.py` — согласие `CROSS_RETRIEVAL_RETAIL` проверяется по паре retail→business, а не по активной роли
- `assistant/scenario_day.py` — «Один день» теперь детерминированно показывает 2 проактивных сообщения, 2 рекомендации и explainability; страховка отсчитывается от текущей даты
- `landing/index.html` — добавлены блоки «Дорожная карта» и «Команда», навигация обновлена
- `presentation/index.html` — каждый слайд оформлен как `<section class="slide">` (ровно 15), удалены CJK-символы
- Удалены оставшиеся CJK-символы в `assistant/ring_network.py`, `assistant/explainability.py`, `MANIFEST.md`

### Тесты
- Всего 111 тестов (старые 6 + новые 105)
- `tests/test_ip_funnel.py` — воронка: без согласия не работает, с согласием работает, отзыв блокирует
- `tests/test_model_router.py` — маршрутизация детерминирована, кэш не смешивает namespace и роли
- `tests/test_profiling.py` — профилирование: не для сотрудника, сегменты, предпочтения, паттерны, рекомендации, изоляция по ролям
- `tests/test_evals.py` — golden dataset формируется для всех ролей
- `tests/test_api_new.py` — новые API-эндпоинты доступны (требуют аутентификации)
- `tests/test_digital_folder.py` — изоляция ролей, TTL-архивация, версионирование, clamp уверенности, мягкое удаление
- `tests/test_ring_network.py` — извлечение фактов только из сообщений пользователя, неизменность диалога, суммаризация
- `tests/test_proactive.py` — триггеры, rate-limit, согласие на cross-retrieval, dismiss, explainability
- `tests/test_experiments.py` — детерминированное назначение, распределение, blinding отчёта, значимость lift
- `tests/test_economics.py` — воспроизводимость, sensitivity, нулевая выгода, согласованность затрат
- `tests/test_explainability.py` — источники, top-3 фактов, ссылка на согласие, изоляция анклавов
- `tests/test_guardrails.py` — детекция инъекций, ложные срабатывания, аудит и счётчики

---

## v2.1 — «СберОдин»

### Новое
- **Пакет `assistant/`** — слой оркестрации над GigaChat и агентами Сбера с интерфейсом:
  - `assistant/domain.py` — Role (retail/business/employee), ConsentScope, SubjectIdentity (HMAC-SHA256 с role-salt)
  - `assistant/consent.py` — ConsentService (WORM-журнал, версии, отзыв согласий)
  - `assistant/role_context.py` — RoleContextManager (switch_role / get_context / check_consent, изоляция namespace)
  - `assistant/context_store.py` — Redis-сессии TTL 15 мин + pgvector retrieval
  - `assistant/gigachat.py` — offline-обёртка GigaChat, ролевые промпты, PII-маскирование
  - `assistant/api.py` — FastAPI: /chat, /role/switch, /consents/{id}/withdraw + новые эндпоинты
- **`assistant/ip_funnel.py`** — воронка «клиент → самозанятый → ИП»: детектор триггеров, стадии, handoff retail→biz строго через ConsentService (scope CROSS_RETRIEVAL_RETAIL)
- **`assistant/model_router.py`** — маршрутизатор моделей (Lite / Pro / Reasoning), кэш Redis с изоляцией по namespace+role, учёт токенов и стоимости, отчёт /cost/report
- **`assistant/evals.py`** — golden dataset (30+ вопросов на роль), метрики (retrieval relevance, answer correctness, refusal correctness, cross-namespace leak = 0, latency, cost per task), отчёт в консоль и JSON
- **`assistant/profiling.py`** — профилирование пользователя и персонализированные рекомендации:
  - Профилирование только для RETAIL и BUSINESS (не для EMPLOYEE — ТК РФ ст. 86–88)
  - Сегменты (mass/premium/business_owner/self_employed/ip), категории предпочтений, паттерны поведения
  - Хранение профиля в ролевом анклаве (изолированно, контекст не теряется в рамках пользователя)
  - Рекомендации на основе профиля + контекста вопроса; рекомендации с requires_consent фильтруются без согласия
- **`landing/`** — одностраничный сайт (HTML/CSS/JS, без внешних CDN, тёмная тема, оранжевый #FF5C00, интерактивный псевдо-чат)
- **`presentation/`** — HTML-презентация, 15 слайдов, печать в PDF = 15 страниц
- **API:** новые эндпоинты `GET /profile`, `POST /profile/analyze`, `GET /recommendations` + ранее добавленные `GET /funnel/status`, `POST /funnel/transition`, `GET /cost/report`, `GET /metrics`, `POST /feedback`, `POST /consents`, `GET /consents`, `POST /consents/{id}/withdraw`

### Изменения
- `app/main.py` — подключён роутер `assistant.api`
- `README.md` — обновлён с описанием всех модулей, артефактов и критериев приёма
- `CHANGELOG.md` — создан

### Сохранено (обратная совместимость)
- Все 6 старых тестов остаются зелёными
- API RoleContextManager не сломан
- Cross-namespace доступ только через ConsentService
- GigaChat в offline-режиме для песочницы

### Тесты
- Всего 76 тестов (старые 6 + новые 70)
- `tests/test_ip_funnel.py` — воронка: без согласия не работает, с согласием работает, отзыв блокирует
- `tests/test_model_router.py` — маршрутизация детерминирована, кэш не смешивает namespace и роли
- `tests/test_profiling.py` — профилирование: не для сотрудника, сегменты, предпочтения, паттерны, рекомендации, изоляция по ролям
- `tests/test_evals.py` — golden dataset формируется для всех ролей
- `tests/test_api_new.py` — новые API-эндпоинты доступны (требуют аутентификации)
