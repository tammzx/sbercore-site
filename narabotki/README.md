# SberOne Assistant — «Универсальный помощник Сбера» (прототип v2.3)

**Один агент-оператор** для всех инструментов Сбера с тремя вариациями (`retail` / `business` / `employee`) и **единым контекстом на пользователя**: история непрерывна при переключении ролей; чувствительные данные — в ролевых анклавах (банковская тайна 395-1 ст. 26, ТК РФ ст. 86–88), общий слой — обещлён.

Соответствует кейсу «СберОдин» (AI for Business Cup '26): GROWTH (воронка retail → ИП), EXPERIENCE (помощник, которому доверяют), EFFICIENCY (маршрутизация моделей, кэш, экономика).

## Артефакты архитектуры

| Артефакт | Файл |
|---|---|
| 1. C4-диаграмма (контейнерный уровень) | `docs/01_c4_container.md` |
| 2. Потоки данных с учётом согласий | `docs/02_data_flow_consents.md` |
| 3. Матрица соответствия требованиям | `docs/03_compliance_matrix.md` |
| 4. Песочница и ключи API Сбера | `docs/04_sandbox_and_api_keys.md` |
| 5. Лендинг | `landing/index.html` |
| 6. Презентация (15 слайдов) | `presentation/index.html` |

## Пакет `assistant/` (интерфейс для разработчиков)

| Модуль | Назначение |
|---|---|
| `assistant/domain.py` | Role, ConsentScope, SubjectIdentity, правовые основания |
| `assistant/consent.py` | ConsentService — гранулярные согласия, отзыв, кэш |
| `assistant/role_context.py` | RoleContextManager — switch_role, get_context, check_consent |
| `assistant/context_store.py` | ContextStore — Redis TTL + pgvector |
| `assistant/gigachat.py` | GigaChatClient — офлайн-обёртка, ролевые промпты, PII-маскирование |
| `assistant/ip_funnel.py` | FunnelService — воронка retail → ИП, handoff по соглашению |
| `assistant/model_router.py` | ModelRouter — маршрутизация Lite/Pro/Reasoning, кэш, учёт токенов |
| `assistant/profiling.py` | Профилирование и персонализированные рекомендации (только retail/business) |
| `assistant/evals.py` | Evaluator — golden dataset, метрики, отчёт |
| `assistant/digital_folder.py` | Цифровая папка: Fact-модель, CRUD фактов, TTL-архивация |
| `assistant/ring_network.py` | Кольцевая нейросеть: извлечение фактов, суммаризация |
| `assistant/proactive.py` | Проактивный помощник: инициирование диалога, триггеры |
| `assistant/experiments.py` | A/B-эксперименты, причинная инференция (causal inference) |
| `assistant/economics.py` | ROI-калькулятор: NPV, payback, sensitivity-анализ |
| `assistant/explainability.py` | Объяснимость: «почему я это вижу» |
| `assistant/guardrails.py` | Защита от prompt injection в LangGraph-графе |
| `assistant/scenario_day.py` | Сквозной сценарий «Один день пользователя» |
| `assistant/api.py` | FastAPI роутер: /chat, /roles/*, /consents/*, /funnel/*, /cost/report, /metrics, /feedback, /profile, /recommendations, /folder/*, /proactive/*, /experiments/*, /economics/roi, /economics/calculate, /explain/{id}, /guardrails/stats, /health |

## Быстрый старт (песочница, без ключей)

```bash
pip install -r requirements.txt
python3 -m pytest -q          # все тесты зелёные
python3 -m sandbox.demo       # полный сценарий: consent → роли → анклавы → воронка → профиль → кольцевая сеть → проактивность → один день
python3 -m assistant.scenario_day  # только сквозной сценарий «Один день» (proactive + explainability)
python3 -m assistant.evals    # оценка качества (golden dataset, сводка в консоль + JSON)
```

## Полный стенд (с реальными сервисами)

```bash
cp .env.example .env          # вставить ключи — см. docs/04_sandbox_and_api_keys.md
docker-compose up -d postgres redis
psql "$DATABASE_URL" -f db/init.sql
uvicorn app.main:app --port 8000     # http://localhost:8000/docs
```

## Ключи API Сбера (кратко)

| Сервис | Переменные .env | Где получить |
|---|---|---|
| GigaChat API (обязателен) | `GIGACHAT_CLIENT_ID`, `GIGACHAT_CLIENT_SECRET`, `GIGACHAT_SCOPE` | developers.sber.ru → GigaChat → создать проект |
| Sber ID (обязателен) | `SBERID_CLIENT_ID`, `SBERID_CLIENT_SECRET`, `SBERID_REDIRECT_URI` | sberid.sberbank.ru → регистрация приложения |
| SberBusiness (опция) | `SBERBUSINESS_CLIENT_ID`, `SBERBUSINESS_CLIENT_SECRET` | по договору со Сбером |
| Сертификат НУЦ Минцифры | `GIGACHAT_CA_BUNDLE` | Госуслуги / gu.ru (Russian Trusted CA) |

## Критерий приёма

- `pytest -q` → 111 тестов зелёные (старые 6 + новые 105)
- `python -m sandbox.demo` → работает, показывает сценарий «Один день» с proactive и explainability
- `python -m assistant.scenario_day` → 2 проактивных сообщения, рекомендации и justification
- `python -m assistant.evals` → выводит сводку качества (retrieval 100%, отказ 100%, утечки 0)
- `tests/test_context_manager.py` и `tests/test_isolation.py` — без изменений (гарантии анклавов не сломаны)
- `landing/index.html` → открывается локально без сервера
- `presentation/index.html` → печать в PDF даёт ровно 15 страниц (15 × `<section class="slide">`)
- Нет CJK-символов в коде
- README.md и CHANGELOG.md актуальны
