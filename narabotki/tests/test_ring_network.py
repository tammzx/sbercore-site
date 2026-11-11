"""Тесты кольцевой нейросети: извлечение фактов, TTL, суммаризация."""
from __future__ import annotations

from assistant.digital_folder import DigitalFolderService, FactCategory
from assistant.domain import ConsentScope, Role
from assistant.ring_network import RingConfig, RingNetwork
from tests.support import make_rcm


async def _build(
    *,
    batch_size: int = 10,
    summary_threshold: int = 10,
) -> tuple[RingNetwork, DigitalFolderService]:
    rcm, store, consents, _ = await make_rcm()
    await consents.grant(
        rcm.user_ref,
        ConsentScope.PDN_DIALOGUE,
        scope=[Role.RETAIL.to_legacy()],
    )
    folder = DigitalFolderService(rcm, store)
    ring = RingNetwork(
        rcm,
        folder,
        RingConfig(
            enabled=True,
            batch_size=batch_size,
            summary_threshold=summary_threshold,
        ),
    )
    return ring, folder


async def test_extracts_facts_from_user_dialogue() -> None:
    """Из пользовательского сообщения извлекаются суммы, намерения и семья."""
    ring, _ = await _build()
    rcm = ring._rcm  # type: ignore[attr-defined]

    await rcm.add_message(
        "user",
        "Хочу открыть вклад, сумма 50000 руб, интересует страховка, есть дети",
        role=Role.RETAIL,
    )
    created = await ring.scan_and_extract(role=Role.RETAIL)
    keys = {f.key for f in created}

    assert "intent_open_deposit" in keys
    assert "intent_insurance" in keys
    assert "has_children" in keys
    assert any(
        f.category is FactCategory.FINANCE and f.value == 50000.0
        for f in created
    )


def test_assistant_messages_are_ignored() -> None:
    """Факты извлекаются только из сообщений пользователя."""
    ring, _ = _build_sync()
    facts = ring._extract_from_messages(  # type: ignore[attr-defined]
        [{"author": "assistant", "content": "сумма 50000 руб, откройте вклад"}],
        Role.RETAIL,
    )
    assert facts == []


def _build_sync() -> tuple[RingNetwork, DigitalFolderService]:
    """Создать RingNetwork без async-инфраструктуры (для чистых методов)."""

    class _RCM:
        user_ref = "u" * 32
        active_role = Role.RETAIL

    class _Folder:
        pass

    return RingNetwork(_RCM(), _Folder()), _Folder()  # type: ignore[arg-type]


async def test_scan_does_not_change_dialogue() -> None:
    """Фоновое сканирование не добавляет и не меняет сообщения диалога."""
    ring, _ = await _build()
    rcm = ring._rcm  # type: ignore[attr-defined]

    await rcm.add_message("user", "открыть вклад на 10000 руб", role=Role.RETAIL)
    before = (await rcm.get_context(Role.RETAIL))["messages"]

    await ring.scan_and_extract(role=Role.RETAIL)

    after = (await rcm.get_context(Role.RETAIL))["messages"]
    assert before == after


async def test_summary_replaces_details_after_threshold() -> None:
    """При превышении порога детальные факты архивируются, остаётся сводка."""
    ring, folder = await _build(batch_size=20, summary_threshold=2)
    rcm = ring._rcm  # type: ignore[attr-defined]

    await rcm.add_message("user", "открыть вклад 1000 руб", role=Role.RETAIL)
    await rcm.add_message("user", "кредит 2000 руб", role=Role.RETAIL)

    await ring.scan_and_extract(role=Role.RETAIL)
    facts = await folder.get_all_facts(role=Role.RETAIL)

    summaries = [f for f in facts if f.key.startswith("summary_")]
    assert len(summaries) == 1
    assert summaries[0].category is FactCategory.PREFERENCES
    assert all(not f.key.startswith("intent_") for f in facts)
