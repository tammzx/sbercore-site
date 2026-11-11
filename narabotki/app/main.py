"""Точка входа SberOne Assistant.

Жизненный цикл: клиент GigaChat, PostgreSQL (asyncpg), Redis,
Consent Service, Audit Service, Context Store (Redis).
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.ai.gigachat_client import GigaChatClient
from app.ai.operator_agent import SberToolRegistry, calculator_handler
from app.ai.rag import RagIndex
from app.api import chat, consents_api, roles
from assistant.api import router as assistant_api_router
from app.auth import sberid
from app.auth.consents import PostgresConsentService
from app.config import settings
from app.core.audit import PgAuditLog
from app.core.isolation import pseudonymize
from assistant.role_context import ConsentRequiredError, IsolationViolationError


class RedisContextStore:
    """Прод-реализация ContextStore: JSON в Redis с TTL (горячий контекст роли)."""

    def __init__(self, redis_client, ttl_seconds: int) -> None:
        self._redis = redis_client
        self._ttl = ttl_seconds

    async def load(self, namespace: str) -> dict:
        raw = await self._redis.get(f"ctx:{namespace}")
        if raw is None:
            return {}
        return json.loads(raw)

    async def save(self, namespace: str, state: dict, ttl_seconds: int | None = None) -> None:
        await self._redis.setex(
            f"ctx:{namespace}",
            ttl_seconds or self._ttl,
            json.dumps(state, ensure_ascii=False),
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Тяжёлые зависимости импортируются лениво: модуль остаётся импортируемым
    # в ограниченных окружениях (sandbox/CI без greenlet), прод — без изменений.
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    app.state.gigachat = GigaChatClient(
        client_id=settings.GIGACHAT_CLIENT_ID,
        client_secret=settings.GIGACHAT_CLIENT_SECRET,
        scope=settings.GIGACHAT_SCOPE,
        ca_bundle=settings.GIGACHAT_CA_BUNDLE or None,
        verify_ssl=settings.GIGACHAT_VERIFY_SSL,
    )
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    app.state.engine = engine
    app.state.redis = aioredis.from_url(settings.REDIS_URL, decode_responses=False)
    consents = PostgresConsentService(session_factory, app.state.redis)
    app.state.consents = consents
    app.state.audit = PgAuditLog(session_factory)
    app.state.context_store = RedisContextStore(app.state.redis, settings.SESSION_TTL_SECONDS)
    app.state.rag = RagIndex(session_factory, app.state.gigachat)
    app.state.pseudonymize = lambda uid: pseudonymize(uid, settings.PSEUDONYM_KEY)
    # Единый агент-оператор: реестр инструментов СберAPI с RBAC по ролям.
    # Handlers внешних систем подключаются здесь (SberBusiness и т.д.);
    # calculator работает автономно (Decimal).
    app.state.tool_registry = SberToolRegistry(
        consents=consents,
        handlers={"calculator": calculator_handler},
    )

    yield

    await app.state.gigachat.aclose()
    await app.state.redis.aclose()
    await engine.dispose()


app = FastAPI(
    title="SberOne Assistant",
    description="Универсальный помощник Сбера: три роли, изоляция контуров, 152-ФЗ",
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(sberid.router)
app.include_router(chat.router)
app.include_router(roles.router)
app.include_router(consents_api.router)
app.include_router(assistant_api_router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok", "data_residency": settings.DATA_RESIDENCY}


@app.exception_handler(ConsentRequiredError)
async def consent_error_handler(request: Request, exc: ConsentRequiredError) -> JSONResponse:
    """Нет согласия → 403 + машина согласий (152-ФЗ ст. 9)."""
    return JSONResponse(
        status_code=403,
        content={
            "error": "consent_required",
            "consent_type": exc.scope.value if hasattr(exc.scope, 'value') else str(exc.scope),
            "scope": exc.scope.value if hasattr(exc.scope, 'value') else str(exc.scope),
            "consent_url": f"/v1/consents/grant?consent_type={exc.scope.value if hasattr(exc.scope, 'value') else str(exc.scope)}&scope={exc.scope.value if hasattr(exc.scope, 'value') else str(exc.scope) or ''}",
        },
    )


@app.exception_handler(IsolationViolationError)
async def isolation_error_handler(request: Request, exc: IsolationViolationError) -> JSONResponse:
    """Попытка нарушения изоляции ролей → 409, событие логируется."""
    return JSONResponse(status_code=409, content={"error": "isolation_violation", "detail": str(exc)})
