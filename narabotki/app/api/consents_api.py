"""Endpoint согласий: список, выдача, отзыв (152-ФЗ ст. 9)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import UserSession, get_user_session
from app.core.roles import ConsentType

router = APIRouter(prefix="/v1/consents", tags=["consents"])


class ConsentOut(BaseModel):
    consent_id: str
    consent_type: str
    scope: list[str]
    from_role: str | None
    to_role: str | None
    granted_at: str
    expires_at: str | None


class GrantRequest(BaseModel):
    consent_type: str = Field(description=f"Один из: {[c.value for c in ConsentType]}")
    scope: list[str] = Field(default_factory=list)
    from_role: str | None = None
    to_role: str | None = None
    ttl_days: int | None = Field(default=None, le=730)


@router.get("", response_model=list[ConsentOut])
async def list_consents(
    request: Request,
    session: UserSession = Depends(get_user_session),
) -> list[ConsentOut]:
    consents = await request.app.state.consents.list_active(session.user_ref)
    return [
        ConsentOut(
            consent_id=str(c["id"]),
            consent_type=c["consent_type"],
            scope=c.get("scope", []),
            from_role=c.get("from_role"),
            to_role=c.get("to_role"),
            granted_at=str(c.get("granted_at")),
            expires_at=str(c["expires_at"]) if c.get("expires_at") else None,
        )
        for c in consents
    ]


@router.post("/grant", response_model=ConsentOut)
async def grant_consent(
    body: GrantRequest,
    request: Request,
    session: UserSession = Depends(get_user_session),
) -> ConsentOut:
    """Выдать согласие. evidence — факт подтверждения в UI (хешируется)."""
    if body.consent_type not in [c.value for c in ConsentType]:
        raise HTTPException(status_code=422, detail="Неизвестный тип согласия")
    consent_id = await request.app.state.consents.grant(
        session.user_ref,
        body.consent_type,
        scope=body.scope,
        from_role=body.from_role,
        to_role=body.to_role,
        ttl_days=body.ttl_days,
        evidence=f"ui:{session.user_ref}",
    )
    consents = await request.app.state.consents.list_active(session.user_ref)
    for c in consents:
        if str(c["id"]) == consent_id:
            return ConsentOut(
                consent_id=consent_id,
                consent_type=c["consent_type"],
                scope=c.get("scope", []),
                from_role=c.get("from_role"),
                to_role=c.get("to_role"),
                granted_at=str(c.get("granted_at")),
                expires_at=str(c["expires_at"]) if c.get("expires_at") else None,
            )
    raise HTTPException(status_code=500, detail="Согласие не найдено после выдачи")


@router.delete("/{consent_id}")
async def revoke_consent(
    consent_id: str,
    request: Request,
    session: UserSession = Depends(get_user_session),
) -> dict[str, str]:
    """Отозвать согласие — мгновенно (инвалидация кэша, аудит)."""
    await request.app.state.consents.revoke(session.user_ref, consent_id)
    return {"status": "revoked", "consent_id": consent_id}
