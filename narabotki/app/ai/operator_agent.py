"""OperatorAgent — единый агент-оператор всех инструментов Сбера.

Три вариации одного агента (retail / business / employee):
- **Вариация = системный промпт + доступные инструменты** (RBAC по роли);
- **Контекст один на пользователя** — RoleContextManager (v2.1):
  общая обесличенная история видна во всех ролях, чувствительные данные —
  в ролевых анклавах (банковская тайна 395-1 ст. 26, ТК РФ ст. 86–88);
- Каждое обращение к инструменту проверяет роль и согласие (152-ФЗ ст. 9).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from assistant.domain import ConsentScope, Role
from assistant.role_context import ConsentRequiredError, RoleContextManager
from app.ai.calculations import annuity_payment, needs_calculation
from app.ai.rag import RagIndex

# --------------------------------------------------------------------------
# Реестр инструментов Сбера с RBAC по ролям
# --------------------------------------------------------------------------

ToolHandler = Callable[..., Awaitable[dict[str, Any]]]


class ToolNotAllowedError(Exception):
    """Инструмент недоступен в активной роли."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


@dataclass(frozen=True)
class SberTool:
    """Инструмент агент-оператора с ограничением по ролям и согласиям."""

    name: str
    description: str
    allowed_roles: frozenset[Role]
    required_consent: ConsentScope | None = None
    variation: str = "общий"  # подсказка: для какой вариации инструмент


DEFAULT_SBER_TOOLS: list[SberTool] = [
    SberTool(
        name="rag_search",
        description="Поиск по документам и истории пользователя в текущей роли",
        allowed_roles=frozenset(Role),
    ),
    SberTool(
        name="calculator",
        description="Точные финансовые расчёты (Decimal, без LLM-арифметики)",
        allowed_roles=frozenset(Role),
    ),
    SberTool(
        name="client_products",
        description="Продукты клиента СберБанка: карты, вклады, кредиты (физлицо)",
        allowed_roles=frozenset({Role.RETAIL}),
        required_consent=ConsentScope.BANKING_DATA_ANALYSIS,
        variation="физлицо",
    ),
    SberTool(
        name="business_accounts",
        description="Счета и балансы компании (юр. лицо); режим банковской тайны",
        allowed_roles=frozenset({Role.BUSINESS}),
        required_consent=ConsentScope.BANKING_DATA_ANALYSIS,
        variation="юр. лицо",
    ),
    SberTool(
        name="business_payment_draft",
        description="Черновик платёжного поручения (подписывает только пользователь)",
        allowed_roles=frozenset({Role.BUSINESS}),
        required_consent=ConsentScope.BANKING_DATA_ANALYSIS,
        variation="юр. лицо",
    ),
    SberTool(
        name="hr_directory",
        description="Служебный справочник сотрудника (HR-контур, ТК РФ ст. 86–88)",
        allowed_roles=frozenset({Role.EMPLOYEE}),
        variation="сотрудник",
    ),
]


