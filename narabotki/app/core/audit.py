"""Неизменяемый аудит-журнал с хеш-цепочкой (152-ФЗ ст. 18.1 ч. 2).

Каждая запись: ``entry_hash = sha256(prev_hash || canonical(payload))``.
Изменение/удаление записей запрещено на уровне БД (см. db/init.sql).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Protocol


def canonical(payload: dict[str, Any]) -> str:
    """Детерминированная сериализация для хеширования."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class AuditLog(Protocol):
    """Интерфейс аудита. Реализации: PgAuditLog (прод), InMemoryAuditLog (тесты)."""

    async def append(self, event: str, actor_ref: str, payload: dict[str, Any]) -> str:
        """Записать событие, вернуть entry_hash."""
        ...


class InMemoryAuditLog:
    """Тестовая реализация: список записей + проверяемая хеш-цепочка."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def append(self, event: str, actor_ref: str, payload: dict[str, Any]) -> str:
        prev_hash = self.entries[-1]["entry_hash"] if self.entries else "GENESIS"
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "actor_ref": actor_ref,
            "payload": payload,
            "prev_hash": prev_hash,
        }
        record["entry_hash"] = hashlib.sha256(
            (prev_hash + canonical(record)).encode("utf-8")
        ).hexdigest()
        self.entries.append(record)
        return record["entry_hash"]

    def find(self, event: str) -> list[dict[str, Any]]:
        return [e for e in self.entries if e["event"] == event]


class PgAuditLog:
    """Прод-реализация поверх PostgreSQL (таблица audit_log, append-only)."""

    INSERT = """
        INSERT INTO audit_log (event, actor_ref, payload, prev_hash, entry_hash)
        VALUES (:event, :actor_ref, :payload::jsonb, :prev_hash, :entry_hash)
        RETURNING entry_hash
    """
    LAST_HASH = "SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1"

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def append(self, event: str, actor_ref: str, payload: dict[str, Any]) -> str:
        import sqlalchemy as sa

        async with self._session_factory() as session:
            last = (await session.execute(sa.text(self.LAST_HASH))).scalar()
            prev_hash = last or "GENESIS"
            record = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": event,
                "actor_ref": actor_ref,
                "payload": payload,
                "prev_hash": prev_hash,
            }
            entry_hash = hashlib.sha256(
                (prev_hash + canonical(record)).encode("utf-8")
            ).hexdigest()
            await session.execute(
                sa.text(self.INSERT),
                {
                    "event": event,
                    "actor_ref": actor_ref,
                    "payload": canonical(payload),
                    "prev_hash": prev_hash,
                    "entry_hash": entry_hash,
                },
            )
            await session.commit()
            return entry_hash
