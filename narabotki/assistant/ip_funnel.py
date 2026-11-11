"""Воронка роста: клиент → самозанятый → ИП → активный бизнес-клиент.

Детектор триггеров на основе данных клиента, управление стадиями
воронки и handoff из retail_ns в biz_ns СТРОГО через ConsentService
со scope CROSS_RETRIEVAL_RETAIL. Без согласия — явный отказ.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from assistant.domain import Role, ConsentScope
from assistant.role_context import ConsentRequiredError, RoleContextManager


class FunnelStage(str, Enum):
    """Стадии воронки роста клиента."""

    LEAD = "lead"                      # лид — только наблюдение
    SELF_EMPLOYED = "self_employed"    # самозанятый
    IP_CANDIDATE = "ip_candidate"      # кандидат в ИП
    IP_STARTED = "ip_started"          # ИП оформляется
    IP_OPENED = "ip_opened"            # ИП открыт
    ACTIVE_BUSINESS = "active_business"  # активный бизнес-клиент


class TriggerType(str, Enum):
    """Типы триггеров перехода на следующую стадию."""

    REGULAR_SERVICE_TRANSFERS = "regular_service_transfers"  # регулярные переводы «за услуги»
    INCOME_OUTSIDE_SALARY = "income_outside_salary"          # доходы вне зарплаты
    RECURRING_OPERATIONS = "recurring_operations"            # повторяющиеся операции
    NEED_CHECKING_ACCOUNT = "need_checking_account"          # потребность в расчётном счёте


@dataclass
class TriggerEvent:
    """Обнаруженный триггер в данных клиента."""

    trigger_type: TriggerType
    description: str
    confidence: float  # 0.0–1.0
    source: str  # источник данных: transactions / receipts / profile
    timestamp: str


@dataclass
class FunnelTransition:
    """Переход между стадиями воронки."""

    from_stage: FunnelStage
    to_stage: FunnelStage
    reason: str
    timestamp: str
    consent_id: str | None
    data_source: str


# Пороговые значения для детектора триггеров
_TRIGGER_THRESHOLDS: dict[TriggerType, dict[str, Any]] = {
    TriggerType.REGULAR_SERVICE_TRANSFERS: {
        "min_count": 3,
        "min_avg_amount": 1000.0,
        "window_days": 30,
    },
    TriggerType.INCOME_OUTSIDE_SALARY: {
        "min_count": 2,
        "min_amount": 5000.0,
    },
    TriggerType.RECURRING_OPERATIONS: {
        "min_count": 5,
        "window_days": 30,
    },
    TriggerType.NEED_CHECKING_ACCOUNT: {
        "explicit_request": True,
    },
}

# Цепочка стадий воронки
_STAGE_CHAIN: list[FunnelStage] = [
    FunnelStage.LEAD,
    FunnelStage.SELF_EMPLOYED,
    FunnelStage.IP_CANDIDATE,
    FunnelStage.IP_STARTED,
    FunnelStage.IP_OPENED,
    FunnelStage.ACTIVE_BUSINESS,
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FunnelService:
    """Сервис воронки роста клиента.

    Args:
        rcm: менеджер ролевого контекста (для доступа к данным и согласиям)
        consent_service: сервис согласий (ConsentService)
        store: хранилище контекстов (ContextStore)
    """

    def __init__(self, rcm: RoleContextManager, consent_service, store) -> None:
        self._rcm = rcm
        self._consent = consent_service
        self._store = store

    # -- детект триггеров -------------------------------------------------------

    def detect_triggers(
        self,
        transactions: list[dict],
        receipts: list[dict] | None = None,
        profile: dict | None = None,
    ) -> list[TriggerEvent]:
        """Обнаружить триггеры в данных клиента.

        Анализирует транзакции, чеки и профиль на предмет:
        - регулярных переводов «за услуги» (фриланс/услуги),
        - доходов вне зарплаты,
        - повторяющихся операций,
        - явной потребности в расчётном счёте.
        """
        receipts = receipts or []
        profile = profile or {}
        triggers: list[TriggerEvent] = []

        # 1. Регулярные переводы «за услуги»
        service_tx = [
            t for t in transactions
            if "услуг" in str(t.get("description", "")).lower()
            or "фриланс" in str(t.get("description", "")).lower()
            or "за работу" in str(t.get("description", "")).lower()
        ]
        if len(service_tx) >= _TRIGGER_THRESHOLDS[TriggerType.REGULAR_SERVICE_TRANSFERS]["min_count"]:
            triggers.append(TriggerEvent(
                trigger_type=TriggerType.REGULAR_SERVICE_TRANSFERS,
                description=(
                    f"Обнаружено {len(service_tx)} регулярных перевода 'за услуги' "
                    f"— признак предпринимательской деятельности"
                ),
                confidence=0.75,
                source="transactions",
                timestamp=_now(),
            ))

        # 2. Доходы вне зарплаты
        non_salary = [
            t for t in transactions
            if str(t.get("category", "")).lower() not in ("зарплата", "salary")
            and float(t.get("amount", 0)) > _TRIGGER_THRESHOLDS[TriggerType.INCOME_OUTSIDE_SALARY]["min_amount"]
        ]
        if len(non_salary) >= _TRIGGER_THRESHOLDS[TriggerType.INCOME_OUTSIDE_SALARY]["min_count"]:
            triggers.append(TriggerEvent(
                trigger_type=TriggerType.INCOME_OUTSIDE_SALARY,
                description=(
                    f"Найдено {len(non_salary)} доходов вне зарплаты "
                    f"— возможен переход на самозанятость"
                ),
                confidence=0.70,
                source="transactions",
                timestamp=_now(),
            ))

        # 3. Повторяющиеся операции
        recurring_tx = [
            t for t in transactions
            if t.get("recurring", False) or t.get("periodicity") == "monthly"
        ]
        if len(recurring_tx) >= _TRIGGER_THRESHOLDS[TriggerType.RECURRING_OPERATIONS]["min_count"]:
            triggers.append(TriggerEvent(
                trigger_type=TriggerType.RECURRING_OPERATIONS,
                description=(
                    f"Обнаружено {len(recurring_tx)} повторяющихся операций "
                    f"— стабильный денежный поток"
                ),
                confidence=0.65,
                source="transactions",
                timestamp=_now(),
            ))

        # 4. Потребность в расчётном счёте (явный запрос в профиле или диалоге)
        if profile.get("needs_checking_account") or any(
            "расчётный счёт" in str(r.get("text", "")).lower()
            for r in receipts
        ):
            triggers.append(TriggerEvent(
                trigger_type=TriggerType.NEED_CHECKING_ACCOUNT,
                description=(
                    "Клиент выразил потребность в расчётном счёте "
                    "— готовим предложение от СберБизнес"
                ),
                confidence=0.90,
                source="profile",
                timestamp=_now(),
            ))

        return triggers

    def recommend_stage(self, triggers: list[TriggerEvent], current: FunnelStage) -> FunnelStage:
        """Рекомендовать следующую стадию на основе триггеров."""
        if not triggers:
            return current

        # Максимальная стадия, которую могут обосновать триггеры
        max_confidence = max(t.confidence for t in triggers)
        trigger_types = {t.trigger_type for t in triggers}

        current_idx = _STAGE_CHAIN.index(current) if current in _STAGE_CHAIN else 0

        # Если есть триггер «потребность в расчётном счёте» — можно предложить ИП
        if TriggerType.NEED_CHECKING_ACCOUNT in trigger_types and max_confidence >= 0.8:
            return _STAGE_CHAIN[min(current_idx + 2, len(_STAGE_CHAIN) - 1)]

        # Если три регулярных триггера — следующий этап
        if len(trigger_types) >= 2 and max_confidence >= 0.7:
            return _STAGE_CHAIN[min(current_idx + 1, len(_STAGE_CHAIN) - 1)]

        return current

    # -- переходы между стадиями -----------------------------------------------

    async def transition(
        self,
        to_stage: FunnelStage,
        reason: str,
        data_source: str = "funnel_detector",
    ) -> FunnelTransition:
        """Выполнить переход на новую стадию воронки.

        Требует согласия CROSS_RETRIEVAL_RETAIL для handoff из retail в biz.
        Без согласия — ConsentRequiredError.
        """
        current = self._rcm.active_role or Role.RETAIL
        from_stage = self._stage_for_role(current)

        # Handoff retail → biz требует согласия
        if from_stage in (FunnelStage.LEAD, FunnelStage.SELF_EMPLOYED) and to_stage in (
            FunnelStage.IP_CANDIDATE, FunnelStage.IP_STARTED, FunnelStage.IP_OPENED, FunnelStage.ACTIVE_BUSINESS
        ):
            consent_granted = await self._consent.check(
                self._rcm.user_ref,
                ConsentScope.CROSS_RETRIEVAL_RETAIL,
                from_role=current.to_legacy(),
                to_role=Role.BUSINESS.to_legacy(),
            )
            if not consent_granted:
                raise ConsentRequiredError(
                    ConsentScope.CROSS_RETRIEVAL_RETAIL,
                    message=(
                        "Переход в бизнес-контур требует согласия на "
                        "перекрестное получение данных (cross_retrieval_retail)"
                    ),
                )
            consent_id = await self._consent.active_consent_id(
                self._rcm.user_ref,
                ConsentScope.CROSS_RETRIEVAL_RETAIL,
                from_role=current.to_legacy(),
                to_role=Role.BUSINESS.to_legacy(),
            )
        else:
            consent_id = None

        transition = FunnelTransition(
            from_stage=from_stage,
            to_stage=to_stage,
            reason=reason,
            timestamp=_now(),
            consent_id=consent_id,
            data_source=data_source,
        )

        # Сохраняем переход в контексте пользователя
        await self._rcm.isolate_data(
            "funnel_transition",
            {
                "from_stage": transition.from_stage.value,
                "to_stage": transition.to_stage.value,
                "reason": transition.reason,
                "timestamp": transition.timestamp,
                "consent_id": transition.consent_id,
                "data_source": transition.data_source,
            },
            role=current,
        )

        # Аудит перехода записывается через RoleContextManager (внутри isolate_data)

        return transition

    def get_status(self) -> dict[str, Any]:
        """Получить текущий статус воронки пользователя."""
        current_role = self._rcm.active_role or Role.RETAIL
        current_stage = self._stage_for_role(current_role)
        return {
            "current_role": current_role.to_legacy(),
            "current_stage": current_stage.value,
            "available_stages": [s.value for s in _STAGE_CHAIN],
        }

    def _stage_for_role(self, role: Role) -> FunnelStage:
        """Соответствие роли тестовой стадии воронки (упрощённая маппинг)."""
        mapping = {
            Role.RETAIL: FunnelStage.LEAD,
            Role.BUSINESS: FunnelStage.IP_OPENED,
            Role.EMPLOYEE: FunnelStage.LEAD,
        }
        return mapping.get(role, FunnelStage.LEAD)
