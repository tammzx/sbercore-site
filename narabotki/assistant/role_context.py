"""Менеджер ролевого контекста: единый контекст + ролевые анклавы.

Обёртка над app.core.context_manager.RoleContextManager с интерфейсом,
ожидаемым пакетом assistant (Role из assistant.domain).
"""
from __future__ import annotations

from typing import Any

from app.core.context_manager import (
    RoleContextManager as _RoleContextManager,
    ContextStore as _ContextStore,
    ConsentPort as _ConsentPort,
    AuditLog as _AuditLog,
)
from app.core.exceptions import (
    ConsentRequiredError as _ConsentRequiredError,
    RoleNotAvailableError as _RoleNotAvailableError,
    IsolationViolationError as _IsolationViolationError,
)
from app.core.roles import Role as _AppRole, ConsentType as _ConsentType
from assistant.domain import Role, ConsentScope


def _to_app_role(role: Role) -> _AppRole:
    """Конвертировать assistant.domain.Role → app.core.roles.Role."""
    return _AppRole(role.to_legacy())


def _from_app_role(role: _AppRole | None) -> Role | None:
    """Конвертировать app.core.roles.Role → assistant.domain.Role."""
    if role is None:
        return None
    return Role.from_legacy(role.value)


class ConsentRequiredError(Exception):
    """Требуется согласие субъекта ПДн (152-ФЗ ст. 9)."""

    def __init__(self, scope: ConsentScope, message: str | None = None) -> None:
        self.scope = scope
        self.message = message or f"Требуется согласие: {scope.value}"
        super().__init__(self.message)


class RoleNotAvailableError(Exception):
    """Роль недоступна пользователю (нет в claims SberID)."""

    def __init__(self, role: Role) -> None:
        self.role = role
        super().__init__(f"Роль недоступна пользователю: {role.value}")


class IsolationViolationError(Exception):
    """Попытка нарушения изоляции ролей (доступ к чужому namespace)."""


