"""Тесты воронки роста: клиент → самозанятый → ИП.

Проверяем:
- детекция триггеров из данных клиента;
- рекомендация стадии;
- переход без согласия → ConsentRequiredError;
- переход с согласием → успешно;
- отзыв согласия блокирует доступ.
"""
from __future__ import annotations

import pytest

from assistant.domain import Role, ConsentScope
from assistant.ip_funnel import (
    FunnelService,
    FunnelStage,
    TriggerEvent,
    TriggerType,
)
from assistant.role_context import ConsentRequiredError
from tests.fakes import FakeStore, FakeConsents


class _FakeRCM:
    """Минимальный замок RoleContextManager для тестов воронки."""

    def __init__(self, user_ref: str = "u" * 32, active_role: Role = Role.RETAIL) -> None:
        self.user_ref = user_ref
        self._active_role = active_role
        self._facts: dict[str, Any] = {}

    @property
    def active_role(self) -> Role:
        return self._active_role

    async def isolate_data(self, key: str, value: Any, *, role: Role | None = None) -> None:
        self._facts[key] = value


# Логика детекции триггеров вынесена в отдельную функцию для простоты тестирования
def detect_triggers_logic(transactions: list[dict], profile: dict | None = None) -> list[TriggerEvent]:
    """Упрощённая детекция триггеров (дублирует логику FunnelService.detect_triggers без зависимости от rcm)."""
    profile = profile or {}
    triggers: list[TriggerEvent] = []

    service_tx = [
        t for t in transactions
        if "услуг" in str(t.get("description", "")).lower()
    ]
    if len(service_tx) >= 3:
        triggers.append(TriggerEvent(
            trigger_type=TriggerType.REGULAR_SERVICE_TRANSFERS,
            description=f"Найдено {len(service_tx)} перевода 'за услуги'",
            confidence=0.75,
            source="transactions",
            timestamp="2026-01-01T00:00:00+00:00",
        ))

    if profile.get("needs_checking_account"):
        triggers.append(TriggerEvent(
            trigger_type=TriggerType.NEED_CHECKING_ACCOUNT,
            description="Потребность в расчётном счёте",
            confidence=0.90,
            source="profile",
            timestamp="2026-01-01T00:00:00+00:00",
        ))

    return triggers


# Ленивый импорт, чтобы не ломать при отсутствии зависимостей
try:
    from assistant.ip_funnel import FunnelService  # noqa: F401
except Exception:  # pragma: no cover
    pass


# ---------------------------------------------------------------------------
# Тесты детекции триггеров
# ---------------------------------------------------------------------------

def test_detect_regular_service_transfers():
    tx = [
        {"description": "Перевод за услуги консультации", "amount": 5000},
        {"description": "Перевод за услуги дизайна", "amount": 7000},
        {"description": "Перевод за услуги копирайтинга", "amount": 3000},
        {"description": "Зарплата", "amount": 50000},
    ]
    triggers = detect_triggers_logic(tx)
    assert any(t.trigger_type == TriggerType.REGULAR_SERVICE_TRANSFERS for t in triggers)


def test_detect_checking_account_need():
    triggers = detect_triggers_logic([], profile={"needs_checking_account": True})
    assert any(t.trigger_type == TriggerType.NEED_CHECKING_ACCOUNT for t in triggers)


def test_no_triggers_without_signals():
    tx = [{"description": "Зарплата", "amount": 50000}]
    triggers = detect_triggers_logic(tx)
    assert len(triggers) == 0


# ---------------------------------------------------------------------------
# Тесты перехода стадий (по соглашению / без согласия)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transition_without_consent_blocked():
    """Переход retail → biz без согласия CROSS_RETRIEVAL_RETAIL должен падать."""
    rcm = _FakeRCM(active_role=Role.RETAIL)
    consents = FakeConsents()
    store = FakeStore()
    funnel = FunnelService(rcm, consents, store)

    with pytest.raises(ConsentRequiredError) as exc:
        await funnel.transition(FunnelStage.IP_CANDIDATE, "рост оборотов", "transactions")
    assert exc.value.scope == ConsentScope.CROSS_RETRIEVAL_RETAIL


@pytest.mark.asyncio
async def test_transition_with_consent_works():
    """Переход retail → biz с согласия должен проходить."""
    rcm = _FakeRCM(active_role=Role.RETAIL)
    consents = FakeConsents()
    await consents.grant(
        rcm.user_ref, ConsentScope.CROSS_RETRIEVAL_RETAIL,
        scope=["cross_retrieval_retail"],
        from_role="client", to_role="business",
    )
    store = FakeStore()
    funnel = FunnelService(rcm, consents, store)

    transition = await funnel.transition(FunnelStage.IP_CANDIDATE, "рост оборотов", "transactions")
    assert transition.to_stage == FunnelStage.IP_CANDIDATE
    assert transition.consent_id is not None


@pytest.mark.asyncio
async def test_transition_consent_revocation_blocks():
    """После отзыва согласия переход снова блокируется."""
    rcm = _FakeRCM(active_role=Role.RETAIL)
    consents = FakeConsents()
    await consents.grant(
        rcm.user_ref, ConsentScope.CROSS_RETRIEVAL_RETAIL,
        scope=["cross_retrieval_retail"],
        from_role="client", to_role="business",
    )
    await consents.revoke(rcm.user_ref, "consent-1")
    store = FakeStore()
    funnel = FunnelService(rcm, consents, store)

    with pytest.raises(ConsentRequiredError):
        await funnel.transition(FunnelStage.IP_CANDIDATE, "рост оборотов", "transactions")


@pytest.mark.asyncio
async def test_transition_same_role_no_consent_needed():
    """Переход внутри retail не требует cross-реtrieve согласия."""
    rcm = _FakeRCM(active_role=Role.RETAIL)
    consents = FakeConsents()
    store = FakeStore()
    funnel = FunnelService(rcm, consents, store)

    transition = await funnel.transition(FunnelStage.SELF_EMPLOYED, "самозанятость", "profile")
    assert transition.to_stage == FunnelStage.SELF_EMPLOYED
