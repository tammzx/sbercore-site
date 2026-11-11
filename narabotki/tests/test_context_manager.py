"""Тесты RoleContextManager v2.1: единый контекст + ролевые анклавы + согласия.

Проверяем ключевые гарантии:
- контекст ОДИН на пользователя (непрерывность при переключении ролей);
- общий слой обесличен (маскирование реквизитов);
- анклавы ролей изолированы (банковская тайна, ТК РФ);
- вывод из анклава — только по согласию context_transfer;
- согласия: выдача/отзыв/правовое основание сотрудника (ТК РФ ст. 86).
"""
from __future__ import annotations

import pytest

from app.core.audit import InMemoryAuditLog
from app.core.context_manager import (
    RoleContextManager,
    RoleNotAvailableError,
)
from app.core.exceptions import ConsentRequiredError
from app.core.roles import ConsentType, Role
from tests.fakes import FakeConsents, FakeStore

USER_REF = "u" * 32


def make_manager(available_roles=None, active_role=Role.CLIENT, **kw) -> tuple[RoleContextManager, FakeStore, FakeConsents]:
    store = kw.pop("store", FakeStore())
    consents = kw.pop("consents", FakeConsents())
    audit = kw.pop("audit", None) or InMemoryAuditLog()
    m = RoleContextManager(
        user_ref=kw.pop("user_ref", USER_REF),
        available_roles=available_roles or frozenset(Role),
        active_role=active_role,
        store=store,
        consents=consents,
        audit=audit,
    )
    return m, store, consents


# --------------------------------------------------------------------------
# Единый контекст: непрерывность при переключении ролей
# --------------------------------------------------------------------------

async def test_unified_context_is_one_for_all_roles():
    """КЛЮЧЕВОЙ СЦЕНАРИЙ v2.1: контекст один — история общая для трёх ролей."""
    m, _, consents = make_manager(active_role=Role.CLIENT)
    await consents.grant(USER_REF, "pdn_dialogue", scope=["client"])
    await consents.grant(USER_REF, "pdn_dialogue", scope=["business"])
    await consents.grant(USER_REF, "pdn_dialogue", scope=["employee"])

    await m.add_message("user", "Как оформить автоплатёж?")            # физлицо
    await m.switch_role(Role.BUSINESS)
    await m.add_message("user", "Проверить.attach платежку по счёту")  # юр. лицо
    await m.switch_role(Role.EMPLOYEE)
    await m.add_message("user", "Собери отчёт по задачам")             # сотрудник

    # Возвращаемся к физлицу — история ВСЕХ трёх вариаций на месте (один контекст)
    view = await m.get_context(Role.CLIENT)
    contents = [msg["content"] for msg in view.messages]
    assert len(contents) == 3
    assert view.is_continuous is True
    assert any("автоплатёж" in c for c in contents)
    assert any("платежку" in c for c in contents)
    assert any("отчёт" in c for c in contents)


async def test_shared_layer_is_sanitized():
    """Общий слой хранится обесличенно: реквизиты маскируются при записи."""
    m, _, consents = make_manager(active_role=Role.CLIENT)
    await consents.grant(USER_REF, "pdn_dialogue", scope=["client"])

    await m.add_message("user", "Переведи на счёт 40817810000012345678")
    view = await m.get_context(Role.CLIENT)
    assert "40817810000012345678" not in view.messages[0]["content"]
    assert "4081****5678" in view.messages[0]["content"]


# --------------------------------------------------------------------------
# Ролевые анклавы: банковская тайна и ТК РФ
# --------------------------------------------------------------------------

async def test_sensitive_data_goes_to_enclave_only():
    """Чувствительное сообщение: сырые данные — только в анклаве роли."""
    m, store, consents = make_manager(active_role=Role.BUSINESS)
    await consents.grant(USER_REF, "pdn_dialogue", scope=["business"])

    await m.add_message("user", "Баланс расчётного счёта 40817810000098765432: 1.2 млн", sensitive=True)

    # В общем слое содержимое скрыто
    view = await m.get_context(Role.BUSINESS)
    shared_contents = [msg["content"] for msg in view.messages]
    assert any("••••••" in c for c in shared_contents)
    assert not any("98765432" in c for c in shared_contents)

    # В анклаве business — сырые данные
    enclave_ns = f"{USER_REF}:business:enclave"
    assert "98765432" in store.data[enclave_ns]["messages"][0]["content"]


async def test_enclave_invisible_in_other_roles():
    """Анклав business не виден в роли client и employee (банковская тайна)."""
    m, _, consents = make_manager(active_role=Role.BUSINESS)
    await consents.grant(USER_REF, "pdn_dialogue", scope=["business"])
    await consents.grant(USER_REF, "pdn_dialogue", scope=["employee"])

    await m.isolate_data("account_balance", "1 200 000 RUB")
    await m.switch_role(Role.EMPLOYEE)
    view = await m.get_context(Role.EMPLOYEE)
    assert view.enclave_facts == {}  # чужой анклав не возвращается

    await m.switch_role(Role.CLIENT)
    assert await m.get_isolated_data("account_balance") is None  # и через accessor


