"""Endpoint /v1/chat: диалог через единого агент-оператора (один контекст, три вариации)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.deps import get_role_context_manager
from app.ai.operator_agent import OperatorAgent
from assistant.role_context import ConsentRequiredError, RoleContextManager

router = APIRouter(prefix="/v1", tags=["chat"])


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=8000)


class ChatResponse(BaseModel):
    answer: str
    role: str
    tools_used: list[str]
    sources: list[dict[str, Any]]
    context_continuous: bool
    ai_generated: bool = True


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    request: Request,
    rcm: RoleContextManager = Depends(get_role_context_manager),
) -> ChatResponse:
    """Диалог с агентом-оператором в активной вариации роли.

    - Контекст ОДИН на пользователя: история общая, непрерывная;
    - согласия проверяет RoleContextManager (403 при отсутствии);
    - инструменты фильтруются по роли (RBAC) + согласия;
    - чувствительные данные уходят только в анклав активной роли.
    """
    agent = OperatorAgent(
        rcm=rcm,
        gigachat=request.app.state.gigachat,
        registry=request.app.state.tool_registry,
        rag=request.app.state.rag,
    )
    try:
        result = await agent.respond(body.question)
    except ConsentRequiredError:
        raise

    view = await rcm.get_context()
    return ChatResponse(
        answer=result.answer,
        role=result.role,
        tools_used=result.tools_used,
        sources=result.sources,
        context_continuous=view["is_continuous"],
    )
