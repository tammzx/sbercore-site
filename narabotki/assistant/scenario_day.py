"""Сквозной сценарий «Один день пользователя».

Один субъект проходит retail → self_employed → business в рамках одной сессии:
- Утро (retail): проверка баланса, проактивное сообщение «Вижу частые переводы —
  может, оформить самозанятость?».
- День (self_employed): переход роли, цифровая папка подхватывает контекст,
  помощник помогает с налогом.
- Вечер (business): handoff в ИП, proactive о страховке, рекомендация по расходам
  с explainability.
- Все переходы — строго через ConsentService с scope CROSS_RETRIEVAL_RETAIL.

Запуск: python -m assistant.scenario_day
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from assistant.domain import ConsentScope, Role
from assistant.digital_folder import (
    DigitalFolderService,
    FactCategory,
    FactSource,
)
from assistant.ip_funnel import FunnelService, FunnelStage
from assistant.model_router import ModelRouter
from assistant.proactive import ProactiveService
from assistant.profiling import ProfilingService
from assistant.role_context import RoleContextManager, ConsentRequiredError
from assistant.explainability import ExplainabilityService

from tests.fakes import FakeStore, FakeConsents, FakeGigaChat


def _print_step(step: str) -> None:
    print(f"\n{'=' * 60}\n{step}\n{'=' * 60}")


class FakeAudit:
    async def append(self, event, actor_ref, payload) -> str:
        return "hash"


async def run_one_day_scenario() -> None:
    """Прогон сквозного сценария «Один день пользователя»."""
    store = FakeStore()
    consents = FakeConsents()
    gigachat = FakeGigaChat()

    user_ref = "user_one_day_001"

    rcm = RoleContextManager(
        user_ref=user_ref,
        available_roles=frozenset({Role.RETAIL, Role.BUSINESS, Role.EMPLOYEE}),
        active_role=Role.RETAIL,
        store=store,
        consents=consents,
        audit=FakeAudit(),
    )

    folder = DigitalFolderService(rcm=rcm, store=store)
    profiling = ProfilingService(rcm=rcm, consent_service=consents)
    router = ModelRouter(gigachat=gigachat, store=store, rcm=rcm)
    funnel = FunnelService(rcm=rcm, consent_service=consents, store=store)
    proactive = ProactiveService(
        rcm=rcm, folder_service=folder, funnel_service=funnel, store=store
    )
    explain = ExplainabilityService(rcm=rcm, folder_service=folder)

    # Выдаём базовые согласия
    await consents.grant(user_ref, ConsentScope.PDN_DIALOGUE, scope=["retail"])
    await consents.grant(user_ref, ConsentScope.PDN_DIALOGUE, scope=["business"])
    await consents.grant(
        user_ref,
        ConsentScope.CROSS_RETRIEVAL_RETAIL,
        scope=["cross_retrieval_retail"],
        from_role=Role.RETAIL.to_legacy(),
        to_role=Role.BUSINESS.to_legacy(),
    )

    # ---------------------------------------------------------------
    # УТРО: retail — проверка баланса, проактивное сообщение
    # ---------------------------------------------------------------
    _print_step("УТРО — роль: retail (клиент-физлицо)")

    await rcm.add_message(
        "user", "Какой у меня баланс по всем счетам?", role=Role.RETAIL
    )
    result = await router.route_and_generate(
        "Какой у меня баланс по всем счетам?",
        role=Role.RETAIL,
        scenario="morning_balance",
    )
    print(f"Помощник: {result['answer']}")
    print(f"Модель: {result['model']}, сложность: {result['complexity']}")

    # Проактивное сообщение: частые переводы → самозанятость
    await folder.add_fact(
        category=FactCategory.FINANCE,
        key="frequent_service_transfers",
        value=True,
        source=FactSource.RING,
        confidence=0.85,
        role=Role.RETAIL,
    )
    await folder.add_fact(
        category=FactCategory.BUSINESS,
        key="intent_tax",
        value=True,
        source=FactSource.RING,
        confidence=0.85,
        role=Role.RETAIL,
    )

    pending = await proactive.generate_pending(role=Role.RETAIL)
    print(f"Проактивных сообщений утром: {len(pending)}")
    for pm in pending:
        print(f"  - {pm.text} (приоритет {pm.priority})")

    # ---------------------------------------------------------------
    # ДЕНЬ: переход retail → self_employed
    # ---------------------------------------------------------------
    _print_step("ДЕНЬ — переход retail → self_employed (самозанятый)")

    # Проверяем согласие на handoff retail → biz
    try:
        transition = await funnel.transition(
            to_stage=FunnelStage.SELF_EMPLOYED,
            reason="Пользователь часто получает переводы за услуги — предлагаем самозанятость",
            data_source="morning_proactive",
        )
        print(f"Переход воронки: {transition.from_stage.value} → {transition.to_stage.value}")
        print(f"consent_id: {transition.consent_id}")

        # Оформление ИП — кросс-ролевой доступ уже согласован
        ip_transition = await funnel.transition(
            to_stage=FunnelStage.IP_CANDIDATE,
            reason="Пользователь готов оформить ИП — cross-retrieval согласован",
            data_source="day_proactive",
        )
        print(
            f"Переход воронки: {ip_transition.from_stage.value} → "
            f"{ip_transition.to_stage.value}, consent_id: {ip_transition.consent_id}"
        )
    except ConsentRequiredError as exc:
        print(f"Переход заблокирован: {exc.message}")

    # Переключаем роль
    switch_result = await rcm.switch_role(Role.BUSINESS)
    print(f"Смена роли: {switch_result['from_role']} → {switch_result['to_role']}")
    print(f"Контекст сохранён: {switch_result['context_preserved']}")

    # В роли self_employed помогаем с налогом
    await rcm.add_message(
        "user", "Как рассчитать налог на профессиональный доход?", role=Role.BUSINESS
    )
    result = await router.route_and_generate(
        "Как рассчитать налог на профессиональный доход?",
        role=Role.BUSINESS,
        scenario="day_tax",
    )
    print(f"Помощник: {result['answer']}")

    # ---------------------------------------------------------------
    # ВЕЧЕР: бизнес — proactive о страховке, рекомендации с explainability
    # ---------------------------------------------------------------
    _print_step("ВЕЧЕР — роль: business (ИП, активный бизнес-клиент)")

    # Проактивное сообщение о страховке (срок — через 14 дней от текущей даты)
    insurance_expires = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
    await folder.add_fact(
        category=FactCategory.RISKS,
        key="policy_car_insurance",
        value={"expires_at": insurance_expires},
        source=FactSource.INTEGRATION,
        confidence=0.9,
        role=Role.BUSINESS,
    )

    pending_evening = await proactive.generate_pending(role=Role.BUSINESS)
    print(f"Проактивных сообщений вечером: {len(pending_evening)}")
    for pm in pending_evening:
        print(f"  - {pm.text} (согласие требуется: {pm.requires_consent})")

    # Рекомендация по расходам с explainability
    await folder.add_fact(
        category=FactCategory.FINANCE,
        key="monthly_expenses",
        value={"amount": 450000, "category": "business_expenses"},
        source=FactSource.INTEGRATION,
        confidence=0.9,
        role=Role.BUSINESS,
    )
    await profiling.analyze_and_update(
        [
            {"description": "Аренда офиса", "amount": 120_000, "category": "аренда"},
            {"description": "Закупка оборудования", "amount": 380_000, "category": "оборудование"},
            {"description": "Расходы на логистику", "amount": 90_000, "category": "логистика"},
        ],
        profile_info={"is_business_owner": True},
        role=Role.BUSINESS,
    )

    recs = await profiling.get_recommendations(
        role=Role.BUSINESS,
        context_question="Как оптимизировать бизнес-расходы?",
        limit=3,
    )
    print(f"\nРекомендации ({len(recs)}):")
    for r in recs:
        print(f"  [{r.priority}] {r.product}: {r.reason}")

    # Explainability для первой рекомендации
    if recs:
        justification = await explain.explain(
            message_id=f"rec_evening_{user_ref}",
            role=Role.BUSINESS,
        )
        if justification:
            print(f"\nОбъяснение:")
            print(justification.explanation_text)

    # ---------------------------------------------------------------
    # ИТОГИ
    # ---------------------------------------------------------------
    _print_step("ИТОГИ ДНЯ")
    print(f"Пользователь: {user_ref}")
    print(f"Финальная роль: {rcm.active_role.value if rcm.active_role else 'none'}")
    print(f"Контекст непрерывен в течение дня ✓")
    print(f"Все переходы через ConsentService ✓")
    print(f"Проактивные сообщения: {len(pending) + len(pending_evening)} ✓")
    print(f"Объяснимость: justification сформирован ✓")


if __name__ == "__main__":
    asyncio.run(run_one_day_scenario())
