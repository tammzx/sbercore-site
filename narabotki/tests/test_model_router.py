"""Тесты маршрутизатора моделей: классификация, кэш, стоимость.

Проверяем:
- детерминированную классификацию сложности;
- маршрутизацию simple→Lite, medium→Pro, complex/reasoning→Reasoning;
- изоляцию кэша по namespace и role;
- подсчёт токенов и стоимости;
- отчёт /cost/report.
"""
from __future__ import annotations

import pytest

from assistant.domain import Role
from assistant.model_router import (
    ModelRouter,
    ModelType,
    ComplexityLevel,
    classify_complexity,
    model_for_complexity,
    cache_key,
    normalize_query,
)
from tests.fakes import FakeGigaChat, FakeStore


class _FakeRCM:
    def __init__(self, user_ref: str = "u" * 32, role: Role = Role.RETAIL) -> None:
        self.user_ref = user_ref
        self._role = role

    @property
    def active_role(self) -> Role:
        return self._role

    @property
    def unified_namespace(self) -> str:
        return f"{self.user_ref}:unified"


# ---------------------------------------------------------------------------
# Тесты классификации и маршрутизации
# ---------------------------------------------------------------------------

def test_classify_simple():
    assert classify_complexity("привет") == ComplexityLevel.SIMPLE
    assert classify_complexity("где отделение?") == ComplexityLevel.SIMPLE


def test_classify_medium():
    assert classify_complexity("как перевести деньги на карту?") == ComplexityLevel.MEDIUM
    assert classify_complexity("баланс по счёту") == ComplexityLevel.MEDIUM


def test_classify_complex():
    assert classify_complexity("как открыть ИП") == ComplexityLevel.COMPLEX
    assert classify_complexity("кредит на бизнес") == ComplexityLevel.COMPLEX


def test_classify_reasoning():
    assert classify_complexity("рассчитай налог и оптимизируй налогообложение") == ComplexityLevel.REASONING
    assert classify_complexity("сравни стратегии развития бизнеса") == ComplexityLevel.REASONING
    assert classify_complexity("как открыть ИП и расчётный счёт?") == ComplexityLevel.REASONING


def test_model_for_complexity():
    assert model_for_complexity(ComplexityLevel.SIMPLE) == ModelType.LITE
    assert model_for_complexity(ComplexityLevel.MEDIUM) == ModelType.PRO
    assert model_for_complexity(ComplexityLevel.COMPLEX) == ModelType.REASONING
    assert model_for_complexity(ComplexityLevel.REASONING) == ModelType.REASONING


# ---------------------------------------------------------------------------
# Тесты кэша: изоляция по namespace и role
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cache_key_isolation():
    """Ключ кэша должен различаться для разных namespace и role."""
    k1 = cache_key("u1:unified", "retail", "Как открыть вклад?", "v1")
    k2 = cache_key("u1:unified", "business", "Как открыть вклад?", "v1")
    k3 = cache_key("u2:unified", "retail", "Как открыть вклад?", "v1")
    assert k1 != k2  # разные role
    assert k1 != k3  # разные namespace (user_ref)


@pytest.mark.asyncio
async def test_normalize_query_deterministic():
    assert normalize_query("  Как открыть Вклад?!  ") == normalize_query("как открыть вклад")


@pytest.mark.asyncio
async def test_cache_hit_and_miss():
    """Первый запрос — miss, второй с тем же вопросом — hit."""
    rc = _FakeRCM(role=Role.RETAIL)
    store = FakeStore()
    gigachat = FakeGigaChat()
    router = ModelRouter(gigachat, store, rc)

    r1 = await router.route_and_generate("Как открыть вклад?", role=Role.RETAIL)
    assert r1["source"] == "gigachat"
    assert r1["tokens"]["cache_hit"] is False

    r2 = await router.route_and_generate("Как открыть вклад?", role=Role.RETAIL)
    assert r2["source"] == "cache"
    assert r2["tokens"]["cache_hit"] is True
    assert r1["answer"] == r2["answer"]


@pytest.mark.asyncio
async def test_cache_isolated_by_role():
    """Ответ для retail не должен возвращаться для business того же вопроса."""
    rc_retail = _FakeRCM(role=Role.RETAIL)
    rc_business = _FakeRCM(role=Role.BUSINESS)
    store = FakeStore()
    gigachat = FakeGigaChat()
    router = ModelRouter(gigachat, store, rc_retail)

    await router.route_and_generate("Как открыть вклад?", role=Role.RETAIL)

    router_biz = ModelRouter(gigachat, store, rc_business)
    r = await router_biz.route_and_generate("Как открыть вклад?", role=Role.BUSINESS)
    assert r["source"] == "gigachat"  # не должен быть хит из кэша retail


# ---------------------------------------------------------------------------
# Тесты токенов и стоимости
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_token_usage_counted():
    rc = _FakeRCM(role=Role.RETAIL)
    store = FakeStore()
    gigachat = FakeGigaChat()
    router = ModelRouter(gigachat, store, rc)

    r = await router.route_and_generate("Как открыть вклад?", role=Role.RETAIL)
    assert r["tokens"]["total"] > 0
    assert r["tokens"]["prompt"] > 0
    assert r["tokens"]["completion"] > 0
    assert r["cost_rub"] > 0


@pytest.mark.asyncio
async def test_cost_report_structure():
    rc = _FakeRCM(role=Role.RETAIL)
    store = FakeStore()
    gigachat = FakeGigaChat()
    router = ModelRouter(gigachat, store, rc)

    await router.route_and_generate("Как открыть вклад?", role=Role.RETAIL, scenario="dialogue")
    await router.route_and_generate("Как перевести деньги?", role=Role.BUSINESS, scenario="payment")

    report = router.cost_report()
    assert "total_cost_rub" in report
    assert "total_tokens" in report
    assert "by_role" in report
    assert "by_model" in report
    assert "by_scenario" in report
    assert report["currency"] == "RUB"
    assert report["total_requests"] == 2