class RoleContextManager:
    """Единый контекст трёх ролей одного физического лица + ролевые анклавы.

    Методы (публичный API пакета assistant):
        switch_role(target_role) -> dict
        get_context(role) -> dict
        check_consent(scope, role, from_role, to_role) -> bool
        add_message(author, content, sensitive, role, metadata) -> dict
        promote_to_shared(message_ids) -> dict
        isolate_data(key, value, role) -> None
        get_isolated_data(key, role) -> Any | None
    """

    def __init__(
        self,
        *,
        user_ref: str,
        available_roles: frozenset[Role] | set[Role],
        active_role: Role | None,
        store: _ContextStore,
        consents: _ConsentPort,
        audit: _AuditLog,
        session_ttl_seconds: int = 12 * 60 * 60,
    ) -> None:
        self._inner = _RoleContextManager(
            user_ref=user_ref,
            available_roles=frozenset(_to_app_role(r) for r in available_roles),
            active_role=_to_app_role(active_role) if active_role else None,
            store=store,
            consents=consents,
            audit=audit,
            session_ttl_seconds=session_ttl_seconds,
        )
        self._consents = consents
        self._audit_log = audit
        self._user_ref = user_ref

    @classmethod
    async def create(
        cls,
        *,
        user_ref: str,
        available_roles: frozenset[Role] | set[Role],
        store: _ContextStore,
        consents: _ConsentPort,
        audit: _AuditLog,
        default_role: Role = Role.RETAIL,
        session_ttl_seconds: int = 12 * 60 * 60,
    ) -> "RoleContextManager":
        """Асинхронная фабрика: восстановить активную роль из сессии."""
        inner = await _RoleContextManager.create(
            user_ref=user_ref,
            available_roles=frozenset(_to_app_role(r) for r in available_roles),
            store=store,
            consents=consents,
            audit=audit,
            default_role=_to_app_role(default_role),
            session_ttl_seconds=session_ttl_seconds,
        )
        return cls(
            user_ref=user_ref,
            available_roles=available_roles,
            active_role=_from_app_role(inner.active_role),
            store=store,
            consents=consents,
            audit=audit,
            session_ttl_seconds=session_ttl_seconds,
        )

    @property
    def active_role(self) -> Role | None:
        return _from_app_role(self._inner.active_role)

    @property
    def available_roles(self) -> set[Role]:
        return {Role.from_legacy(r.value) for r in self._inner.available_roles}

    @property
    def user_ref(self) -> str:
        return self._user_ref

    @property
    def unified_namespace(self) -> str:
        return self._inner.unified_namespace

    async def switch_role(self, target: Role) -> dict:
        """Переключить активную роль.

        Единый контекст сохраняется (context_preserved=True); меняется только
        «оптика» — анклавы чужих ролей скрываются.
        """
        try:
            result = await self._inner.switch_role(_to_app_role(target))
        except _RoleNotAvailableError as exc:
            raise RoleNotAvailableError(target) from exc
        return {
            "from_role": result.from_role.value if result.from_role else None,
            "to_role": result.to_role.value,
            "context_preserved": result.context_preserved,
            "dialogue_consent_granted": result.dialogue_consent_granted,
        }

    async def get_context(self, role: Role | None = None) -> dict:
        """Единый контекст пользователя в «оптике» роли.

        Возвращает словарь с полями: role, unified_namespace, enclave_namespace,
        messages, enclave_facts, audit_reason, is_continuous.
        """
        inner_role = _to_app_role(role) if role else None
        try:
            view = await self._inner.get_context(inner_role)
        except _ConsentRequiredError as exc:
            raise ConsentRequiredError(_ConsentType.PDN_DIALOGUE) from exc
        return {
            "role": view.role.value,
            "unified_namespace": view.unified_namespace,
            "enclave_namespace": view.enclave_namespace,
            "messages": view.messages,
            "enclave_facts": view.enclave_facts,
            "audit_reason": view.audit_reason,
            "is_continuous": view.is_continuous,
        }

    async def check_consent(
        self,
        scope: ConsentScope,
        *,
        role: Role | None = None,
        from_role: str | None = None,
        to_role: str | None = None,
    ) -> bool:
        """Проверить согласие субъекта ПДн.

        Для роли EMPLOYEE и scope PDN_DIALOGUE согласие не требуется
        (ТК РФ ст. 86 — трудовой договор).
        """
        inner_role = _to_app_role(role) if role else None
        if scope is ConsentScope.CROSS_RETRIEVAL_RETAIL:
            # Кросс-ролевой доступ retail → business: отдельный consent_type,
            # которого нет в legacy-перечислении app.core.roles.
            return await self._consents.check(
                self._user_ref,
                scope.value,
                from_role=from_role,
                to_role=to_role,
                scope=scope.value,
            )
        if scope is ConsentScope.PDN_DIALOGUE and inner_role is not None:
            scope = inner_role.value  # legacy имя роли (client/business/employee)
        try:
            return await self._inner.check_consent(
                self._map_scope(scope),
                role=inner_role,
                from_role=from_role,
                to_role=to_role,
            )
        except _ConsentRequiredError as exc:
            raise ConsentRequiredError(scope, exc.message) from exc

    async def add_message(
        self,
        author: str,
        content: str,
        *,
        sensitive: bool = False,
        role: Role | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """Добавить сообщение в ЕДИНЫЙ контекст пользователя.

        sensitive=True — сырые данные только в анклаве роли, в общий слой
        попадает замаскированная метка.
        """
        inner_role = _to_app_role(role) if role else None
        return await self._inner.add_message(
            author, content, sensitive=sensitive, role=inner_role, metadata=metadata
        )

    async def promote_to_shared(self, message_ids: list[int]) -> dict:
        """Вывести выбранные записи анклава в общий слой (по согласию).

        Требует согласия CONTEXT_TRANSFER:{role}->shared.
        """
        try:
            result = await self._inner.promote_to_shared(message_ids)
        except _ConsentRequiredError as exc:
            raise ConsentRequiredError(ConsentScope.CONTEXT_TRANSFER, exc.message) from exc
        return {"promoted": result.promoted, "consent_id": result.consent_id}

    async def isolate_data(self, key: str, value: Any, *, role: Role | None = None) -> None:
        """Сохранить чувствительные данные в анклаве роли."""
        inner_role = _to_app_role(role) if role else None
        await self._inner.isolate_data(key, value, role=inner_role)

    async def get_isolated_data(self, key: str, *, role: Role | None = None) -> Any | None:
        """Прочитать данные анклава активной (или указанной) роли."""
        inner_role = _to_app_role(role) if role else None
        return await self._inner.get_isolated_data(key, role=inner_role)

    # -- внутреннее ------------------------------------------------------------

    def _map_scope(self, scope: ConsentScope) -> _ConsentType:
        mapping = {
            ConsentScope.PDN_DIALOGUE: _ConsentType.PDN_DIALOGUE,
            ConsentScope.CONTEXT_TRANSFER: _ConsentType.CONTEXT_TRANSFER,
            ConsentScope.BANKING_DATA_ANALYSIS: _ConsentType.BANKING_DATA_ANALYSIS,
            ConsentScope.ML_IMPROVEMENT: _ConsentType.ML_IMPROVEMENT,
        }
        return mapping.get(scope, _ConsentType.PDN_DIALOGUE)
