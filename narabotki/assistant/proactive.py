"""Проактивный помощник — инициирует диалог на основе цифровой папки и триггеров.

Сервис отслеживает события (истекает страховка, большая трата, просрочка,
сезонный паттерн) и при соблюдении условий (время суток, частота, не спамить
более 1 раза в день) предлагает пользователю действие.

Интеграция с воронкой: если триггер «пользователь часто платит налоги как
физлицо», proactive предлагает перейти на самозанятость → handoff в ip_funnel.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any

from assistant.domain import ConsentScope, Role
from assistant.digital_folder import (
    DigitalFolderService,
    Fact,
    FactCategory,
)
from assistant.ip_funnel import FunnelService, FunnelStage
from assistant.role_context import RoleContextManager


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_str() -> str:
    return _now().isoformat()


@dataclass
class ProactiveTrigger:
    """Описание триггера проактивного сообщения."""

    trigger_id: str
    name: str
    description: str
    condition_fn: str  # имя условия (для сериализации)
    requires_consent: ConsentScope | None = None
    min_interval_hours: int = 24  # не спамить чаще этого


@dataclass
class ProactiveMessage:
    """Проактивное сообщение пользователю."""

    message_id: str
    trigger_id: str | None = None
    user_ref: str = ""
    role: Role = Role.RETAIL
    text: str = ""
    priority: int = 2
    suggested_action: str | None = None
    suggested_action_params: dict[str, Any] = field(default_factory=dict)
    requires_consent: bool = False
    consent_scope: ConsentScope | None = None
    created_at: str = field(default_factory=_now_str)
    read_at: str | None = None
    dismissed_at: str | None = None
    acted_at: str | None = None


class ProactiveService:
    """Сервис проактивных сообщений.

    Хранит непрочитанные сообщения в ролевом анклаве пользователя.
    Проверяет согласия перед предложением доступа к новым данным.
    """

    def __init__(
        self,
        rcm: RoleContextManager,
        folder_service: DigitalFolderService,
        funnel_service: FunnelService,
        store=None,
    ) -> None:
        self._rcm = rcm
        self._folder = folder_service
        self._funnel = funnel_service
        self._store = store

    # -- триггеры -------------------------------------------------------------

    def _default_triggers(self) -> list[ProactiveTrigger]:
        """Стандартный набор триггеров проактивности."""
        return [
            ProactiveTrigger(
                trigger_id="insurance_expiry",
                name="Истекает страховка",
                description="Полис страхования скоро истекает",
                condition_fn="check_insurance_expiry",
                requires_consent=None,
                min_interval_hours=24,
            ),
            ProactiveTrigger(
                trigger_id="large_spending",
                name="Большая трата",
                description="Обнаружена нетипично крупная операция",
                condition_fn="check_large_spending",
                requires_consent=None,
                min_interval_hours=24,
            ),
            ProactiveTrigger(
                trigger_id="tax_self_employed",
                name="Налоги как физлицо",
                description="Частые налоговые платежи — можно перейти на самозанятость",
                condition_fn="check_tax_pattern",
                requires_consent=ConsentScope.CROSS_RETRIEVAL_RETAIL,
                min_interval_hours=24,
            ),
            ProactiveTrigger(
                trigger_id="seasonal_deposit",
                name="Сезонный вклад",
                description="Начало сезона — выгодные ставки по вкладам",
                condition_fn="check_seasonal",
                requires_consent=None,
                min_interval_hours=168,  # раз в неделю
            ),
        ]

    # -- проверка условий триггеров -------------------------------------------

    async def _check_trigger(
        self,
        trigger: ProactiveTrigger,
        role: Role,
    ) -> bool:
        """Проверить, сработал ли триггер для данной роли."""
        if trigger.condition_fn == "check_insurance_expiry":
            return await self._check_insurance_expiry(role)
        if trigger.condition_fn == "check_large_spending":
            return await self._check_large_spending(role)
        if trigger.condition_fn == "check_tax_pattern":
            return await self._check_tax_pattern(role)
        if trigger.condition_fn == "check_seasonal":
            return await self._check_seasonal(role)
        return False

    async def _check_insurance_expiry(self, role: Role) -> bool:
        """Есть ли страховка, истекающая в ближайшие 30 дней."""
        facts = await self._folder.get_facts_by_category(
            FactCategory.RISKS, role=role
        )
        for fact in facts:
            expires_at = fact.value.get("expires_at") if isinstance(fact.value, dict) else None
            if expires_at:
                try:
                    exp_date = datetime.fromisoformat(expires_at)
                    if _now() < exp_date < _now() + timedelta(days=30):
                        return True
                except ValueError:
                    continue
        return False

    async def _check_large_spending(self, role: Role) -> bool:
        """Были ли крупные траты за последние 7 дней."""
        facts = await self._folder.get_facts_by_category(
            FactCategory.FINANCE, role=role
        )
        for fact in facts:
            if isinstance(fact.value, (int, float)) and fact.value > 100_000:
                return True
        return False

    async def _check_tax_pattern(self, role: Role) -> bool:
        """Есть ли паттерн регулярных налоговых платежей как физлицо."""
        facts = await self._folder.get_facts_by_category(
            FactCategory.BUSINESS, role=role
        )
        for fact in facts:
            if fact.key == "intent_tax" and fact.value is True:
                return True
        return False

    async def _check_seasonal(self, role: Role) -> bool:
        """Сезонный паттерн (начало сезона вкладов)."""
        month = _now().month
        # Декабрь-январь — сезон вкладов
        return month in (12, 1)

    # -- управление сообщениями ----------------------------------------------

    def _messages_namespace(self, role: Role) -> str:
        return f"{self._rcm.user_ref}:{role.value}:proactive"

    async def _load_messages(self, role: Role) -> list[ProactiveMessage]:
        """Загрузить все проактивные сообщения роли."""
        if self._store is None:
            return []
        namespace = self._messages_namespace(role)
        state = await self._store.load(namespace)
        raw = state.get("messages", [])
        return [ProactiveMessage(**m) for m in raw]

    async def _save_messages(self, role: Role, messages: list[ProactiveMessage]) -> None:
        """Сохранить проактивные сообщения роли."""
        if self._store is None:
            return
        namespace = self._messages_namespace(role)
        state = await self._store.load(namespace)
        state["messages"] = [asdict(m) for m in messages]
        state["updated_at"] = _now_str()
        await self._store.save(namespace, state)

    async def generate_pending(
        self,
        *,
        role: Role | None = None,
    ) -> list[ProactiveMessage]:
        """Сгенерировать новые проактивные сообщения на основе триггеров.

        Проверяет rate-limit (не чаще 1 раза в день на триггер),
        согласия, и изоляцию по ролям.
        """
        role = role or self._rcm.active_role
        if role is None:
            return []

        messages = await self._load_messages(role)
        existing_ids = {m.message_id for m in messages}

        new_messages: list[ProactiveMessage] = []
        for trigger in self._default_triggers():
            # Rate-limit: не спамить чаще min_interval_hours
            already_sent = False
            for m in messages:
                if getattr(m, "trigger_id", None) == trigger.trigger_id:
                    if m.created_at:
                        sent_time = datetime.fromisoformat(m.created_at)
                        if _now() - sent_time < timedelta(hours=trigger.min_interval_hours):
                            already_sent = True
                            break

            if already_sent:
                continue

            # Проверяем условие триггера
            triggered = await self._check_trigger(trigger, role)
            if not triggered:
                continue

            # Проверяем согласие, если требуется
            if trigger.requires_consent is not None:
                if trigger.requires_consent is ConsentScope.CROSS_RETRIEVAL_RETAIL:
                    from_role = Role.RETAIL.to_legacy()
                    to_role = Role.BUSINESS.to_legacy()
                else:
                    from_role = role.to_legacy()
                    to_role = role.to_legacy()
                granted = await self._rcm.check_consent(
                    trigger.requires_consent,
                    role=role,
                    from_role=from_role,
                    to_role=to_role,
                )
                if not granted:
                    continue

            # Генерируем сообщение
            message = self._build_message(trigger, role)
            message_id = hashlib.sha256(
                f"{trigger.trigger_id}:{role.value}:{_now_str()}".encode()
            ).hexdigest()[:16]
            message.message_id = message_id
            message.trigger_id = trigger.trigger_id
            new_messages.append(message)

        if new_messages:
            messages.extend(new_messages)
            await self._save_messages(role, messages)

        return new_messages

    def _build_message(
        self,
        trigger: ProactiveTrigger,
        role: Role,
    ) -> ProactiveMessage:
        """Построить текст проактивного сообщения на основе триггера."""
        templates = {
            "insurance_expiry": (
                "Вижу, что ваша страховка скоро истекает. Хотите оформить продление?",
                "Продлить страховку",
                {"action": "renew_insurance"},
            ),
            "large_spending": (
                "Обнаружена крупная трата. Хотите обсудить варианты защиты сбережений?",
                "Обсудить сбережения",
                {"action": "discuss_savings"},
            ),
            "tax_self_employed": (
                "Вы регулярно платите налоги как физическое лицо. Может, оформить самозанятость?",
                "Перейти на самозанятость",
                {"action": "transition_to_self_employed"},
            ),
            "seasonal_deposit": (
                "Начался сезон выгодных вкладов. Хотите узнать текущие ставки?",
                "Узнать ставки",
                {"action": "show_deposit_rates"},
            ),
        }
        text, action, params = templates.get(
            trigger.trigger_id,
            ("Есть новое предложение для вас.", "Подробнее", {}),
        )
        return ProactiveMessage(
            message_id="",
            user_ref=self._rcm.user_ref,
            role=role,
            text=text,
            priority=2,
            suggested_action=action,
            suggested_action_params=params,
            requires_consent=trigger.requires_consent is not None,
            consent_scope=trigger.requires_consent,
        )

    async def get_pending(
        self,
        *,
        role: Role | None = None,
    ) -> list[ProactiveMessage]:
        """Получить список непрочитанных проактивных сообщений."""
        role = role or self._rcm.active_role
        if role is None:
            return []
        messages = await self._load_messages(role)
        return [
            m for m in messages
            if m.read_at is None and m.dismissed_at is None and m.acted_at is None
        ]

    async def dismiss(
        self,
        message_id: str,
        *,
        role: Role | None = None,
    ) -> bool:
        """Отклонить проактивное сообщение."""
        role = role or self._rcm.active_role
        if role is None:
            return False
        messages = await self._load_messages(role)
        for m in messages:
            if m.message_id == message_id:
                m.dismissed_at = _now_str()
                await self._save_messages(role, messages)
                return True
        return False

    async def act(
        self,
        message_id: str,
        *,
        role: Role | None = None,
    ) -> dict[str, Any] | None:
        """Выполнить suggested_action проактивного сообщения.

        При действии «transition_to_self_employed» — handoff в ip_funnel.
        """
        role = role or self._rcm.active_role
        if role is None:
            return None
        messages = await self._load_messages(role)
        for m in messages:
            if m.message_id == message_id:
                m.acted_at = _now_str()
                await self._save_messages(role, messages)

                if m.suggested_action_params.get("action") == "transition_to_self_employed":
                    # Handoff в воронку роста
                    transition = await self._funnel.transition(
                        to_stage=FunnelStage.SELF_EMPLOYED,
                        reason="Проактивное предложение: переход на самозанятость",
                        data_source="proactive_service",
                    )
                    return {
                        "action": "transition_to_self_employed",
                        "funnel_transition": {
                            "from_stage": transition.from_stage.value,
                            "to_stage": transition.to_stage.value,
                            "consent_id": transition.consent_id,
                        },
                    }
                return {
                    "action": m.suggested_action_params.get("action"),
                    "params": m.suggested_action_params,
                }
        return None
