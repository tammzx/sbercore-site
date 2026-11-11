"""Тесты API «СберОдин»: новые эндпоинты воронки, стоимости, согласий."""
from __future__ import annotations

import pytest

from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_funnel_status_endpoint(client):
    # Нужен JWT — в тестовом клиенте без аутентификации вернёт 401
    response = client.get("/v1/funnel/status")
    assert response.status_code in (401, 403)


def test_funnel_transition_endpoint(client):
    response = client.post("/v1/funnel/transition", json={
        "to_stage": "ip_candidate",
        "reason": "рост оборотов",
    })
    assert response.status_code in (401, 403, 422)


def test_cost_report_endpoint(client):
    response = client.get("/v1/cost/report")
    assert response.status_code in (401, 403)


def test_metrics_endpoint(client):
    response = client.get("/v1/metrics")
    assert response.status_code in (401, 403)


def test_feedback_endpoint(client):
    response = client.post("/v1/feedback", json={
        "role": "retail",
        "question": "Как открыть вклад?",
        "rating": "helpful",
    })
    assert response.status_code in (401, 403)


def test_consents_endpoints(client):
    # POST /consents без аутентификации
    response = client.post("/v1/consents", json={
        "consent_type": "pdn_dialogue",
        "scope": ["retail"],
    })
    assert response.status_code in (401, 403)

    # GET /consents без аутентификации
    response = client.get("/v1/consents")
    assert response.status_code in (401, 403)

    # POST /consents/{id}/withdraw без аутентификации
    response = client.post("/v1/consents/consent-1/withdraw")
    assert response.status_code in (401, 403)
