"""API-песочница: полный smoke FastAPI-приложения на фейках (без greenlet/БД/сети).

Приложение тестируется через ASGITransport: маршрутизация, JWT-аутентификация,
RBAC эндпоинтов, согласия, 403-машина согласий. Жизненный цикл (lifespan)
не запускается — app.state заполняется фейками вручную.
"""
from __future__ import annotations

import httpx
import pytest

from app.ai.operator_agent import SberToolRegistry, calculator_handler
from app.auth.jwt_handler import create_session_token
from app.core.roles import Role
from tests.fakes import FakeConsents, FakeGigaChat, FakeRag, FakeStore

USER_REF = "u" * 32
JWT_SECRET = "change_me_jwt_secret"  # дефолт settings (песочница)


def make_token(roles: list[str], active_role: str | None = "client") -> str:
    return create_session_token(
        user_id="SBERID-TEST", user_ref=USER_REF,
        available_roles=roles, secret=JWT_SECRET,
    )


def make_state(app, consents: FakeConsents) -> None:
    """Заполнить app.state фейками вместо продуктовых компонентов."""
    from app.core.audit import InMemoryAuditLog

    gigachat = FakeGigaChat()
    app.state.gigachat = gigachat
    app.state.rag = FakeRag(gigachat)
    app.state.consents = consents
    app.state.audit = InMemoryAuditLog()
    app.state.context_store = FakeStore()
    app.state.tool_registry = SberToolRegistry(consents=consents, handlers={"calculator": calculator_handler})


@pytest.fixture
async def client():
    from app.main import app

    consents = FakeConsents()
    for r in Role:  # согласие на обработку ПДн во всех ролях (pdn_dialogue)
        await consents.grant(USER_REF, "pdn_dialogue", scope=[r.value])
    make_state(app, consents)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, consents


AUTH = {"Authorization": f"Bearer {make_token(['client', 'business', 'employee'])}"}


async def test_health(client):
    c, _ = client
    r = await c.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_unauthorized_rejected(client):
    c, _ = client
    r = await c.post("/v1/chat", json={"question": "привет"})
    assert r.status_code == 401


async def test_list_roles_from_jwt_claims(client):
    c, _ = client
    r = await c.get("/v1/roles", headers=AUTH)
    assert r.status_code == 200
    assert [x["role"] for x in r.json()] == ["retail", "business", "employee"]


async def test_chat_via_operator_agent(client):
    """Диалог через единого агента-оператора: ответ + единый контекст."""
    c, _ = client
    r = await c.post("/v1/chat", json={"question": "Как открыть вклад?"}, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "retail"  # новая терминология ролей
    assert body["ai_generated"] is True
    assert "ЭХО" in body["answer"]  # фейковый GigaChat вернул детерминированный ответ


async def test_switch_role_endpoint_and_shared_context(client):
    """Переключение роли через API: контекст сохраняется (context_preserved)."""
    c, _ = client
    await c.post("/v1/chat", json={"question": "вопрос в физлице"}, headers=AUTH)
    r = await c.post("/v1/roles/switch", json={"target": "business"}, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["to_role"] == "business"
    assert body["context_preserved"] is True

    r2 = await c.post("/v1/chat", json={"question": "вопрос в юрлице"}, headers=AUTH)
    assert r2.status_code == 200
    assert r2.json()["context_continuous"] is True  # история из обеих ролей


async def test_consent_403_machine(client):
    """Машина согласий: согласие есть → 200; отозвали всё → 403 + consent_url."""
    c, _ = client
    r = await c.post("/v1/chat", json={"question": "вопрос"}, headers=AUTH)  # согласие есть
    assert r.status_code == 200

    # Убираем согласие на диалог в client — чат должен отказать с consent_url
    from app.main import app

    # (отзыв через фейковый сервис напрямую — эндпоинт revoke тестируется ниже)
    app.state.consents._entries = []  # сброс всех согласий песочницы
    r2 = await c.post("/v1/chat", json={"question": "опять вопрос"}, headers=AUTH)
    assert r2.status_code == 403
    body = r2.json()  # глобальный handler возвращает плоский JSON без detail
    assert body["error"] == "consent_required"
    assert "/v1/consents/grant" in body["consent_url"]


async def test_consents_endpoints_grant_and_revoke(client):
    c, _ = client
    r = await c.post("/v1/consents/grant", headers=AUTH, json={
        "consent_type": "banking_data_analysis",
        "scope": ["business_accounts"],
        "ttl_days": 30,
    })
    assert r.status_code == 200, r.text
    consent_id = r.json()["consent_id"]

    r_list = await c.get("/v1/consents", headers=AUTH)
    assert any(x["consent_id"] == consent_id for x in r_list.json())

    r_del = await c.delete(f"/v1/consents/{consent_id}", headers=AUTH)
    assert r_del.status_code == 200
    assert r_del.json()["status"] == "revoked"


async def test_promote_endpoint_requires_consent(client):
    c, _ = client
    r = await c.post("/v1/roles/promote", json={"message_ids": [0]}, headers=AUTH)
    assert r.status_code == 403
    assert r.json()["detail"]["consent_type"] == "context_transfer"
