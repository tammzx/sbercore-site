"""Сервис согласий: PostgreSQL — источник истины, Redis — кэш решений на 60 с.

Обёртка над app.auth.consents.PostgresConsentService с интерфейсом,
ожидаемым пакетом assistant (ConsentScope вместо ConsentType).
"""
from __future__ import annotations

from typing import Any

from app.auth.consents import PostgresConsentService as _PostgresConsentService
from assistant.domain import ConsentScope, SubjectIdentity


class ConsentService:
    """Управление согласиями субъекта ПДн.

    Методы:
        check(user, scope, from_role, to_role) -> bool
        grant(user, scope, from_role, to_role, ttl_days, evidence) -> str
        revoke(user, consent_id) -> None
        active_consent_id(user, scope, from_role, to_role) -> str | None
        list_active(user) -> list[dict]
    """

    def __init__(self, session_factory, redis_client=None, audit=None) -> None:
        self._inner = _PostgresConsentService(session_factory, redis_client, audit)

    async def check(
        self,
        user_ref: str,
        scope: ConsentScope,
        *,
        from_role: str | None = None,
        to_role: str | None = None,
    ) -> bool:
        """Проверить активное согласие (кэш Redis 60 с, источник — PostgreSQL)."""
        return await self._inner.check(
            user_ref,
            scope.value,
            from_role=from_role,
            to_role=to_role,
            scope=scope.value,
        )

    async def grant(
        self,
        user_ref: str,
        scope: ConsentScope,
        *,
        scope_list: list[str] | None = None,
        from_role: str | None = None,
        to_role: str | None = None,
        ttl_days: int | None = None,
        evidence: str = "ui",
    ) -> str:
        """Зафиксировать согласие. Вернуть consent_id."""
        return await self._inner.grant(
            user_ref,
            scope.value,
            scope=scope_list or [],
            from_role=from_role,
            to_role=to_role,
            ttl_days=ttl_days,
            evidence=evidence,
        )

    async def revoke(self, user_ref: str, consent_id: str) -> None:
        """Отозвать согласие (мгновенно, с инвалидацией кэша)."""
        await self._inner.revoke(user_ref, consent_id)

    async def active_consent_id(
        self,
        user_ref: str,
        scope: ConsentScope,
        *,
        from_role: str | None = None,
        to_role: str | None = None,
    ) -> str | None:
        """ID активного согласия по типу и паре ролей."""
        return await self._inner.active_consent_id(
            user_ref,
            scope.value,
            from_role=from_role,
            to_role=to_role,
        )

    async def list_active(self, user_ref: str) -> list[dict[str, Any]]:
        """Список активных согласий пользователя."""
        return await self._inner.list_active(user_ref)

    async def invalidate_cache(
        self,
        user_ref: str,
        scope: ConsentScope,
        *,
        from_role: str | None = None,
        to_role: str | None = None,
    ) -> None:
        """Инвалидировать кэш согласия (используется после отзыва)."""
        await self._inner._invalidate(user_ref, scope.value, from_role, to_role)
