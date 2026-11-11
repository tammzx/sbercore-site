"""Тесты единого агент-оператора: один агент, три вариации, RBAC инструментов."""
from __future__ import annotations

import pytest

from app.ai.operator_agent import (
    OperatorAgent,
    SberToolRegistry,
    ToolNotAllowedError,
    calculator_handler,
)
from app.core.audit import InMemoryAuditLog
from assistant.domain import Role, ConsentScope
from assistant.role_context import RoleContextManager, ConsentRequiredError
from tests.fakes import FakeConsents, FakeGigaChat, FakeRag, FakeStore

USER_REF = "u" * 32


async def make_agent(available_roles=None, active_role=Role.RETAIL):
    store, consents = FakeStore(), FakeConsents()
    for r in Role:
        await consents.grant(USER_REF, ConsentScope.PDN_DIALOGUE, scope=[r.to_legacy()])
    rcm = RoleContextManager(
        user_ref=USER_REF,
        available_roles=available_roles or frozenset(Role),
        active_role=active_role,
        store=store,
        consents=consents,
        audit=InMemoryAuditLog(),
    )
    gigachat = FakeGigaChat()
    rag = FakeRag(gigachat)
    registry = SberToolRegistry(
        consents=consents,
        handlers={"calculator": calculator_handler},
    )
    agent = OperatorAgent(rcm=rcm, gigachat=gigachat, registry=registry, rag=rag)
    return agent, rcm, consents, registry


# --------------------------------------------------------------------------
# Один агент — три вариации
# --------------------------------------------------------------------------

async def test_same_agent_serves_all_three_roles():
    """ЕДИНЫЙ агент-оператор обслуживает все три роли последовательно."""
    agent, rcm, _, _ = await make_agent()

    r1 = await agent.respond("Как открыть вклад?")           # retail
    await rcm.switch_role(Role.BUSINESS)
    r2 = await agent.respond("Сделай платёжное поручение")   # business
    await rcm.switch_role(Role.EMPLOYEE)
    r3 = await agent.respond("Найди регламент отпусков")     # employee

    assert r1.role == "retail"
    assert r2.role == "business"
    assert r3.role == "employee"
    # Контекст один: все три диалога в общей истории
    view = await rcm.get_context(Role.RETAIL)
    user_msgs = [m for m in view["messages"] if m["author"] == "user"]
    assert len(user_msgs) == 3


async def test_variation_changes_tools_and_prompt():
    """Вариация роли меняет набор доступных инструментов агента."""
    _, _, _, registry = await make_agent()
    client_tools = {t.name for t in registry.tools_for(Role.RETAIL)}
    business_tools = {t.name for t in registry.tools_for(Role.BUSINESS)}
    employee_tools = {t.name for t in registry.tools_for(Role.EMPLOYEE)}

    assert "client_products" in client_tools and "business_accounts" not in client_tools
    assert "business_accounts" in business_tools and "client_products" not in business_tools
    assert "hr_directory" in employee_tools and "business_accounts" not in employee_tools
    assert "calculator" in client_tools & business_tools & employee_tools  # общий инструмент


# --------------------------------------------------------------------------
# RBAC инструментов
# --------------------------------------------------------------------------

async def test_tool_rejected_for_wrong_role():
    _, _, _, registry = await make_agent(active_role=Role.RETAIL)
    with pytest.raises(ToolNotAllowedError):
        await registry.call(role=Role.RETAIL, user_ref=USER_REF, tool_name="business_accounts")


async def test_tool_requires_consent():
    _, _, consents, registry = await make_agent(active_role=Role.BUSINESS)
    with pytest.raises(ConsentRequiredError):
        await registry.call(role=Role.BUSINESS, user_ref=USER_REF, tool_name="business_accounts")
    # выдаём согласие на работу со счетами — инструмент открывается
    await consents.grant(USER_REF, ConsentScope.BANKING_DATA_ANALYSIS, scope=["business_accounts"])
    result = await registry.call(role=Role.BUSINESS, user_ref=USER_REF, tool_name="business_accounts")
    assert result["status"] == "not_configured"  # handler не подключён — но RBAC пройден


async def test_calculator_tool_exact_decimal():
    _, _, _, registry = await make_agent(active_role=Role.RETAIL)
    result = await registry.call(
        role=Role.RETAIL, user_ref=USER_REF,
        tool_name="calculator", question="Рассчитай платёж по кредиту 1000000 под 12 процентов на 24 месяца",
    )
    assert "47073.47" in result["result"]  # аннуитет 1млн/12%/24мес = 47073.47 руб.


# --------------------------------------------------------------------------
# Общий слой: чувствительное не протекает через агента
# --------------------------------------------------------------------------

async def test_agent_answers_share_history_but_not_enclaves():
    agent, rcm, _, _ = await make_agent(active_role=Role.BUSINESS)

    await rcm.add_message("user", "Секретный баланс: 9876543210", sensitive=True)
    await rcm.switch_role(Role.RETAIL)
    response = await agent.respond("Что было последним вопросом?")

    # В ответе агенту видна общая история, но НЕ содержимое чужого анклава
    view = await rcm.get_context(Role.RETAIL)
    assert all("9876543210" not in m["content"] for m in view["messages"])
    assert "9876543210" not in response.answer