async def test_isolate_data_scoped_by_role():
    """isolate_data пишет в анклав активной роли; чужая роль данных не видит."""
    m, _, consents = make_manager(active_role=Role.CLIENT)
    await consents.grant(USER_REF, "pdn_dialogue", scope=["client"])
    await consents.grant(USER_REF, "pdn_dialogue", scope=["business"])

    await m.isolate_data("cards", ["2202****5678"])
    await m.switch_role(Role.BUSINESS)
    assert await m.get_isolated_data("cards") is None
    await m.switch_role(Role.CLIENT)
    assert await m.get_isolated_data("cards") == ["2202****5678"]


# --------------------------------------------------------------------------
# promote_to_shared: вывод из анклава по согласию
# --------------------------------------------------------------------------

async def test_promote_to_shared_requires_consent():
    m, _, _ = make_manager(active_role=Role.BUSINESS)
    with pytest.raises(ConsentRequiredError) as exc:
        await m.promote_to_shared([0])
    assert exc.value.scope == "business->shared"


async def test_promote_to_shared_with_consent_sanitizes():
    m, store, consents = make_manager(active_role=Role.BUSINESS)
    await consents.grant(USER_REF, "pdn_dialogue", scope=["business"])
    await consents.grant(
        USER_REF, "context_transfer",
        scope=["business->shared"], from_role="business", to_role="shared",
    )
    await m.add_message("user", "Платёж со счёта 40817810000098765432", sensitive=True)

    result = await m.promote_to_shared([0])
    assert result.promoted == 1
    assert result.consent_id is not None

    view = await m.get_context(Role.BUSINESS)
    shared = [msg for msg in view.messages if msg.get("sensitivity") == "shared"]
    promoted_msg = [msg for msg in shared if msg.get("metadata", {}).get("promoted_from")]
    assert promoted_msg, "обесличенная копия должна появиться в общем слое"
    assert "98765432" not in promoted_msg[0]["content"]
    assert promoted_msg[0]["metadata"]["consent_id"] == result.consent_id


# --------------------------------------------------------------------------
# Согласия
# --------------------------------------------------------------------------

async def test_get_context_blocked_without_consent():
    m, _, _ = make_manager(active_role=Role.CLIENT)
    with pytest.raises(ConsentRequiredError) as exc:
        await m.get_context(Role.CLIENT)
    assert exc.value.consent_type == "pdn_dialogue"
    assert exc.value.scope == "client"


async def test_employee_dialogue_needs_no_consent_tk86():
    """Сотрудник: основание — трудовой договор, согласие не требуется."""
    m, _, _ = make_manager(active_role=Role.EMPLOYEE)
    view = await m.get_context(Role.EMPLOYEE)
    assert "legal_base=contract" in view.audit_reason
    assert await m.check_consent(ConsentType.PDN_DIALOGUE, role=Role.EMPLOYEE) is True


async def test_consent_revocation_blocks_context():
    m, _, consents = make_manager(active_role=Role.CLIENT)
    await consents.grant(USER_REF, "pdn_dialogue", scope=["client"])
    assert await m.check_consent(ConsentType.PDN_DIALOGUE, role=Role.CLIENT) is True
    await consents.revoke_by_type(USER_REF, "pdn_dialogue")
    with pytest.raises(ConsentRequiredError):
        await m.get_context(Role.CLIENT)


# --------------------------------------------------------------------------
# Переключение ролей
# --------------------------------------------------------------------------

async def test_switch_role_updates_active_and_preserves_context():
    m, _, consents = make_manager(active_role=Role.CLIENT)
    await consents.grant(USER_REF, "pdn_dialogue", scope=["business"])
    await m.add_message("user", "история в физлице")

    result = await m.switch_role(Role.BUSINESS)
    assert m.active_role is Role.BUSINESS
    assert result.context_preserved is True
    assert result.dialogue_consent_granted is True
    # История общая — ничего «переносить» не пришлось
    view = await m.get_context(Role.BUSINESS)
    assert any("история в физлице" in msg["content"] for msg in view.messages)


async def test_switch_role_rejected_when_unavailable():
    m, _, _ = make_manager(available_roles=frozenset({Role.CLIENT}), active_role=Role.CLIENT)
    with pytest.raises(RoleNotAvailableError):
        await m.switch_role(Role.BUSINESS)


async def test_audit_trail_records_switches():
    audit = InMemoryAuditLog()
    m, _, _ = make_manager(active_role=Role.CLIENT, audit=audit)
    await m.switch_role(Role.BUSINESS)
    await m.switch_role(Role.EMPLOYEE)
    events = [e["event"] for e in audit.entries]
    assert events.count("role.switched") == 2
    # хеш-цепочка цела
    assert audit.entries[1]["prev_hash"] == audit.entries[0]["entry_hash"]
