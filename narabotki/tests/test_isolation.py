"""Тесты изоляции: псевдонимизация, namespace, маскирование реквизитов, аудит."""
from __future__ import annotations

from app.core.audit import InMemoryAuditLog
from app.core.isolation import (
    pseudonymize,
    role_namespace,
    sanitize_for_transfer,
    sanitize_messages,
)
from app.core.roles import Role


def test_pseudonymize_deterministic_and_bound_to_key():
    ref1 = pseudonymize("sberid-123", "key-a")
    ref2 = pseudonymize("sberid-123", "key-a")
    ref3 = pseudonymize("sberid-123", "key-b")
    ref4 = pseudonymize("sberid-999", "key-a")

    assert ref1 == ref2          # детерминирован
    assert ref1 != ref3          # зависит от ключа
    assert ref1 != ref4          # уникален для пользователя
    assert "sberid" not in ref1  # сырой идентификатор не восстанавливается
    assert len(ref1) == 32


def test_role_namespace_isolation():
    ref = pseudonymize("sberid-123", "key-a")
    namespaces = {role_namespace(ref, r) for r in Role}
    assert len(namespaces) == 3  # три роли — три namespace, пересечений нет
    assert all(n.startswith(ref) for n in namespaces)


def test_sanitize_masks_requisites():
    text = "Переведи на счёт 40817810000012345678 с карты 2202200312345678"
    masked = sanitize_for_transfer(text)
    assert "40817810000012345678" not in masked
    assert "2202200312345678" not in masked
    assert "4081****5678" in masked
    assert "2202****5678" in masked


def test_sanitize_messages_no_mutation():
    messages = [{"content": "счёт 40817810000012345678", "author": "user"}]
    masked = sanitize_messages(messages)
    assert "****" in masked[0]["content"]
    assert "40817810000012345678" in messages[0]["content"]  # исходник не тронут


async def test_audit_hash_chain():
    """Хеш-цепочка: каждая запись ссылается на хеш предыдущей."""
    audit = InMemoryAuditLog()
    await audit.append("e1", "user-ref", {"n": 1})
    await audit.append("e2", "user-ref", {"n": 2})
    assert audit.entries[0]["prev_hash"] == "GENESIS"
    assert audit.entries[1]["prev_hash"] == audit.entries[0]["entry_hash"]
    assert audit.entries[0]["entry_hash"] != audit.entries[1]["entry_hash"]
