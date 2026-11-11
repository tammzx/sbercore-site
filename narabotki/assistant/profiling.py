"""Профилирование пользователя и персонализированные рекомендации.

Профилирование ведётся только для ролей RETAIL и BUSINESS. Для EMPLOYEE
профиль не формируется (ТК РФ ст. 86–88, изоляция HR-контура).

Все данные профиля хранятся в ролевом анклаве (изолированно), поэтому
контекст не теряется в рамках одного пользователя при переключении ролей.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from assistant.domain import Role, ConsentScope
from assistant.role_context import ConsentRequiredError, RoleContextManager


class UserSegment(str, Enum):
    """Сегменты пользователя для розницы и бизнеса."""

    MASS = "mass"               # массовый клиент
    PREMIUM = "premium"         # премиум-клиент
    BUSINESS_OWNER = "business_owner"  # владелец бизнеса
    SELF_EMPLOYED = "self_employed"    # самозанятый
    IP = "ip"                   # ИП
    UNKNOWN = "unknown"         # неопределённый


class PreferenceCategory(str, Enum):
    """Категории предпочтений клиента."""

    SAVINGS = "savings"         # вклады, накопления
    CREDIT = "credit"           # кредиты, ипотека
    PAYMENTS = "payments"       # переводы, платежи
    INVESTMENTS = "investments" # инвестиции
    INSURANCE = "insurance"     # страхование
    BUSINESS_TOOLS = "business_tools"  # бизнес-инструменты
    PAYROLL = "payroll"         # зарплатные проекты


@dataclass
class BehavioralPattern:
    """Поведенческий паттерн, обнаруженный в действиях пользователя."""

    pattern_type: str           # например 'regular_transfers', 'high_balance'
    description: str
    confidence: float           # 0.0–1.0
    evidence_count: int
    first_seen: str
    last_seen: str


@dataclass
class UserProfile:
    """Профиль пользователя. Хранится в ролевом анклаве."""

    user_ref: str
    role: Role
    segment: UserSegment = UserSegment.UNKNOWN
    preferences: list[PreferenceCategory] = field(default_factory=list)
    behavior_patterns: list[BehavioralPattern] = field(default_factory=list)
    lifecycle_stage: str = "new"          # new, active, loyal, at_risk, churned
    estimated_value_rub: float = 0.0
    last_transaction_at: str | None = None
    total_transactions: int = 0
    avg_transaction_rub: float = 0.0
    recommended_products: list[str] = field(default_factory=list)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserProfile":
        return cls(**data)


@dataclass
class Recommendation:
    """Персонализированная рекомендация."""

    product: str
    reason: str
    confidence: float           # 0.0–1.0
    priority: int               # 1 — выше
    requires_consent: bool = False
    consent_scope: ConsentScope | None = None


class ProfilingService:
    """Сервис профилирования и рекомендаций.

    Args:
        rcm: менеджер ролевого контекста (доступ к анклаву и единому контексту)
        consent_service: сервис согласий (ConsentService)
    """

    def __init__(self, rcm: RoleContextManager, consent_service) -> None:
        self._rcm = rcm
        self._consent = consent_service

    # -- профиль --------------------------------------------------------------

    def _can_profile(self, role: Role) -> bool:
        """Профилирование разрешено только для розницы и бизнеса."""
        return role in (Role.RETAIL, Role.BUSINESS)

    def _profile_key(self, role: Role) -> str:
        return f"profile:{role.value}"

    async def get_profile(self, role: Role | None = None) -> UserProfile | None:
        """Получить профиль пользователя для роли.

        Для сотрудника возвращает None (профилирование не ведётся).
        """
        role = role or self._rcm.active_role
        if role is None:
            return None
        if not self._can_profile(role):
            return None

        data = await self._rcm.get_isolated_data(self._profile_key(role), role=role)
        if not data:
            return None
        return UserProfile.from_dict(data)

    async def analyze_and_update(
        self,
        transactions: list[dict],
        receipts: list[dict] | None = None,
        profile_info: dict | None = None,
        *,
        role: Role | None = None,
    ) -> UserProfile:
        """Проанализировать данные клиента и обновить его профиль.

        Требует согласия pdn_dialogue:{role} (уже проверяется rcm.get_context).
        """
        role = role or self._rcm.active_role
        if role is None:
            raise RoleNotAvailableError(role)  # type: ignore[arg-type]
        if not self._can_profile(role):
            return UserProfile(user_ref=self._rcm.user_ref, role=role)

        receipts = receipts or []
        profile_info = profile_info or {}
        existing = await self.get_profile(role) or UserProfile(
            user_ref=self._rcm.user_ref, role=role
        )

        # --- сегмент ---------------------------------------------------------
        segment = self._detect_segment(existing, transactions, profile_info)

        # --- предпочтения ----------------------------------------------------
        preferences = self._detect_preferences(existing, transactions, receipts)

        # --- паттерны поведения ---------------------------------------------
        patterns = self._detect_patterns(existing.behavior_patterns, transactions)

        # --- метрики стоимости -----------------------------------------------------
        total_amount = sum(float(t.get("amount", 0)) for t in transactions)
        tx_count = len(transactions)
        avg_tx = total_amount / tx_count if tx_count else 0.0

        now = datetime.now(timezone.utc).isoformat()
        updated = UserProfile(
            user_ref=self._rcm.user_ref,
            role=role,
            segment=segment,
            preferences=preferences,
            behavior_patterns=patterns,
            lifecycle_stage=existing.lifecycle_stage or "new",
            estimated_value_rub=existing.estimated_value_rub + total_amount,
            last_transaction_at=(
                existing.last_transaction_at or now
            ),
            total_transactions=existing.total_transactions + tx_count,
            avg_transaction_rub=avg_tx if avg_tx else existing.avg_transaction_rub,
            recommended_products=existing.recommended_products,
            updated_at=now,
        )

        await self._rcm.isolate_data(
            self._profile_key(role), updated.to_dict(), role=role
        )
        return updated

    # -- рекомендации ---------------------------------------------------------

    async def get_recommendations(
        self,
        *,
        role: Role | None = None,
        context_question: str | None = None,
        limit: int = 5,
    ) -> list[Recommendation]:
        """Получить персонализированные рекомендации.

        Учитывает профиль пользователя и текущий вопрос (контекст).
        Для сотрудника возвращает пустой список.
        """
        role = role or self._rcm.active_role
        if role is None or not self._can_profile(role):
            return []

        profile = await self.get_profile(role)
        if not profile:
            return []

        recommendations: list[Recommendation] = []

        # Базовые рекомендации по сегменту
        base = self._base_recommendations(profile)
        recommendations.extend(base)

        # Контекстные рекомендации по вопросу
        if context_question:
            contextual = self._contextual_recommendations(
                profile, context_question
            )
            recommendations.extend(contextual)

        # Дедупликация и сортировка по приоритету
        seen: set[str] = set()
        unique: list[Recommendation] = []
        for rec in sorted(recommendations, key=lambda r: r.priority):
            if rec.product in seen:
                continue
            seen.add(rec.product)
            unique.append(rec)

        # Проверка согласия для рекомендаций, требующих перекрёстного доступа
        final: list[Recommendation] = []
        for rec in unique[:limit]:
            if rec.requires_consent and rec.consent_scope is not None:
                if rec.consent_scope is ConsentScope.CROSS_RETRIEVAL_RETAIL:
                    from_role = Role.RETAIL.to_legacy()
                    to_role = Role.BUSINESS.to_legacy()
                else:
                    from_role = role.to_legacy()
                    to_role = role.to_legacy()
                granted = await self._consent.check(
                    self._rcm.user_ref,
                    rec.consent_scope,
                    from_role=from_role,
                    to_role=to_role,
                )
                if not granted:
                    continue  # пропускаем рекомендацию без согласия
            final.append(rec)

        return final

    # -- внутренние хелперы ----------------------------------------------------

    def _detect_segment(
        self,
        existing: UserProfile,
        transactions: list[dict],
        profile_info: dict,
    ) -> UserSegment:
        total_amount = sum(float(t.get("amount", 0)) for t in transactions)
        tx_count = len(transactions)

        if profile_info.get("is_business_owner"):
            return UserSegment.BUSINESS_OWNER
        if profile_info.get("is_self_employed"):
            return UserSegment.SELF_EMPLOYED
        if total_amount >= 1_000_000 or tx_count >= 50:
            return UserSegment.PREMIUM
        if tx_count >= 10 or existing.segment in (UserSegment.PREMIUM, UserSegment.BUSINESS_OWNER):
            return existing.segment
        return UserSegment.MASS

    def _detect_preferences(
        self,
        existing: UserProfile,
        transactions: list[dict],
        receipts: list[dict],
    ) -> list[PreferenceCategory]:
        found: set[PreferenceCategory] = set(existing.preferences)

        for t in transactions:
            desc = str(t.get("description", "")).lower()
            cat = str(t.get("category", "")).lower()
            if any(w in desc for w in ("вклад", "накоп", "депозит")):
                found.add(PreferenceCategory.SAVINGS)
            if any(w in desc for w in ("кредит", "ипотека", "займ")):
                found.add(PreferenceCategory.CREDIT)
            if any(w in desc for w in ("перевод", "оплата", "платёж")):
                found.add(PreferenceCategory.PAYMENTS)
            if any(w in desc for w in ("инвестиц", "акция", "облигац")):
                found.add(PreferenceCategory.INVESTMENTS)
            if any(w in desc for w in ("страхов", "полис")):
                found.add(PreferenceCategory.INSURANCE)
            if cat in ("biz_tools", "business") or any(
                w in desc for w in ("расчётный счёт", "эквайринг", "зарплатный")
            ):
                found.add(PreferenceCategory.BUSINESS_TOOLS)

        return sorted(found, key=lambda p: p.value)

    def _detect_patterns(
        self,
        existing: list[BehavioralPattern],
        transactions: list[dict],
    ) -> list[BehavioralPattern]:
        now = datetime.now(timezone.utc).isoformat()
        patterns: dict[str, BehavioralPattern] = {p.pattern_type: p for p in existing}

        # Регулярные переводы «за услуги»
        service_tx = [
            t for t in transactions
            if "услуг" in str(t.get("description", "")).lower()
        ]
        if service_tx:
            p = patterns.get("regular_service_transfers")
            if p:
                p.evidence_count += len(service_tx)
                p.last_seen = now
                p.confidence = min(0.95, p.confidence + 0.05)
            else:
                patterns["regular_service_transfers"] = BehavioralPattern(
                    pattern_type="regular_service_transfers",
                    description="Регулярные переводы «за услуги» — признак предпринимательской деятельности",
                    confidence=0.60,
                    evidence_count=len(service_tx),
                    first_seen=now,
                    last_seen=now,
                )

        # Высокий средний чек
        amounts = [float(t.get("amount", 0)) for t in transactions if t.get("amount")]
        if amounts and sum(amounts) / len(amounts) > 50_000:
            p = patterns.get("high_avg_check")
            if not p:
                patterns["high_avg_check"] = BehavioralPattern(
                    pattern_type="high_avg_check",
                    description="Высокий средний чек операций",
                    confidence=0.70,
                    evidence_count=1,
                    first_seen=now,
                    last_seen=now,
                )

        return list(patterns.values())

    def _base_recommendations(self, profile: UserProfile) -> list[Recommendation]:
        recs: list[Recommendation] = []

        if profile.segment == UserSegment.PREMIUM:
            recs.append(Recommendation(
                product="Премиальное обслуживание",
                reason="Ваш сегмент — премиум. Доступны эксклюзивные условия по вкладам и кредитам.",
                confidence=0.9,
                priority=1,
            ))
        if profile.segment == UserSegment.BUSINESS_OWNER:
            recs.append(Recommendation(
                product="Расчётный счёт для бизнеса",
                reason="Вы идентифицированы как владелец бизнеса. Рекомендуем открыть Р/С в СберБизнес.",
                confidence=0.85,
                priority=1,
                requires_consent=True,
                consent_scope=ConsentScope.CROSS_RETRIEVAL_RETAIL,
            ))
        if PreferenceCategory.SAVINGS in profile.preferences:
            recs.append(Recommendation(
                product="Накопительный счёт",
                reason="Вы часто работаете с накоплениями — подойдёт накопительный счёт с выгодной ставкой.",
                confidence=0.75,
                priority=2,
            ))
        if PreferenceCategory.CREDIT in profile.preferences:
            recs.append(Recommendation(
                product="Кредитная карта",
                reason="История показывает интерес к кредитным продуктам.",
                confidence=0.7,
                priority=3,
            ))
        if PreferenceCategory.INVESTMENTS in profile.preferences:
            recs.append(Recommendation(
                product="Инвестиционный счёт",
                reason="Вы интересовались инвестициями — доступны брокерские услуги Сбера.",
                confidence=0.65,
                priority=4,
            ))
        if PreferenceCategory.INSURANCE in profile.preferences:
            recs.append(Recommendation(
                product="Страхование",
                reason="На основе ваших операций рекомендуем оформить страховой полис.",
                confidence=0.6,
                priority=5,
            ))

        return recs

    def _contextual_recommendations(
        self,
        profile: UserProfile,
        question: str,
    ) -> list[Recommendation]:
        recs: list[Recommendation] = []
        q = question.lower()

        if any(w in q for w in ("вклад", "накоп", "процент")):
            recs.append(Recommendation(
                product="Вклад «СберОнлайн»",
                reason="Вы спросили о вкладах — подбор условия под ваш сегмент.",
                confidence=0.8,
                priority=2,
            ))
        if any(w in q for w in ("ипотека", "кредит", "займ")):
            recs.append(Recommendation(
                product="Ипотека / потребительский кредит",
                reason="Вопрос о кредитных продуктах — расчёт условий под ваш доход.",
                confidence=0.85,
                priority=2,
            ))
        if any(w in q for w in ("страхов", "полис")):
            recs.append(Recommendation(
                product="Страхование имущества/жизни",
                reason="Запрос по страхованию — подбор полиса под ваш профиль.",
                confidence=0.75,
                priority=3,
            ))
        if any(w in q for w in ("бизнес", "ип", "самозанят", "расчётный счёт")):
            recs.append(Recommendation(
                product="СберБизнес для ИП",
                reason="Вы интересуетесь бизнес-статусом — помощь с оформлением и Р/С.",
                confidence=0.9,
                priority=1,
                requires_consent=True,
                consent_scope=ConsentScope.CROSS_RETRIEVAL_RETAIL,
            ))

        return recs


class RoleNotAvailableError(Exception):
    """Заглушка для совместимости с тестами (используется из role_context)."""

    def __init__(self, role: Role) -> None:
        self.role = role
        super().__init__(f"Роль недоступна пользователю: {role.value}")
