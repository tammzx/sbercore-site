"""JWT-сессии. Внутренний токен после OIDC-логина (HS256, короткий TTL)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.core.roles import Role

SESSION_TTL = timedelta(hours=12)
ALGORITHM = "HS256"


def create_session_token(
    *,
    user_id: str,
    user_ref: str,
    available_roles: list[str],
    secret: str,
    active_role: Role | None = None,
) -> str:
    """Внутренняя JWT-сессия. Роли берутся из claims SberID и хранятся в токене."""
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": user_ref,               # псевдоним, не сырой SberID
        "uid": user_id,                # сырой id — только для auth-модуля
        "roles": available_roles,      # доступные роли из claims SberID
        "iat": now,
        "exp": now + SESSION_TTL,
        "typ": "session",
    }
    if active_role:
        payload["active_role"] = active_role.value
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_session_token(token: str, secret: str) -> dict[str, Any]:
    return jwt.decode(token, secret, algorithms=[ALGORITHM])
