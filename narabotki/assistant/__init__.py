"""Пакет «СберОдин» — слой оркестрации над GigaChat и агентами Сбера.

Предоставляет три контура: клиент (СберБанк Онлайн / GigaChat),
предприниматель (СберБизнес / Giga-ассистент), сотрудник («Пульс» / HR).
"""
from __future__ import annotations

from assistant.domain import Role, ConsentScope, SubjectIdentity
from assistant.consent import ConsentService
from assistant.role_context import RoleContextManager

__all__ = [
    "Role",
    "ConsentScope",
    "SubjectIdentity",
    "ConsentService",
    "RoleContextManager",
]
