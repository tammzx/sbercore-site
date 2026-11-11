# 01. C4-диаграмма архитектуры (контейнерный уровень, C4 Level 2)

**Система:** SberOne Assistant — «Универсальный помощник Сбера»
**Одно физическое лицо — три роли:** `client` (СберБанк), `business` (СберБизнес), `employee` (сотрудник Сбера).
**Ключевой принцип:** изоляция контуров ролей + явный consent management (152-ФЗ, 395-1 ст. 26, ТК РФ ст. 86–88).

## Mermaid C4Container

```mermaid
C4Container
    title SberOne Assistant — контейнеры (C4 Level 2)

    Person(user, "Физическое лицо", "Клиент / предприниматель / сотрудник — три роли одной личности")

    System_Boundary(b, "SberOne Assistant (контур РФ)") {
        Container(apps, "Клиентские приложения", "Web / Mobile / Telegram-bot", "Интерфейсы трёх ролей, маркировка ответов ИИ")
        Container(gw, "API Gateway + BFF", "FastAPI", "Аутентификация SberID, роутинг ролей, лимиты, маппинг ошибок согласий")
        Container(orch, "Orchestration Layer", "LangGraph", "Диалоговый граф: consent gate -> RAG -> генерация -> критик -> code gate")
        Container(opagent, "Operator Agent", "Python, единый агент-оператор", "Один агент для трёх вариаций (физлицо/юр.лицо/сотрудник); RBAC инструментов СберAPI; единый контекст + ролевые анклавы")
        Container(ai, "AI Core", "Python, GigaChat API client", "Генерация (GigaChat-3), эмбеддинги (EmbeddingsGigaR), критик, Decimal-расчёты")
        Container(consent, "Consent Service", "FastAPI + PostgreSQL", "Гранулярные согласия (тип, scope, срок), отзыв, Redis-кэш")
        Container(audit, "Audit Service", "PostgreSQL append-only", "Неизменяемый журнал с хеш-цепочкой, хранение 5 лет")
        ContainerDb(redis, "Session Store", "Redis", "Сессии и горячий контекст по ключам {user_ref}:{role}, TTL 12 ч")
        ContainerDb(pg, "Context Store", "PostgreSQL + pgvector", "История диалогов (шифрование) и векторы; namespace на роль")
    }

    System_Ext(sberid, "SberID (OIDC)", "Аутентификация, claims ролей, минимальные scope")
    System_Ext(sbol, "СберБанк Онлайн API", "Счета, продукты клиента")
    System_Ext(sbb, "SberBusiness API", "Счета, платежи, выписки ИП/ЮЛ")
    System_Ext(hr, "HR-портал Сбера", "Служебные данные сотрудника")
    System_Ext(giga, "GigaChat API", "LLM и эмбеддинги, хостинг РФ")

    Rel(user, apps, "Использует (роль выбирает явно)")
    Rel(apps, gw, "HTTPS + JWT, PKCE")
    Rel(gw, opagent, "Диалог в активной вариации роли")
    Rel(opagent, orch, "генерация ядра (LangGraph)")
    Rel(opagent, consent, "согласия: pdn_dialogue, banking_data_analysis")
    Rel(opagent, redis, "единый контекст {user_ref}:unified + анклавы {user_ref}:{role}")
    Rel(opagent, sbol, "инструмент client_products (только роль client)")
    Rel(opagent, sbb, "инструменты business_* (только роль business)")
    Rel(opagent, hr, "инструмент hr_directory (только роль employee)")
    Rel(orch, ai, "generate / embeddings / расчёты")
    Rel(orch, pg, "RAG-поиск строго в namespace роли")
    Rel(opagent, audit, "события: role.switch, context.*, message.appended")
    Rel(gw, sberid, "OIDC code flow")
    Rel(ai, giga, "TLS 1.2+, сертификаты НУЦ Минцифры")
```

## ASCII-версия (гарантированный рендеринг)

