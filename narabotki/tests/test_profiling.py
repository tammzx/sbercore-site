"""Тесты профилирования и рекомендаций.

Проверяем:
- профиль не создаётся для сотрудника (EMPLOYEE);
- профиль создаётся для розницы и бизнеса;
- анализ транзакций обновляет сегмент, предпочтения, паттерны;
- рекомендации зависят от профиля и контекста;
- рекомендации с requires_consent фильтруются без согласия;
- данные профиля изолированы по ролям (анклав).
"""
from __future__ import annotations

import pytest

from assistant.domain import Role, ConsentScope
from assistant.profiling import (
    ProfilingService,
    UserProfile,
    UserSegment,
    PreferenceCategory,
    Recommendation,
    RoleNotAvailableError,
)
from tests.fakes import FakeStore, FakeConsents


class _FakeRCM:
    """Минимальный замок RoleContextManager для тестов профилирования."""

    def __init__(
        self,
        user_ref: str = "u" * 32,
        active_role: Role = Role.RETAIL,
    ) -> None:
        self.user_ref = user_ref
        self._active_role = active_role
        self._facts: dict[str, Any] = {}

    @property
    def active_role(self) -> Role:
        return self._active_role

    async def get_isolated_data(self, key: str, *, role: Role | None = None) -> Any | None:
        return self._facts.get(key)

    async def isolate_data(self, key: str, value: Any, *, role: Role | None = None) -> None:
        self._facts[key] = value


# ---------------------------------------------------------------------------
# Тесты профиля: только retail/business, не employee
# ---------------------------------------------------------------------------

def test_profile_not_created_for_employee():
    """Сотрудник: профилирование не ведётся, возвращается None."""
    import asyncio

    async def _run() -> None:
        rcm = _FakeRCM(active_role=Role.EMPLOYEE)
        consents = FakeConsents()
        profiler = ProfilingService(rcm, consents)
        profile = await profiler.get_profile(Role.EMPLOYEE)
        assert profile is None

    asyncio.run(_run())


def test_profile_created_for_retail_and_business():
    """Розница и бизнес: профиль создаётся и сохраняется в анклаве."""
    import asyncio

    async def _run() -> None:
        for role in (Role.RETAIL, Role.BUSINESS):
            rcm = _FakeRCM(active_role=role)
            consents = FakeConsents()
            profiler = ProfilingService(rcm, consents)
            profile = await profiler.analyze_and_update(
                [{"description": "Зарплата", "amount": 50000, "category": "зарплата"}],
                role=role,
            )
            assert profile.role == role
            assert profile.user_ref == "u" * 32
            assert profile.updated_at != ""

    asyncio.run(_run())


def test_segment_detection():
    """Сегмент определяется по сумме и количеству транзакций."""
    import asyncio

    async def _run() -> None:
        rcm = _FakeRCM(active_role=Role.RETAIL)
        consents = FakeConsents()
        profiler = ProfilingService(rcm, consents)

        # Массовый сегмент
        profile = await profiler.analyze_and_update(
            [{"description": "Перевод", "amount": 1000, "category": "перевод"} for _ in range(5)],
            role=Role.RETAIL,
        )
        assert profile.segment in (UserSegment.MASS, UserSegment.PREMIUM)

        # Премиум по сумме
        profile2 = await profiler.analyze_and_update(
            [{"description": "Вклад", "amount": 2_000_000, "category": "сбережения"}],
            role=Role.RETAIL,
        )
        assert profile2.segment == UserSegment.PREMIUM

    asyncio.run(_run())


def test_preferences_detected():
    """Предпочтения определяются по описаниям транзакций."""
    import asyncio

    async def _run() -> None:
        rcm = _FakeRCM(active_role=Role.RETAIL)
        consents = FakeConsents()
        profiler = ProfilingService(rcm, consents)
        profile = await profiler.analyze_and_update(
            [
                {"description": "Вклад накопления", "amount": 10000, "category": "сбережения"},
                {"description": "Кредитная карта оплата", "amount": 5000, "category": "кредит"},
                {"description": "Страхование имущества", "amount": 3000, "category": "страхование"},
            ],
            role=Role.RETAIL,
        )
        assert PreferenceCategory.SAVINGS in profile.preferences
        assert PreferenceCategory.CREDIT in profile.preferences
        assert PreferenceCategory.INSURANCE in profile.preferences

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Тесты рекомендаций
# ---------------------------------------------------------------------------

