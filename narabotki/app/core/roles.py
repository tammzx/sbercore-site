"""Роли пользователя, правовые основания обработки ПДн и типы согласий."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Role(str, Enum):
    """Три роли одного физического лица."""

    CLIENT = "client"      # клиент СберБанка (розница)
    BUSINESS = "business"  # предприниматель (СберБизнес)
    EMPLOYEE = "employee"  # сотрудник Сбера (HR-контур)


class LegalBase(str, Enum):
    """Правовое основание обработки ПДн (152-ФЗ ст. 6)."""

    CONSENT = "consent"    # согласие субъекта — 152-ФЗ ст. 9
    CONTRACT = "contract"  # исполнение договора, в т.ч. трудового — ТК РФ ст. 86
    LAW = "law"            # требование закона


@dataclass(frozen=True)
class RoleSpec:
    """Спецификация роли: правовое основание и требования к согласиям."""

    role: Role
    legal_base: LegalBase
    # Требуется ли согласие на обработку ПДн в диалоге.
    # Для сотрудника — False: основание «трудовой договор» (ТК РФ ст. 86).
    consent_required_for_dialogue: bool
    description: str


ROLE_SPECS: dict[Role, RoleSpec] = {
    Role.CLIENT: RoleSpec(
        role=Role.CLIENT,
        legal_base=LegalBase.CONSENT,
        consent_required_for_dialogue=True,
        description="Розничный клиент: обработка ПДн на основании согласия (152-ФЗ ст. 9).",
    ),
    Role.BUSINESS: RoleSpec(
        role=Role.BUSINESS,
        legal_base=LegalBase.CONSENT,
        consent_required_for_dialogue=True,
        description=(
            "Предприниматель: согласие на обработку ПДн (152-ФЗ ст. 9), "
            "данные счетов — под режимом банковской тайны (395-1 ст. 26)."
        ),
    ),
    Role.EMPLOYEE: RoleSpec(
        role=Role.EMPLOYEE,
        legal_base=LegalBase.CONTRACT,
        consent_required_for_dialogue=False,
        description=(
            "Сотрудник: правовое основание — трудовой договор (ТК РФ ст. 86); "
            "согласие не используется, контур изолирован (ТК РФ ст. 88)."
        ),
    ),
}


class ConsentType(str, Enum):
    """Типы согласий. Каждое — отдельная цель обработки (152-ФЗ ст. 5 ч. 2)."""

    PDN_DIALOGUE = "pdn_dialogue"                     # обработка ПДн в диалогах роли; scope = роль
    CONTEXT_TRANSFER = "context_transfer"             # вывод записей анклава в общий слой; scope = "{role}->shared"
    BANKING_DATA_ANALYSIS = "banking_data_analysis"   # чтение данных счетов для анализа (395-1 ст. 26)
    ML_IMPROVEMENT = "ml_improvement"                 # обезличенные данные для дообучения моделей
