"""Зависимости FastAPI: аутентификация, псевдонимизация, фабрика RoleContextManager."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.core.isolation import pseudonymize
from app.core.roles import Role as AppRole
from assistant.domain import Role
from assistant.role_context import RoleContextManager

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> dict[str, Any]:
    """Декодировать JWT-сессию (cookie или Bearer)."""
    token = None
    if creds is not None:
        token = creds.credentials
    else:
        token = request.cookies.get("session")
    if not token:
        raise HTTPException(status_code=401, detail="Требуется аутентификация")
    try:
        payload = pyjwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except pyjwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Сессия недействительна") from exc
    return payload


@dataclass
class UserSession:
    user_id: str          # сырой id (только для auth-операций)
    user_ref: str         # псевдоним — в контур ИИ
    available_roles: list[str]
    active_role: str | None


def get_user_session(user: dict[str, Any] = Depends(get_current_user)) -> UserSession:
    return UserSession(
        user_id=user["uid"],
        user_ref=user["sub"],
        available_roles=user.get("roles", []),
        active_role=user.get("active_role"),
    )


def available_roles(session: UserSession = Depends(get_user_session)) -> frozenset[Role]:
    app_roles = frozenset(AppRole(r) for r in session.available_roles if r in [x.value for x in AppRole])
    return frozenset(Role.from_legacy(r.value) for r in app_roles)


async def get_role_context_manager(
    request: Request,
    session: UserSession = Depends(get_user_session),
    roles: frozenset[Role] = Depends(available_roles),
) -> RoleContextManager:
    """Фабрика менеджера контекста на запрос (псевдоним + роли из JWT)."""
    return await RoleContextManager.create(
        user_ref=session.user_ref,
        available_roles=roles,
        store=request.app.state.context_store,
        consents=request.app.state.consents,
        audit=request.app.state.audit,
        session_ttl_seconds=settings.SESSION_TTL_SECONDS,
    )


def pseudonymize_ref(user_id: str) -> str:
    """Псевдоним пользователя для контура ИИ."""
    return pseudonymize(user_id, settings.PSEUDONYM_KEY)
