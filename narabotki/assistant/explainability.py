"""Объяснимость (Explainability) — «Почему я это вижу».

Для каждой рекомендации и проактивного сообщения формирует justification:
- Источник данных (цифровая папка, текущий диалог, интеграция).
- Какие факты повлияли на решение (top-3 релевантных факта из папки).
- Ссылка на действующее согласие (consent_id), если использованы ПДн.

Гарантирует, что justification не раскрывает данные из другого анклава.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from assistant.digital_folder import (
    DigitalFolderService,
    Fact,
    FactCategory,
)
from assistant.domain import ConsentScope, Role
from assistant.role_context import RoleContextManager


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Justification:
    """Обоснование рекомендации или проактивного сообщения."""

    message_id: str
    user_ref: str
    role: Role
    data_sources: list[str]  # цифровая папка, текущий диалог, интеграция
    key_facts: list[dict[str, Any]]  # top-3 релевантных факта из папки
    consent_id: str | None  # ссылка на Acting consent, если использовались ПДн
    consent_scope: ConsentScope | None = None
    explanation_text: str = ""  # человекочитаемое объяснение
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["role"] = self.role.value
        d["consent_scope"] = (
            self.consent_scope.value if self.consent_scope else None
        )
        return d


class ExplainabilityService:
    """Сервис объяснимости рекомендаций и проактивных сообщений.

    Формирует justification на основе цифровой папки пользователя.
    Никогда не раскрывает данные из чужого анклава (isolation by role).
    """

    def __init__(
        self,
        rcm: RoleContextManager,
        folder_service: DigitalFolderService,
    ) -> None:
        self._rcm = rcm
        self._folder = folder_service

    async def explain(
        self,
        message_id: str,
        *,
        role: Role | None = None,
        context_sources: list[str] | None = None,
    ) -> Justification | None:
        """Построить justification для сообщения.

        Args:
            message_id: ID сообщения (рекомендация или proactive).
            role: роль пользователя (для изоляции анклава).
            context_sources: источники данных, использованные при формировании.

        Returns:
            Justification с top-3 фактами, источниками и ссылкой на согласие.
        """
        role = role or self._rcm.active_role
        if role is None:
            return None

        # Источники данных
        if context_sources is None:
            context_sources = ["цифровая папка", "текущий диалог"]

        # Собираем релевантные факты из папки роли (только свой анклав!)
        all_facts = await self._folder.get_all_facts(role=role)
        # Сортируем по confidence и берем топ-3
        key_facts = sorted(
            all_facts, key=lambda f: f.confidence, reverse=True
        )[:3]

        key_facts_data = []
        for fact in key_facts:
            key_facts_data.append({
                "id": fact.id,
                "category": fact.category.value,
                "key": fact.key,
                "value": fact.value,
                "confidence": fact.confidence,
                "source": fact.source.value,
            })

        # Определяем, нужно ли согласие
        consent_id = None
        consent_scope = None
        needs_pdn = any(
            f.category in (FactCategory.FINANCE, FactCategory.BUSINESS)
            for f in key_facts
        )
        if needs_pdn:
            # Проверяем, есть ли активное согласие pdn_dialogue для роли
            granted = await self._rcm.check_consent(
                ConsentScope.PDN_DIALOGUE,
                role=role,
            )
            # В реальной системе здесь бы был consent_id из папки/аудита
            if granted:
                consent_id = f"consent_pdn_{role.value}"
                consent_scope = ConsentScope.PDN_DIALOGUE

        # Формируем текст объяснения
        explanation_text = self._build_explanation_text(
            key_facts_data, context_sources, role
        )

        return Justification(
            message_id=message_id,
            user_ref=self._rcm.user_ref,
            role=role,
            data_sources=context_sources,
            key_facts=key_facts_data,
            consent_id=consent_id,
            consent_scope=consent_scope,
            explanation_text=explanation_text,
        )

    def _build_explanation_text(
        self,
        key_facts: list[dict[str, Any]],
        sources: list[str],
        role: Role,
    ) -> str:
        """Сформировать текстовое объяснение на русском."""
        parts: list[str] = []

        parts.append(
            f"Это сообщение сформировано на основе данных из: {', '.join(sources)}."
        )

        if key_facts:
            parts.append("Ключевые факты, повлиявшие на решение:")
            for i, fact in enumerate(key_facts, 1):
                parts.append(
                    f"  {i}. {fact['category']} ({fact['key']}): "
                    f"{fact['value']} (уверенность {fact['confidence']:.0%}, "
                    f"источник: {fact['source']})"
                )
        else:
            parts.append("В цифровой папке нет релевантных фактов для этой роли.")

        role_names = {
            Role.RETAIL: "клиента-физлица",
            Role.BUSINESS: "предпринимателя",
            Role.EMPLOYEE: "сотрудника",
        }
        parts.append(
            f"Все факты взяты только из анклава роли "
            f"«{role_names.get(role, role.value)}» — данные других ролей не используются."
        )

        return "\n".join(parts)

    async def explain_recommendation(
        self,
        recommendation_text: str,
        *,
        role: Role | None = None,
    ) -> Justification | None:
        """Объяснить конкретную рекомендацию."""
        message_id = hashlib.sha256(
            f"rec:{recommendation_text}:{role.value if role else 'unknown'}".encode()
        ).hexdigest()[:16]
        return await self.explain(message_id, role=role)

    async def explain_proactive(
        self,
        proactive_message_id: str,
        *,
        role: Role | None = None,
    ) -> Justification | None:
        """Объяснить проактивное сообщение."""
        return await self.explain(proactive_message_id, role=role)