class SberToolRegistry:
    """Реестр инструментов: роль + согласие проверяются перед каждым вызовом."""

    def __init__(
        self,
        consents: Any,  # ConsentPort
        handlers: dict[str, ToolHandler],
        tools: list[SberTool] | None = None,
    ) -> None:
        self._consents = consents
        self._handlers = handlers
        self._tools = {t.name: t for t in (tools or DEFAULT_SBER_TOOLS)}

    def tools_for(self, role: Role) -> list[SberTool]:
        """Инструменты, доступные в роли (вариация агента)."""
        return [t for t in self._tools.values() if role in t.allowed_roles]

    def describe_for(self, role: Role) -> str:
        return "\n".join(
            f"- {t.name}: {t.description}" for t in self.tools_for(role)
        ) or "—"

    async def call(
        self,
        *,
        role: Role,
        user_ref: str,
        tool_name: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Выполнить инструмент с проверкой RBAC и согласия."""
        tool = self._tools.get(tool_name)
        if tool is None:
            raise ToolNotAllowedError(f"Неизвестный инструмент: {tool_name}")
        if role not in tool.allowed_roles:
            raise ToolNotAllowedError(
                f"Инструмент «{tool_name}» недоступен в роли {role.value} "
                f"(допустимые роли: {', '.join(r.value for r in tool.allowed_roles)})"
            )
        if tool.required_consent is not None:
            if not await self._consents.check(
                user_ref, tool.required_consent.value, scope=tool.name
            ):
                raise ConsentRequiredError(
                    tool.required_consent,
                    message=f"Требуется согласие: {tool_name}",
                )
        handler = self._handlers.get(tool_name)
        if handler is None:
            return {"status": "not_configured", "tool": tool_name}
        return await handler(**kwargs)


# --------------------------------------------------------------------------
# Агент-оператор
# --------------------------------------------------------------------------

OPERATOR_SYSTEM_PROMPT = """Ты — Универсальный помощник Сбера (агент-оператор).
Ты один и тот же агент для всех ролей пользователя, но сейчас работаешь в вариации
«{variation}».

Вариация: {variation} — {role_description}

Доступные инструменты СберAPI в этой вариации:
{tools}

Правила:
1. Отвечай на русском, кратко, точно; финансовую арифметику выполняет калькулятор.
2. Не раскрывай данные других клиентов и чужие анклавы (банковская тайна — 395-1 ст. 26).
3. Чувствительные данные текущей роли не выводи в общий контекст без обесличивания.
4. Помечай сомнительные утверждения; не выдумывай факты."""


@dataclass
class AgentResponse:
    answer: str
    role: str
    tools_used: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    ai_generated: bool = True  # маркировка ИИ (Кодекс этики ИИ)


class OperatorAgent:
    """Единый агент-оператор: один контекст, три вариации, RBAC-инструменты."""

    def __init__(
        self,
        *,
        rcm: RoleContextManager,
        gigachat: Any,  # GigaChatClient или фейк
        registry: SberToolRegistry,
        rag: RagIndex | None = None,
        model: str = "GigaChat-3-Pro",
    ) -> None:
        self._rcm = rcm
        self._gigachat = gigachat
        self._registry = registry
        self._rag = rag
        self._model = model

    async def respond(self, question: str, *, role: Role | None = None) -> AgentResponse:
        """Ответить пользователю в активной (или указанной) роли.

        Единый контекст: вопрос и ответ попадают в общую историю — при
        переключении роли разговор продолжается.
        """
        view = await self._rcm.get_context(role)  # ConsentRequiredError → 403 (обрабатывает API)
        role_str = view["role"]
        role_enum = Role.from_legacy(role_str)
        role_spec = _ROLE_DESCRIPTIONS[role_enum]

        sources: list[dict[str, Any]] = []
        if self._rag is not None:
            hits = await self._rag.search(view["enclave_namespace"], question, k=5)
            sources = [{"content": h.content, "score": round(h.score, 4)} for h in hits]

        tools_used: list[str] = []
        calculation = None
        if needs_calculation(question):
            calculation = await self._registry.call(
                role=role_enum, user_ref=self._rcm.user_ref,
                tool_name="calculator", question=question,
            )
            tools_used.append("calculator")

        system = OPERATOR_SYSTEM_PROMPT.format(
            variation=_VARIATION_NAMES[role_enum],
            role_description=role_spec,
            tools=self._registry.describe_for(role_enum),
        )
        messages = [
            {"role": "system", "content": system},
            *[
                {"role": "user" if m["author"] == "user" else "assistant", "content": m["content"]}
                for m in view["messages"][-10:]
            ],
            {"role": "user", "content": question},
        ]
        if sources:
            messages.insert(1, {
                "role": "system",
                "content": "Источники из документов:\n" + "\n".join(s["content"] for s in sources),
            })

        response = await self._gigachat.generate(messages, model=self._model, temperature=0.6)
        answer = response["choices"][0]["message"]["content"]

        if calculation:
            answer += f"\n\nРасчёт (калькулятор, не ИИ): {calculation.get('result', '—')}"
        answer += "\n\n_Ответ сгенерирован ИИ; проверяйте существенные условия._"

        await self._rcm.add_message("user", question, role=role_enum)
        await self._rcm.add_message("assistant", answer, role=role_enum)

        return AgentResponse(answer=answer, role=role_enum.value, tools_used=tools_used, sources=sources)


_VARIATION_NAMES = {
    Role.RETAIL: "клиент-физлицо (СберБанк Онлайн)",
    Role.BUSINESS: "предприниматель / юр. лицо (СберБизнес)",
    Role.EMPLOYEE: "сотрудник Сбера (HR-контур)",
}

_ROLE_DESCRIPTIONS = {
    Role.RETAIL: (
        "помогай с картами, вкладами, кредитами, платежами физлица; "
        "данные счетов — по согласию banking_data_analysis"
    ),
    Role.BUSINESS: (
        "помогай со счетами, платежами, отчётностью юр. лица; "
        "режим банковской тайны, реквизиты маскируй"
    ),
    Role.EMPLOYEE: (
        "служебные задачи сотрудника; правовое основание — трудовой договор (ТК РФ ст. 86); "
        "ПДн коллег во внешние инструменты не выводить"
    ),
}


async def calculator_handler(question: str, **_: Any) -> dict[str, Any]:
    """Handler инструмента calculator: Decimal-расчёт аннуитета по числам из вопроса."""
    from app.ai.calculations import extract_numbers

    nums = extract_numbers(question)
    if len(nums) >= 2:
        months = int(nums[2]) if len(nums) > 2 else 12
        payment = annuity_payment(nums[0], nums[1], months)
        return {"result": f"аннуитетный платёж {payment} руб./мес. (сумма {nums[0]}, ставка {nums[1]}%, срок {months} мес.)"}
    return {"result": "недостаточно данных для расчёта"}
