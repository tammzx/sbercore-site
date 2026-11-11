"""Общие помощники для тестов модулей v2.3 (без сети и БД)."""
from __future__ import annotations

from typing import Any

from assistant.domain import Role
from assistant.role_context import RoleContextManager
from tests.fakes import FakeConsents, FakeStore


class RecordingAudit:
    """Аудит в памяти: фиксирует события и отдаёт детерминированный хеш."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def append(
        self,
        event: str,
        actor_ref: str,
        payload: dict[str, Any],
    ) -> str:
        self.entries.append(
            {"event": event, "actor_ref": actor_ref, "payload": payload}
        )
        return f"hash-{len(self.entries)}"


async def make_rcm(
    *,
    active_role: Role = Role.RETAIL,
    roles: set[Role] | None = None,
    user_ref: str = "u" * 32,
) -> tuple[RoleContextManager, FakeStore, FakeConsents, RecordingAudit]:
    """Собрать RoleContextManager на фейковой инфраструктуре."""
    store = FakeStore()
    consents = FakeConsents()
    audit = RecordingAudit()
    rcm = RoleContextManager(
        user_ref=user_ref,
        available_roles=frozenset(
            roles or {Role.RETAIL, Role.BUSINESS, Role.EMPLOYEE}
        ),
        active_role=active_role,
        store=store,
        consents=consents,
        audit=audit,
    )
    return rcm, store, consents, audit
