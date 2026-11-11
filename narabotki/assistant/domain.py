"""Доменная модель: роли, виды согласий, псевдонимизированная идентичность.

Общие конструкции для всех модулей пакета assistant.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from enum import Enum
from typing import Final

# --- Роли пользователя -------------------------------------------------------


class Role(str, Enum):
    """Три роли одного физического лица в экосистеме Сбера."""

    RETAIL = "retail"      # частный клиент — СберБанк Онлайн / GigaChat
    BUSINESS = "business"  # предприниматель — СберБизнес / Giga-ассистент
    EMPLOYEE = "employee"  # сотрудник Сбера — «Пульс» / внутренние системы

    @classmethod
    def from_legacy(cls, value: str) -> "Role":
        """Привести устаревшее значение роли к новой перечислению."""
        mapping = {
            "client": cls.RETAIL,
            "business": cls.BUSINESS,
            "employee": cls.EMPLOYEE,
        }
        return mapping.get(value, cls.RETAIL)

    def to_legacy(self) -> str:
        """Вернуть значение для совместимости со старым кодом (app.core.roles)."""
        mapping = {
            Role.RETAIL: "client",
            Role.BUSINESS: "business",
            Role.EMPLOYEE: "employee",
        }
        return mapping[self]


# --- Виды согласий -----------------------------------------------------------


class ConsentScope(str, Enum):
    """Сферы действия согласия (152-ФЗ ст. 5 ч. 2, ст. 9).

    Каждое согласие — отдельная цель обработки с собственным scope.
    """

    PDN_DIALOGUE = "pdn_dialogue"              # обработка ПДн в диалогах роли
    CROSS_RETRIEVAL_RETAIL = "cross_retrieval_retail"  # handoff retail → biz
    BANKING_DATA_ANALYSIS = "banking_data_analysis"  # чтение данных счетов (395-1 ст. 26)
    ML_IMPROVEMENT = "ml_improvement"          # обезличенные данные для дообучения
    CONTEXT_TRANSFER = "context_transfer"      # вывод анклава в общий слой

    def requires_role_pair(self) -> bool:
        """True, если согласие привязано к паре ролей (from → to)."""
        return self in {
            ConsentScope.CROSS_RETRIEVAL_RETAIL,
            ConsentScope.CONTEXT_TRANSFER,
        }


# --- Идентичность субъекта ---------------------------------------------------


@dataclass(frozen=True)
class SubjectIdentity:
    """Псевдонимизированная идентичность пользователя.

    subject_id —HMAC-SHA256 от SberID с role-salt; сырой SberID не
    покидает модуль аутентификации (нет сквозной идентификации).
    """

    subject_id: str  # псевдоним (HMAC), 32 hex-символа
    sber_id_hash: str  # хеш сырого SberID только для аудита внутри auth-модуля
    roles: frozenset[Role]  # доступные роли пользователя

    def has_role(self, role: Role) -> bool:
        """Проверить, доступна ли роль пользователю."""
        return role in self.roles

    @staticmethod
    def compute_subject_id(user_id: str, server_key: str, role_salt: str = "sber-one") -> str:
        """Вычислить псевдоним: HMAC-SHA256(user_id + role_salt, server_key)."""
        material = f"{user_id}:{role_salt}".encode("utf-8")
        return hmac.new(server_key.encode("utf-8"), material, hashlib.sha256).hexdigest()[:32]


# --- Правовые основания -------------------------------------------------------


class LegalBase(str, Enum):
    """Правовое основание обработки ПДн (152-ФЗ ст. 6)."""

    CONSENT = "consent"       # согласие субъекта — 152-ФЗ ст. 9
    CONTRACT = "contract"     # исполнение договора, в т.ч. трудового — ТК РФ ст. 86
    LAW = "law"               # требование закона


# Спецификации ролей (аналог app.core.roles.ROLE_SPECS, адаптировано)
ROLE_SPECS: dict[Role, tuple[LegalBase, bool, str]] = {
    Role.RETAIL: (
        LegalBase.CONSENT,
        True,
        "Розничный клиент: обработка ПДн на основании согласия (152-ФЗ ст. 9).",
    ),
    Role.BUSINESS: (
        LegalBase.CONSENT,
        True,
        "Предприниматель: согласие на обработку ПДн (152-ФЗ ст. 9), "
        "данные счетов — под режимом банковской тайны (395-1 ст. 26).",
    ),
    Role.EMPLOYEE: (
        LegalBase.CONTRACT,
        False,
        "Сотрудник: правовое основание — трудовой договор (ТК РФ ст. 86); "
        "согласие не используется, контур изолирован (ТК РФ ст. 88).",
    ),
}

DEFAULT_SALT: Final[str] = "sber-one"