```
 ┌──────────────────────────── Пользователи ─────────────────────────────┐
 │  Одно физическое лицо:   [client]   [business]   [employee]           │
 └──────────────────────────────────┬────────────────────────────────────┘
                                    │ HTTPS + JWT (SberID)
 ┌──────────────────────────────────▼────────────────────────────────────┐
 │  [C1] Клиентские приложения: Web / Mobile / Telegram-бот              │
 │       Явный выбор роли, маркировка ответов ИИ                          │
 └──────────────────────────────────┬────────────────────────────────────┘
 ┌──────────────────────────────────▼────────────────────────────────────┐
 │  [C2] API Gateway + BFF (FastAPI)                                     │
 │   • SberID OIDC (code + PKCE), JWT-сессии                             │
 │   • Проверка доступных ролей (claims)                                 │
 │   • Rate-limit, маппинг ConsentRequiredError -> 403 + consent_url     │
 └──────┬───────────────────┬───────────────────┬───────────────────────┘
        │                   │                   │
 ┌──────▼───────────────────────────────────────┐ ┌──────▼─────────────┐
 │ [C3] Operator Agent — ЕДИНЫЙ агент-оператор  │ │ [C5] Audit         │
 │  три вариации одного агента:                 │ │ append-only,       │
 │   физлицо · юр.лицо · сотрудник              │ │ хеш-цепочка, 5 лет │
 │  • вариация = системный промпт + инструменты │ └────────────────────┘
 │  • RBAC инструментов СберAPI (SberToolReg.)  │
 │  • единый контекст: {user_ref}:unified       │
 │  • анклавы тайны: {user_ref}:{role}:enclave  │
 └──────┬──────────────┬───────────────┬────────┘
        │              │               │
 ┌──────▼───────┐ ┌────▼──────────┐ ┌──▼───────────────┐
 │ [C4] Consent │ │ [C6] LangGraph│ │ [C7] AI Core     │
 │ Service      │ │ consent gate→ │ │ GigaChat-3 Pro,  │
 │ гранулярные  │ │ RAG→генерация │ │ EmbeddingsGigaR, │
 │ согласия     │ │ →критик→calc  │ │ Decimal code gate│
 └──────────────┘ └───────────────┘ └──────────────────┘
        │                                 │
 ┌──────▼─────────────────────────────────▼─────────────────────────────┐
 │ [C8] Redis (сессии, единый контекст)  [C9] PostgreSQL + pgvector     │
 │  sess:{sid} · ctx:{user_ref}:unified   · messages (шифрование)       │
 │  ctx:{user_ref}:{role}:enclave         · embeddings (namespace)      │
 │  TTL 12 ч                              · consents · audit_log        │
 └──────────────────────────────────────────────────────────────────────┘

 Внешние системы (за границей доверия):
   SberID (OIDC) · СберБанк Онлайн API · SberBusiness API · HR-портал · GigaChat API
```

## Модель v2.1: один агент, один контекст, три вариации

| Аспект | Решение |
|---|---|
| Агент | **Один** `OperatorAgent` обслуживает все инструменты Сбера; между ролями переключается «оптика» |
| Вариация | = системный промпт + набор доступных инструментов (`SberToolRegistry`, RBAC по ролям) |
| Контекст | **Один на пользователя**: `{user_ref}:unified`, непрерывен при switch_role |
| Тайна | Общий слой — обесличенный; чувствительное — в анклавах `{user_ref}:{role}:enclave`, чужим ролям недоступно |
| Согласия | `pdn_dialogue:{role}` на обработку ПДн; `context_transfer:{role}->shared` на вывод анклава; `banking_data_analysis` на финансовые инструменты |

## Границы доверия и правила пересечения

| Граница | Правило пересечения |
|---|---|
| Пользователь → Gateway | Только аутентифицированный JWT; роли из claims SberID, не из тела запроса |
| Gateway → Operator Agent | Каждый запрос несёт `user_ref` (псевдоним) и активную роль; сырой SberID не покидает auth-модуль |
| Operator Agent → Инструменты | RBAC: инструмент доступен только ролям из `allowed_roles` + проверка согласия перед вызовом |
| Operator Agent → Data | Единый контекст `{user_ref}:unified` (обесличенный); анклавы `{user_ref}:{role}:enclave` — только своей роли; SQL-фильтр по namespace |
| Operator Agent → GigaChat | В промпт уходит обесличенный контекст активной «оптики»; реквизиты замаскированы |
| Роль → Анклав чужой роли | Запрещён всегда (банковская тайна 395-1 ст. 26, ТК РФ ст. 88); вывод записи в общий слой — только обесличенно и по согласию `context_transfer` |
| Employee-контур → Любой внешний | Запрещён (ТК РФ ст. 88): служебные данные не покидают контур работодателя |