def test_recommendations_for_employee_empty():
    """Сотрудник: рекомендации не выдаются (пустой список)."""
    import asyncio

    async def _run() -> None:
        rcm = _FakeRCM(active_role=Role.EMPLOYEE)
        consents = FakeConsents()
        profiler = ProfilingService(rcm, consents)
        recs = await profiler.get_recommendations(role=Role.EMPLOYEE)
        assert recs == []

    asyncio.run(_run())


def test_recommendations_depend_on_profile():
    """Рекомендации зависят от сегмента и предпочтений профиля."""
    import asyncio

    async def _run() -> None:
        rcm = _FakeRCM(active_role=Role.RETAIL)
        consents = FakeConsents()
        profiler = ProfilingService(rcm, consents)

        # Создаём премиальный профиль с вкладами
        await profiler.analyze_and_update(
            [
                {"description": "Вклад накопления", "amount": 2_000_000, "category": "сбережения"},
                {"description": "Инвестиции в акции", "amount": 500_000, "category": "инвестиции"},
            ],
            role=Role.RETAIL,
        )

        recs = await profiler.get_recommendations(role=Role.RETAIL)
        assert len(recs) > 0
        products = {r.product for r in recs}
        # Премиум должен получать премиальное обслуживание
        assert any("Премиальное" in p for p in products)
        # Инвестиции — инвестиционный счёт
        assert any("Инвестиционный" in p for p in products)

    asyncio.run(_run())


def test_recommendations_contextual():
    """Контекстные рекомендации по вопросу."""
    import asyncio

    async def _run() -> None:
        rcm = _FakeRCM(active_role=Role.RETAIL)
        consents = FakeConsents()
        profiler = ProfilingService(rcm, consents)
        await profiler.analyze_and_update([], role=Role.RETAIL)

        recs = await profiler.get_recommendations(
            role=Role.RETAIL,
            context_question="Как открыть вклад?",
            limit=5,
        )
        products = {r.product for r in recs}
        assert any("Вклад" in p for p in products)

    asyncio.run(_run())


def test_recommendations_consent_filtered():
    """Рекомендация с requires_consent фильтруется без согласия."""
    import asyncio

    async def _run() -> None:
        rcm = _FakeRCM(active_role=Role.RETAIL)
        consents = FakeConsents()
        profiler = ProfilingService(rcm, consents)
        await profiler.analyze_and_update(
            [{"description": "Бизнес расходы", "amount": 100000}],
            profile_info={"is_business_owner": True},
            role=Role.RETAIL,
        )

        # Бизнес-владелец -> рекомендация расчётного счёта требует согласия
        recs_no_consent = await profiler.get_recommendations(role=Role.RETAIL)
        biz_recs = [r for r in recs_no_consent if "Расчётный счёт" in r.product]
        assert len(biz_recs) == 0  # без согласия не выдаём

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Тесты изоляции профилей по ролям
# ---------------------------------------------------------------------------

def test_profile_isolated_by_role():
    """Профиль розницы не виден в роли бизнеса и наоборот."""
    import asyncio

    async def _run() -> None:
        store = FakeStore()
        consents = FakeConsents()

        rcm_retail = _FakeRCM(user_ref="u" * 32, active_role=Role.RETAIL)
        profiler_retail = ProfilingService(rcm_retail, consents)
        await profiler_retail.analyze_and_update(
            [{"description": "Зарплата", "amount": 50000}],
            role=Role.RETAIL,
        )

        rcm_business = _FakeRCM(user_ref="u" * 32, active_role=Role.BUSINESS)
        profiler_business = ProfilingService(rcm_business, consents)
        profile_biz = await profiler_business.get_profile(Role.BUSINESS)
        assert profile_biz is None  # бизнес-профиль не создавался

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Тесты паттернов поведения
# ---------------------------------------------------------------------------

def test_behavioral_patterns_detected():
    """Обнаруживаются паттерны регулярных переводов и высокого чека."""
    import asyncio

    async def _run() -> None:
        rcm = _FakeRCM(active_role=Role.RETAIL)
        consents = FakeConsents()
        profiler = ProfilingService(rcm, consents)
        profile = await profiler.analyze_and_update(
            [
                {"description": "Перевод за услуги консультации", "amount": 5000},
                {"description": "Перевод за услуги дизайна", "amount": 7000},
                {"description": "Перевод за услуги копирайтинга", "amount": 3000},
                {"description": "Покупка премиума", "amount": 200_000},
            ],
            role=Role.RETAIL,
        )
        pattern_types = {p.pattern_type for p in profile.behavior_patterns}
        assert "regular_service_transfers" in pattern_types
        assert "high_avg_check" in pattern_types

    asyncio.run(_run())
