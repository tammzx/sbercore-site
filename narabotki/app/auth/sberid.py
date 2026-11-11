"""SberID OAuth 2.0 (OIDC code flow) — аутентификация и claims ролей.

State/nonce хранятся в Redis с TTL 10 минут (CSRF-защита).
Роли пользователя определяются claims SberID и НЕ принимаются из тела запроса.
"""
from __future__ import annotations

import secrets
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from app.auth.jwt_handler import create_session_token
from app.config import settings
from app.core.roles import Role

router = APIRouter(prefix="/auth/sberid", tags=["auth"])

SBERID_AUTH_URL = "https://id.sberbank.ru/oidc/authorize"
SBERID_TOKEN_URL = "https://id.sberbank.ru/oidc/token"
SBERID_USERINFO_URL = "https://id.sberbank.ru/oidc/userinfo"

STATE_TTL_SECONDS = 600
# Роль -> claim-маркер SberID (пример; точные claim'ы согласуются при подключении)
ROLE_CLAIMS = ("client", "business", "employee")


@router.get("/login")
async def sberid_login(request: Request) -> dict[str, str]:
    """Начать OIDC flow: вернуть URL авторизации SberID."""
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    redis = request.app.state.redis
    await redis.setex(f"sberid:state:{state}", STATE_TTL_SECONDS, nonce)

    params = {
        "client_id": settings.SBERID_CLIENT_ID,
        "redirect_uri": settings.SBERID_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid profile email phone",  # минимальные scope
        "state": state,
        "nonce": nonce,
        "acr_values": "password",
    }
    return {"login_url": f"{SBERID_AUTH_URL}?{urlencode(params)}"}


@router.get("/callback")
async def sberid_callback(code: str, state: str, request: Request, response: Response) -> dict[str, Any]:
    """Обработать callback: state → токены → профиль → JWT-сессия."""
    redis = request.app.state.redis
    stored_nonce = await redis.get(f"sberid:state:{state}")
    if not stored_nonce:
        raise HTTPException(status_code=400, detail="Invalid state")
    await redis.delete(f"sberid:state:{state}")

    async with httpx.AsyncClient(timeout=15.0) as http:
        token_resp = await http.post(
            SBERID_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.SBERID_REDIRECT_URI,
                "client_id": settings.SBERID_CLIENT_ID,
                "client_secret": settings.SBERID_CLIENT_SECRET,
            },
        )
        token_resp.raise_for_status()
        tokens = token_resp.json()

        userinfo_resp = await http.get(
            SBERID_USERINFO_URL,
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        userinfo_resp.raise_for_status()
        userinfo = userinfo_resp.json()

    # Роли из claims SberID (не из запроса пользователя)
    claimed = userinfo.get("roles") or [r for r in ROLE_CLAIMS if userinfo.get(f"role_{r}")]
    available_roles = list(claimed) or ["client"]  # дефолтная роль

    user_ref = request.app.state.pseudonymize(str(userinfo["sub"]))
    token = create_session_token(
        user_id=str(userinfo["sub"]),
        user_ref=user_ref,
        available_roles=available_roles,
        secret=settings.JWT_SECRET,
        active_role=Role(available_roles[0]),
    )
    response.set_cookie(
        key="session",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=int(settings.SESSION_TTL_SECONDS),
    )
    await request.app.state.audit.append(
        "auth.login",
        user_ref,
        {"roles": available_roles, "nonce_checked": True},
    )
    return {"status": "authenticated", "roles": available_roles}
