# MANIFEST — SberOne Assistant v2.3

«Универсальный помощник Сбера»: один агент-оператор, три вариации роли
(retail / business / employee), единый контекст пользователя +
ролевые анклавы, цифровая папка, кольцевая нейросеть, проактивность,
эксперименты, ROI, объяснимость, защита от injection.
152-ФЗ / 395-1 ст. 26 / ТК РФ.

## Структура

```
├── docs/                        # Архитектурные артефакты
│   ├── 01_c4_container.md       #   1. C4-диаграмма (контейнеры, границы доверия)
│   ├── 02_data_flow_consents.md #   2. Потоки данных + модель согласий
│   ├── 03_compliance_matrix.md  #   3. Матрица соответствия (152-ФЗ, БТ, ТК РФ)
│   └── 04_sandbox_and_api_keys.md # 4. КЛЮЧИ API Сбера и песочница
├── app/                         # Ядро приложения (FastAPI, SQLAlchemy, Redis, pgvector)
│   ├── main.py                  # FastAPI: lifespan, wiring, обработчики ошибок
│   ├── config.py                # pydantic-settings (.env)
│   ├── core/                    # Ядро: контекст, изоляция, аудит, роли, исключения
│   ├── ai/                      # AI Core: GigaChat, LangGraph, RAG, расчёты
│   ├── auth/                    # Аутентификация: SberID, JWT, ConsentService
│   ├── api/                     # REST API: чат, роли, согласия
│   ├── integrations/            # Внешние интеграции: SberBusiness
│   └── models/                  # SQLAlchemy 2.0 модели
├── assistant/                   # Слой «СберОдин»: новый функционал v2.3
│   ├── __init__.py
│   ├── domain.py                # Role, ConsentScope, SubjectIdentity, правовые основания
│   ├── consent.py               # ConsentService — гранулярные согласия, отзыв, кэш
│   ├── role_context.py          # RoleContextManager — switch_role/get_context/check_consent
│   ├── context_store.py         # ContextStore — Redis TTL + pgvector
│   ├── gigachat.py              # GigaChatClient — офлайн-обёртка, ролевые промпты, PII-маскирование
│   ├── ip_funnel.py             # FunnelService — воронка retail → ИП, handoff по соглашению
│   ├── model_router.py          # ModelRouter — маршрутизация Lite/Pro/Reasoning, кэш, учёт токенов
│   ├── profiling.py             # Профилирование и персонализированные рекомендации (только retail/business)
│   ├── evals.py                 # Evaluator — golden dataset, метрики, отчёт
│   ├── digital_folder.py        # Цифровая папка: Fact-модель, CRUD фактов, TTL-архивация
│   ├── ring_network.py          # Кольцевая нейросеть: извлечение фактов, суммаризация
│   ├── proactive.py             # Проактивный помощник: инициирование диалога, триггеры
│   ├── experiments.py           # A/B-эксперименты, причинная инференция (causal inference)
│   ├── economics.py             # ROI-калькулятор: NPV, payback, sensitivity-анализ
│   ├── explainability.py        # Объяснимость: «почему я это вижу»
│   ├── guardrails.py            # Защита от prompt injection в LangGraph-графе
│   ├── scenario_day.py          # Сквозной сценарий «Один день пользователя»
│   └── api.py                   # FastAPI роутер: все эндпоинты v2.3
├── sandbox/
│   └── demo.py                  # ★ ПЕСОЧНИЦА: полный сценарий без сети/БД/ключей
├── tests/                       # 111 тестов: изоляция, согласия, агент, воронка, профиль, кольцевая сеть, proactive, эксперименты, экономика, explainability, guardrails
├── landing/
│   └── index.html               # Лендинг: HTML/CSS/JS, без внешних CDN, тёмная тема #FF5C00
├── presentation/
│   └── index.html               # Презентация: 15 слайдов, печать в PDF = 15 страниц
├── db/init.sql                  # DDL: pgvector, констрейнты, неизменяемый audit_log
├── docker-compose.yml           # postgres (pgvector) + redis
├── requirements.txt             # все зависимости
├── pytest.ini / .env.example / .gitignore / README.md / CHANGELOG.md
```

★ — точки входа для ревью.

## Быстрый чек-лист нового разработчика

1. `pip install -r requirements.txt`
2. `python3 -m pytest -q` → все тесты зелёные
3. `python3 -m sandbox.demo` → полный сценарий системы (включая «Один день»)
4. `python3 -m assistant.evals` → оценка качества
5. `docs/04_sandbox_and_api_keys.md` → ключи GigaChat / SberID → `.env`
6. `docker-compose up -d postgres redis && psql "$DATABASE_URL" -f db/init.sql`
7. `uvicorn app.main:app` → http://localhost:8000/docs

## Правила работы с репозиторием

- Ключи и сертификаты — только в `.env` / локальных pem, которые в `.gitignore`;
- комментарии и docstring — на русском; типизация обязательна (`mypy --strict` цель);
- ветка → MR → ревью архитектора + юриста по согласиям;
- тесты на изоляцию (`tests/test_context_manager.py`, `tests/test_isolation.py`) обязательны к прогону перед MR:
  любые изменения контекстной модели не должны ронять гарантии анклавов;
- не ломать существующий API RoleContextManager и 6 старых тестов.
