"""Цифровая папка пользователя — структурированное хранилище фактов.

Все факты хранятся в ролевом анклаве (изоляция сохраняется):
- личные факты — в namespace {user_ref}:{role}:facts
- общие (обезличенные) — в unified namespace {user_ref}:unified:facts

Поддерживается TTL (истечение срока действия) и версионирование фактов.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from assistant.context_store import ContextStore
from assistant.domain import Role
from assistant.role_context import RoleContextManager, RoleNotAvailableError


class FactCategory(str, Enum):
    """Категории фактов в цифровой папке."""

    FINANCE = "finance"         # финансовые факты (счета, вклады, кредиты)
    BUSINESS = "business"       # бизнес-факты (ИП, налоги, контрагенты)
    PREFERENCES = "preferences" # предпочтения клиента
    GOALS = "goals"             # цели пользователя
    RISKS = "risks"             # риски (просрочки, большие траты)
    FAMILY = "family"           # семейные обстоятельства


class FactSource(str, Enum):
    """Источник факта."""

    DIALOGUE = "dialogue"       # из диалога с помощником
    INTEGRATION = "integration" # из внешней интеграции (СберБизнес, СБОЛ)
    RING = "ring"               # из кольцевой нейросети (assistant/ring_network.py)


@dataclass
class Fact:
    """Один факт в цифровой папке пользователя."""

    id: str
    user_ref: str
    role: Role
    category: FactCategory
    key: str
    value: Any
    source: FactSource
    confidence: float  # [0.0, 1.0]
    created_at: str
    expires_at: str | None  # TTL, если None — бессрочно
    version: int = 1
    archived: bool = False

    def is_expired(self) -> bool:
        """Проверяет, истёк ли срок действия факта."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > datetime.fromisoformat(self.expires_at)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["role"] = self.role.value
        d["category"] = self.category.value
        d["source"] = self.source.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Fact":
        return cls(
            id=data["id"],
            user_ref=data["user_ref"],
            role=Role(data["role"]),
            category=FactCategory(data["category"]),
            key=data["key"],
            value=data["value"],
            source=FactSource(data["source"]),
            confidence=data["confidence"],
            created_at=data["created_at"],
            expires_at=data.get("expires_at"),
            version=data.get("version", 1),
            archived=data.get("archived", False),
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DigitalFolderService:
    """Сервис цифровой папки: CRUD фактов с изоляцией по ролям и TTL.

    Факты пишутся в ролевой анклав через ContextStore:
    - namespace {user_ref}:{role}:facts — личные факты роли
    - namespace {user_ref}:unified:facts — общие (обезличенные) факты

    ARCHIVE_EXPIRED_INTERVAL_SECONDS — интервал фоновой проверки истечения.
    """

    ARCHIVE_EXPIRED_INTERVAL_SECONDS = 60 * 60  # 1 час

    def __init__(
        self,
        rcm: RoleContextManager,
        store: ContextStore,
    ) -> None:
        self._rcm = rcm
        self._store = store

    # -- внутренние хелперы ----------------------------------------------------

    def _facts_namespace(self, role: Role | None = None) -> str:
        """Возвращает namespace для фактов роли (или unified для общих)."""
        role = role or self._rcm.active_role
        if role is None:
            raise RoleNotAvailableError(role)  # type: ignore[arg-type]
        return f"{self._rcm.user_ref}:{role.value}:facts"

    def _unified_facts_namespace(self) -> str:
        return f"{self._rcm.user_ref}:unified:facts"

    async def _load_facts(self, namespace: str) -> dict[str, Fact]:
        """Загрузить все факты из указанного namespace."""
        state = await self._store.load(namespace)
        raw_facts = state.get("facts", {})
        return {fid: Fact.from_dict(f) for fid, f in raw_facts.items()}

    async def _save_facts(
        self,
        namespace: str,
        facts: dict[str, Fact],
    ) -> None:
        """Сохранить все факты в указанный namespace."""
        state = await self._store.load(namespace)
        state["facts"] = {fid: f.to_dict() for fid, f in facts.items()}
        state["updated_at"] = _now()
        await self._store.save(namespace, state)

    # -- публичный API ---------------------------------------------------------

    async def add_fact(
        self,
        *,
        category: FactCategory,
        key: str,
        value: Any,
        source: FactSource,
        confidence: float,
        role: Role | None = None,
        ttl_seconds: int | None = None,
    ) -> Fact:
        """Добавить или обновить факт в цифровой папке роли.

        Если факт с таким ключом уже существует в этой категории и роли —
        обновляется значение, увеличивается версия, сохраняется история.
        """
        role = role or self._rcm.active_role
        if role is None:
            raise RoleNotAvailableError(role)  # type: ignore[arg-type]

        namespace = self._facts_namespace(role)
        facts = await self._load_facts(namespace)

        # Ищем существующий факт по ключу в этой категории
        existing_id = None
        for fid, fact in facts.items():
            if fact.key == key and fact.category == category and not fact.archived:
                existing_id = fid
                break

        expires_at = None
        if ttl_seconds is not None:
            expires_at = (
                datetime.now(timezone.utc)
                + timedelta(seconds=ttl_seconds)
            ).isoformat()

        if existing_id is not None:
            fact = facts[existing_id]
            fact.value = value
            fact.source = source
            fact.confidence = confidence
            fact.version += 1
            fact.expires_at = expires_at
            fact.archived = False
        else:
            fact = Fact(
                id=str(uuid.uuid4()),
                user_ref=self._rcm.user_ref,
                role=role,
                category=category,
                key=key,
                value=value,
                source=source,
                confidence=confidence,
                created_at=_now(),
                expires_at=expires_at,
            )

        facts[fact.id] = fact
        await self._save_facts(namespace, facts)
        return fact

    async def get_facts_by_category(
        self,
        category: FactCategory,
        *,
        role: Role | None = None,
        include_archived: bool = False,
    ) -> list[Fact]:
        """Получить все неархивные факты указанной категории для роли."""
        role = role or self._rcm.active_role
        if role is None:
            raise RoleNotAvailableError(role)  # type: ignore[arg-type]

        namespace = self._facts_namespace(role)
        facts = await self._load_facts(namespace)

        result = []
        for fact in facts.values():
            if fact.category != category:
                continue
            if fact.archived and not include_archived:
                continue
            if fact.is_expired():
                continue
            result.append(fact)
        return result

    async def get_fact(self, fact_id: str, *, role: Role | None = None) -> Fact | None:
        """Получить один факт по ID."""
        role = role or self._rcm.active_role
        if role is None:
            raise RoleNotAvailableError(role)  # type: ignore[arg-type]

        namespace = self._facts_namespace(role)
        facts = await self._load_facts(namespace)
        fact = facts.get(fact_id)
        if fact is None or fact.archived or fact.is_expired():
            return None
        return fact

    async def update_confidence(
        self,
        fact_id: str,
        confidence: float,
        *,
        role: Role | None = None,
    ) -> Fact | None:
        """Обновить уровень уверенности факта."""
        role = role or self._rcm.active_role
        if role is None:
            raise RoleNotAvailableError(role)  # type: ignore[arg-type]

        namespace = self._facts_namespace(role)
        facts = await self._load_facts(namespace)
        fact = facts.get(fact_id)
        if fact is None or fact.archived:
            return None

        fact.confidence = max(0.0, min(1.0, confidence))
        fact.version += 1
        await self._save_facts(namespace, facts)
        return fact

    async def delete_fact(
        self,
        fact_id: str,
        *,
        role: Role | None = None,
    ) -> bool:
        """Архивировать факт (мягкое удаление)."""
        role = role or self._rcm.active_role
        if role is None:
            raise RoleNotAvailableError(role)  # type: ignore[arg-type]

        namespace = self._facts_namespace(role)
        facts = await self._load_facts(namespace)
        fact = facts.get(fact_id)
        if fact is None or fact.archived:
            return False

        fact.archived = True
        fact.version += 1
        await self._save_facts(namespace, facts)
        return True

    async def archive_expired(
        self,
        *,
        role: Role | None = None,
    ) -> int:
        """Архивировать все истёкшие факты (фоновая задача).

        Возвращает количество заархивированных фактов.
        """
        role = role or self._rcm.active_role
        if role is None:
            raise RoleNotAvailableError(role)  # type: ignore[arg-type]

        namespace = self._facts_namespace(role)
        facts = await self._load_facts(namespace)

        archived_count = 0
        for fact in facts.values():
            if not fact.archived and fact.is_expired():
                fact.archived = True
                fact.version += 1
                archived_count += 1

        if archived_count > 0:
            await self._save_facts(namespace, facts)
        return archived_count

    async def get_all_facts(
        self,
        *,
        role: Role | None = None,
        include_archived: bool = False,
    ) -> list[Fact]:
        """Получить все факты роли (с фильтрацией по истечении)."""
        role = role or self._rcm.active_role
        if role is None:
            raise RoleNotAvailableError(role)  # type: ignore[arg-type]

        namespace = self._facts_namespace(role)
        facts = await self._load_facts(namespace)

        result = []
        for fact in facts.values():
            if fact.archived and not include_archived:
                continue
            if fact.is_expired():
                continue
            result.append(fact)
        return result
