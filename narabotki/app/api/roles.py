"""Endpoint ролей: список, активная роль, переключение, вывод анклава.

v2.1: контекст один на пользователя — switch_role сохраняет историю;
вывод чувствительных записей в общий слой — отдельный promote-эндпоинт.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import UserSession, available_roles, get_role_context_manager, get_user_session
from assistant.domain import Role
from assistant.role_context import RoleContextManager
from assistant.role_context import ConsentRequiredError, IsolationViolationError, RoleNotAvailableError

router = APIRouter(prefix="/v1/roles", tags=["roles"])


class RoleOut(BaseModel):
    role: str
    legal_base: str
    consent_required: bool
    description: str


class SwitchRequest(BaseModel):
    target: str  # retail | business | employee


class SwitchResponse(BaseModel):
    from_role: str | None
    to_role: str
    context_preserved: bool  # единый контекст сохранён
    dialogue_consent_granted: bool


class PromoteRequest(BaseModel):
    message_ids: list[int] = Field(min_length=1, description="Индексы записей АНКЛАВА активной роли")


class PromoteResponse(BaseModel):
    promoted: int
    consent_id: str | None


@router.get("", response_model=list[RoleOut])
async def list_roles(roles: frozenset[Role] = Depends(available_roles)) -> list[RoleOut]:
    """Доступные вариации (роли) пользователя из claims SberID."""
    from assistant.domain import ROLE_SPECS

    result = []
    for role, spec in ROLE_SPECS.items():
        if role not in roles:
            continue
        legal_base, consent_required, description = spec
        result.append(
            RoleOut(
                role=role.value,
                legal_base=legal_base.value,
                consent_required=consent_required,
                description=description,
            )
        )
    return result


@router.get("/active", response_model=dict[str, Any])
async def active_role(
    session: UserSession = Depends(get_user_session),
) -> dict[str, Any]:
    return {"active_role": session.active_role, "available_roles": session.available_roles}


@router.post("/switch", response_model=SwitchResponse)
async def switch_role(
    body: SwitchRequest,
    rcm: RoleContextManager = Depends(get_role_context_manager),
) -> SwitchResponse:
    """Переключить вариацию (роль). Единый контекст сохраняется автоматически."""
    try:
        target_role = Role.from_legacy(body.target)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Неизвестная роль: {body.target}") from exc
    try:
        result = await rcm.switch_role(target_role)
    except RoleNotAvailableError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return SwitchResponse(
        from_role=result["from_role"],
        to_role=result["to_role"],
        context_preserved=result["context_preserved"],
        dialogue_consent_granted=result["dialogue_consent_granted"],
    )


@router.post("/promote", response_model=PromoteResponse)
async def promote_to_shared(
    body: PromoteRequest,
    rcm: RoleContextManager = Depends(get_role_context_manager),
) -> PromoteResponse:
    """Вывести выбранные записи анклава активной роли в общий контекст.

    Только обесличенно и только при согласии context_transfer:{role}->shared
    (иначе 403 + consent_url).
    """
    try:
        result = await rcm.promote_to_shared(body.message_ids)
    except RoleNotAvailableError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ConsentRequiredError as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "consent_required",
                "consent_type": exc.scope.value,
                "scope": f"{rcm.active_role.value if rcm.active_role else 'unknown'}->shared",
                "consent_url": f"/v1/consents/grant?consent_type={exc.scope.value}&scope={exc.scope.value}",
            },
        ) from exc
    except IsolationViolationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return PromoteResponse(promoted=result.promoted, consent_id=result.consent_id)
