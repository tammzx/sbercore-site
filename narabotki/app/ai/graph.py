"""Диалоговый граф (LangGraph): consent gate → RAG → генерация → критик → code gate.

Все системные промпты псевдонимизированы: в LLM уходит только
контекст активной роли, реквизиты замаскированы.
"""
from __future__ import annotations

import json
from typing import Annotated, Any, TypedDict

import operator

from app.ai.calculations import annuity_payment, needs_calculation
from app.ai.gigachat_client import GigaChatClient
from app.ai.rag import RagIndex
from app.core.roles import Role

HISTORY_WINDOW = 10

ROLE_SYSTEM_PROMPTS: dict[Role, str] = {
    Role.CLIENT: (
        "Ты — помощник клиента СберБанка. Отвечай кратко и точно на русском. "
        "Никогда не раскрывай данные других клиентов. Финансовые расчёты выполняет "
        "отдельный калькулятор — не считай сам. Помечай сомнительные утверждения."
    ),
    Role.BUSINESS: (
        "Ты — помощник предпринимателя (СберБизнес). Помогай со счетами, платежами, "
        "отчётностью. Не раскрывай сведения, составляющие банковскую тайну, в общих "
        "примерах. Реквизиты в ответах маскируй."
    ),
    Role.EMPLOYEE: (
        "Ты — помощник сотрудника Сбера (внутренний HR-контур). Используй только "
        "служебные данные, разрешённые ролью. Не размещай персональные данные "
        "коллег во внешних инструментах. Основание обработки — трудовой договор (ТК РФ ст. 86)."
    ),
}


class DialogueState(TypedDict):
    user_ref: str
    role: str
    namespace: str
    question: str
    history: list[dict[str, str]]
    sources: list[dict[str, Any]]
    draft: str
    critic: dict[str, Any] | None
    needs_calculation: bool
    calculation: str | None
    final: str
    messages_out: Annotated[list[dict[str, str]], operator.add]


def build_graph(gigachat: GigaChatClient, rag: RagIndex):
    """Собрать граф диалога. Узел consent gate выполнен ДО графа (в API-слое):
    согласие проверяет RoleContextManager.get_context(); сюда попадает
    только уже разрешённый к обработке контекст."""
    from langgraph.graph import END, START, StateGraph

    workflow = StateGraph(DialogueState)

    # --- узлы ---

    async def retrieve_context(state: DialogueState) -> dict[str, Any]:
        """RAG строго в namespace роли + история диалога."""
        hits = await rag.search(state["namespace"], state["question"], k=5)
        return {
            "sources": [{"content": h.content, "score": round(h.score, 4)} for h in hits],
        }

    async def generate_draft(state: DialogueState) -> dict[str, Any]:
        """Черновик ответа через GigaChat (контекст роли + источники)."""
        system = ROLE_SYSTEM_PROMPTS[Role(state["role"])]
        context_block = "\n".join(s["content"] for s in state["sources"])
        messages = [
            {"role": "system", "content": system},
            {"role": "system", "content": f"Источники:\n{context_block or '—'}"},
            *state["history"][-HISTORY_WINDOW:],
            {"role": "user", "content": state["question"]},
        ]
        response = await gigachat.generate(messages, temperature=0.6)
        draft = response["choices"][0]["message"]["content"]
        return {"draft": draft, "needs_calculation": needs_calculation(state["question"])}

    async def critic_check(state: DialogueState) -> dict[str, Any]:
        """Второй проход: критик проверяет черновик на выдумки и корректность."""
        prompt = (
            "Ты — критик ответов банковского ассистента. Проверь черновик на: "
            "1) соответствие источникам; 2) отсутствие выдуманных фактов; "
            "3) юридическую корректность. Ответь строго JSON:\n"
            '{"verdict": "pass" | "fail", "issues": ["..."]}\n\n'
            f"Черновик:\n{state['draft']}\n\n"
            f"Источники:\n" + "\n".join(s["content"] for s in state["sources"])
        )
        response = await gigachat.generate(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        raw = response["choices"][0]["message"]["content"]
        try:
            verdict = json.loads(raw)
        except json.JSONDecodeError:
            verdict = {"verdict": "fail", "issues": ["критик вернул неструктурированный ответ"]}
        return {"critic": verdict}

    async def code_gate(state: DialogueState) -> dict[str, Any]:
        """Финансовые вычисления — Decimal, без LLM-арифметики."""
        if not state.get("needs_calculation"):
            return {}
        nums = _extract(state["question"])
        try:
            if len(nums) >= 2:
                amount, rate = nums[0], nums[1]
                months = int(nums[2]) if len(nums) > 2 else 12
                payment = annuity_payment(amount, rate, months)
                return {"calculation": f"Аннуитетный платёж: {payment} руб./мес."}
        except (ValueError, ArithmeticError):
            return {"calculation": "Не удалось корректно выполнить расчёт по введённым данным."}
        return {}

    async def finalize(state: DialogueState) -> dict[str, Any]:
        """Финальный ответ: маркировка ИИ, учёт вердикта критика, расчёт."""
        critic = state.get("critic") or {}
        if critic.get("verdict") == "fail":
            final = (
                "Не могу подтвердить точность ответа по источникам. "
                "Переформулируйте вопрос или обратитесь к специалисту."
            )
        else:
            parts = [state["draft"]]
            if state.get("calculation"):
                parts.append(f"Расчёт (выполнен калькулятором, не ИИ): {state['calculation']}")
            final = "\n\n".join(parts)
        final += "\n\n_Ответ сгенерирован ИИ; проверяйте существенные условия._"
        return {"final": final}

    # --- сборка ---

    workflow.add_node("retrieve_context", retrieve_context)
    workflow.add_node("generate_draft", generate_draft)
    workflow.add_node("critic_check", critic_check)
    workflow.add_node("code_gate", code_gate)
    workflow.add_node("finalize", finalize)

    workflow.add_edge(START, "retrieve_context")
    workflow.add_edge("retrieve_context", "generate_draft")
    workflow.add_edge("generate_draft", "critic_check")
    workflow.add_conditional_edges(
        "critic_check",
        lambda s: "fail" if (s.get("critic") or {}).get("verdict") == "fail" else "ok",
        {"fail": "finalize", "ok": "code_gate"},
    )
    workflow.add_edge("code_gate", "finalize")
    workflow.add_edge("finalize", END)

    return workflow.compile()


def _extract(text: str) -> list:
    from app.ai.calculations import extract_numbers

    return extract_numbers(text)
