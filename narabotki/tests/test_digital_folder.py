"""Тесты цифровой папки: изоляция, TTL, версионирование, удаление."""
from __future__ import annotations

from assistant.digital_folder import (
    DigitalFolderService,
    FactCategory,
    FactSource,
)
from assistant.domain import Role
from tests.support import make_rcm


async def test_add_and_get_fact() -> None:
    """Добавленный факт доступен в своей категории и роли."""
    rcm, store, _, _ = await make_rcm()
    folder = DigitalFolderService(rcm, store)

    fact = await folder.add_fact(
        category=FactCategory.FINANCE,
        key="monthly_income",
        value=150_000,
        source=FactSource.INTEGRATION,
        confidence=0.9,
        role=Role.RETAIL,
    )

    assert fact.version == 1
    facts = await folder.get_facts_by_category(
        FactCategory.FINANCE, role=Role.RETAIL
    )
    assert [f.key for f in facts] == ["monthly_income"]


async def test_versioning_on_repeated_key() -> None:
    """Повторная запись ключа обновляет значение и увеличивает версию."""
    rcm, store, _, _ = await make_rcm()
    folder = DigitalFolderService(rcm, store)

    first = await folder.add_fact(
        category=FactCategory.FINANCE,
        key="monthly_income",
        value=100_000,
        source=FactSource.DIALOGUE,
        confidence=0.7,
        role=Role.RETAIL,
    )
    second = await folder.add_fact(
        category=FactCategory.FINANCE,
        key="monthly_income",
        value=150_000,
        source=FactSource.INTEGRATION,
        confidence=0.95,
        role=Role.RETAIL,
    )

    assert first.id == second.id
    assert second.version == 2
    assert second.value == 150_000
    facts = await folder.get_facts_by_category(
        FactCategory.FINANCE, role=Role.RETAIL
    )
    assert len(facts) == 1


async def test_ttl_archiving() -> None:
    """Истёкший факт скрывается и архивируется фоновой задачей."""
    rcm, store, _, _ = await make_rcm()
    folder = DigitalFolderService(rcm, store)

    await folder.add_fact(
        category=FactCategory.RISKS,
        key="temporary_risk",
        value="просрочка",
        source=FactSource.DIALOGUE,
        confidence=0.8,
        role=Role.RETAIL,
        ttl_seconds=-1,
    )

    visible = await folder.get_facts_by_category(
        FactCategory.RISKS, role=Role.RETAIL
    )
    assert visible == []

    archived = await folder.archive_expired(role=Role.RETAIL)
    assert archived == 1
    assert await folder.archive_expired(role=Role.RETAIL) == 0


async def test_role_isolation() -> None:
    """Факты retail не видны в business и наоборот."""
    rcm, store, _, _ = await make_rcm()
    folder = DigitalFolderService(rcm, store)

    await folder.add_fact(
        category=FactCategory.FINANCE,
        key="retail_only",
        value=1,
        source=FactSource.DIALOGUE,
        confidence=0.8,
        role=Role.RETAIL,
    )
    await folder.add_fact(
        category=FactCategory.BUSINESS,
        key="business_only",
        value=2,
        source=FactSource.DIALOGUE,
        confidence=0.8,
        role=Role.BUSINESS,
    )

    retail_facts = await folder.get_all_facts(role=Role.RETAIL)
    business_facts = await folder.get_all_facts(role=Role.BUSINESS)

    assert {f.key for f in retail_facts} == {"retail_only"}
    assert {f.key for f in business_facts} == {"business_only"}


async def test_confidence_is_clamped() -> None:
    """Уверенность ограничена диапазоном [0, 1]."""
    rcm, store, _, _ = await make_rcm()
    folder = DigitalFolderService(rcm, store)

    fact = await folder.add_fact(
        category=FactCategory.GOALS,
        key="save_for_house",
        value=True,
        source=FactSource.DIALOGUE,
        confidence=0.5,
        role=Role.RETAIL,
    )
    updated = await folder.update_confidence(
        fact.id, 1.5, role=Role.RETAIL
    )

    assert updated is not None
    assert updated.confidence == 1.0
    assert updated.version == 2


async def test_soft_delete() -> None:
    """Удаление архивирует факт и повторно не срабатывает."""
    rcm, store, _, _ = await make_rcm()
    folder = DigitalFolderService(rcm, store)

    fact = await folder.add_fact(
        category=FactCategory.PREFERENCES,
        key="likes_deposits",
        value=True,
        source=FactSource.DIALOGUE,
        confidence=0.6,
        role=Role.RETAIL,
    )

    assert await folder.delete_fact(fact.id, role=Role.RETAIL) is True
    assert await folder.get_fact(fact.id, role=Role.RETAIL) is None
    assert await folder.delete_fact(fact.id, role=Role.RETAIL) is False
