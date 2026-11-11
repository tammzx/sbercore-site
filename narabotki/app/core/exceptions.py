"""Доменные исключения. Маппятся на HTTP-коды в app/api/deps.py."""
from __future__ import annotations

from app.core.roles import ConsentType


class AssistantError(Exception):
    """Базовая ошибка домена."""


class RoleNotAvailableError(AssistantError):
    """Роль недоступна пользователю (нет в claims SberID)."""

    def __init__(self, role: str) -> None:
        self.role = role
        super().__init__(f"Роль недоступна пользователю: {role}")


class ConsentRequiredError(AssistantError):
    """Требуется согласие субъекта ПДн (152-ФЗ ст. 9)."""

    def __init__(
        self,
        consent_type: ConsentType | str,
        scope: str | None = None,
        message: str | None = None,
    ) -> None:
        self.consent_type = consent_type.value if isinstance(consent_type, ConsentType) else consent_type
        self.scope = scope
        self.message = message or f"Требуется согласие: {self.consent_type} (scope={scope})"
        super().__init__(self.message)


class IsolationViolationError(AssistantError):
    """Попытка нарушения изоляции ролей (доступ к чужому namespace)."""


class GigaChatError(AssistantError):
    """Ошибка вызова GigaChat API."""
