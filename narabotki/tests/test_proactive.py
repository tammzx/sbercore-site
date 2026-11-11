"""Тесты проактивного помощника: триггеры, rate-limit, согласия, dismiss."""
from __future__ import annotations

from assistant.digital_folder import (
    DigitalFolderService,
    FactCategory,
    FactSource,
)
from assistant.domain import ConsentScope, Role
from assistant.explainability import ExplainabilityService
from assistant.ip_funnel import FunnelService
from assistant.proactive import ProactiveService
from tests.support import make_rcm


async def _build() -> tuple[ProactiveService, DigitalFolderService]:
    rcm, store, consents, _ = await make_rcm()
    await consents.grant(
        rcm.user_ref,
        ConsentScope.PDN_DIALOGUE,
        scope=[Role.RETAIL.to_legacy()],
    )
    folder = DigitalFolderService(rcm, store)
    funnel = FunnelService(rcm=rcm, consent_service=consents, store=store)
    proactive = ProactiveService(
        rcm=rcm,
        folder_service=folder,
        funnel_service=funnel,
        store=store,
    )
    return proactive, folder


async def test_large_spending_trigger_and_rate_limit() -> None:
    """Крупная трата порождает сообщение, повтор в тот же день блокируется."""
    proactive, folder = await _build()
    rcm = proactive._rcm  # type: ignore[attr-defined]

    await folder.add_fact(
        category=FactCategory.FINANCE,
        key="large_expense",
        value=250_000,
        source=FactSource.INTEGRATION,
        confidence=0.9,
        role=Role.RETAIL,
    )

    first = await proactive.generate_pending(role=Role.RETAIL)
    assert any(m.trigger_id == "large_spending" for m in first)

    second = await proactive.generate_pending(role=Role.RETAIL)
    assert second == []

    pending = await proactive.get_pending(role=Role.RETAIL)
    assert len(pending) == 1


async def test_tax_trigger_requires_cross_consent() -> None:
    """Без cross_retrieval_retail налоговый триггер не срабатывает."""
    proactive, folder = await _build()
    rcm = proactive._rcm  # type: ignore[attr-defined]

    await folder.add_fact(
        category=FactCategory.BUSINESS,
        key="intent_tax",
        value=True,
        source=FactSource.RING,
        confidence=0.85,
        role=Role.RETAIL,
    )

    blocked = await proactive.generate_pending(role=Role.RETAIL)
    assert not any(m.trigger_id == "tax_self_employed" for m in blocked)

    await rcm._consents.grant(  # type: ignore[attr-defined]
        rcm.user_ref,
        ConsentScope.CROSS_RETRIEVAL_RETAIL,
        scope=["cross_retrieval_retail"],
        from_role=Role.RETAIL.to_legacy(),
        to_role=Role.BUSINESS.to_legacy(),
    )

    allowed = await proactive.generate_pending(role=Role.RETAIL)
    assert any(m.trigger_id == "tax_self_employed" for m in allowed)


async def test_dismiss_hides_message() -> None:
    """Отклонённое сообщение исчезает из очереди."""
    proactive, folder = await _build()

    await folder.add_fact(
        category=FactCategory.FINANCE,
        key="large_expense",
        value=300_000,
        source=FactSource.INTEGRATION,
        confidence=0.9,
        role=Role.RETAIL,
    )
    messages = await proactive.generate_pending(role=Role.RETAIL)
    assert messages

    assert await proactive.dismiss(messages[0].message_id, role=Role.RETAIL) is True
    assert await proactive.get_pending(role=Role.RETAIL) == []
    assert await proactive.dismiss("missing", role=Role.RETAIL) is False


async def test_proactive_has_explanation() -> None:
    """Для проактивного сообщения строится justification с источниками."""
    proactive, folder = await _build()
    rcm = proactive._rcm  # type: ignore[attr-defined]

    await folder.add_fact(
        category=FactCategory.FINANCE,
        key="large_expense",
        value=300_000,
        source=FactSource.INTEGRATION,
        confidence=0.9,
        role=Role.RETAIL,
    )
    messages = await proactive.generate_pending(role=Role.RETAIL)
    assert messages

    explain = ExplainabilityService(rcm, folder)
    justification = await explain.explain_proactive(
        messages[0].message_id, role=Role.RETAIL
    )

    assert justification is not None
    assert justification.data_sources
    assert justification.message_id == messages[0].message_id
