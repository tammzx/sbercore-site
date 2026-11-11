"""Тесты объяснимости: источники, согласие, изоляция анклавов."""
from __future__ import annotations

from assistant.digital_folder import (
    DigitalFolderService,
    FactCategory,
    FactSource,
)
from assistant.domain import ConsentScope, Role
from assistant.explainability import ExplainabilityService
from tests.support import make_rcm


async def _build() -> tuple[ExplainabilityService, DigitalFolderService]:
    rcm, store, consents, _ = await make_rcm()
    await consents.grant(
        rcm.user_ref,
        ConsentScope.PDN_DIALOGUE,
        scope=[Role.RETAIL.to_legacy()],
    )
    folder = DigitalFolderService(rcm, store)
    return ExplainabilityService(rcm, folder), folder


async def test_explanation_has_sources_and_consent() -> None:
    """Justification содержит источники, top-3 факта и ссылку на согласие."""
    explain, folder = await _build()

    await folder.add_fact(
        category=FactCategory.FINANCE,
        key="monthly_income",
        value=150_000,
        source=FactSource.INTEGRATION,
        confidence=0.95,
        role=Role.RETAIL,
    )
    await folder.add_fact(
        category=FactCategory.PREFERENCES,
        key="likes_deposits",
        value=True,
        source=FactSource.DIALOGUE,
        confidence=0.6,
        role=Role.RETAIL,
    )

    justification = await explain.explain("rec-1", role=Role.RETAIL)

    assert justification is not None
    assert justification.data_sources
    assert 1 <= len(justification.key_facts) <= 3
    assert justification.consent_id == f"consent_pdn_{Role.RETAIL.value}"
    assert "цифровая папка" in justification.explanation_text


async def test_explanation_respects_role_isolation() -> None:
    """В объяснение retail не попадают факты business-анклава."""
    explain, folder = await _build()

    await folder.add_fact(
        category=FactCategory.BUSINESS,
        key="secret_business_fact",
        value="оборот 10 млн",
        source=FactSource.INTEGRATION,
        confidence=0.99,
        role=Role.BUSINESS,
    )

    justification = await explain.explain("rec-2", role=Role.RETAIL)

    assert justification is not None
    keys = {f["key"] for f in justification.key_facts}
    assert "secret_business_fact" not in keys


async def test_explanation_without_facts() -> None:
    """Без фактов объяснение всё равно формируется и честно об этом говорит."""
    explain, _ = await _build()

    justification = await explain.explain("rec-3", role=Role.RETAIL)

    assert justification is not None
    assert justification.key_facts == []
    assert "нет релевантных фактов" in justification.explanation_text


async def test_explanation_requires_selected_role() -> None:
    """Без активной роли объяснение не строится."""
    explain, _ = await _build()
    rcm = explain._rcm  # type: ignore[attr-defined]
    rcm._inner._active_role = None  # type: ignore[attr-defined]

    assert await explain.explain("rec-4") is None
