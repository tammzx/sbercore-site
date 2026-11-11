"""Согласия: PostgreSQL — источник истины, Redis — кэш решений на 60 с.

Модель согласия (152-ФЗ ст. 9): цель (тип), состав (scope), срок,
способ отзыва, доказательство (evidence_hash). Отзыв мгновенно
инвалидирует кэш.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.audit import AuditLog


class PostgresConsentService:
    """Прод-реализация поверх таблицы consents + Redis-кэша."""

    CACHE_TTL_SECONDS = 60

    CHECK_SQL = """
        SELECT id FROM consents
        WHERE user_ref = :user_ref
          AND consent_type = :consent_type
          AND revoked_at IS NULL
          AND (expires_at IS NULL OR expires_at > now())
          AND (:from_role IS NULL OR from_role = :from_role)
          AND (:to_role IS NULL OR to_role = :to_role)
          AND (:scope_json IS NULL OR scope @> :scope_json::jsonb)
        ORDER BY granted_at DESC
        LIMIT 1
    """

    def __init__(self, session_factory, redis_client=None, audit: AuditLog | None = None) -> None:
        self._session_factory = session_factory
        self._redis = redis_client
        self._audit = audit

    async def _find(
        self,
        user_ref: str,
        consent_type: str,
        *,
        from_role: str | None = None,
        to_role: str | None = None,
        scope: str | None = None,
    ) -> str | None:
        """Найти активное согласие (id) по типу, паре ролей и scope. None — нет."""
        import sqlalchemy as sa

        scope_json = _json([scope]) if scope else None
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    sa.text(self.CHECK_SQL),
                    {
                        "user_ref": user_ref,
                        "consent_type": consent_type,
                        "from_role": from_role,
                        "to_role": to_role,
                        "scope_json": scope_json,
                    },
                )
            ).first()
        return str(row[0]) if row else None

    # -- запись ------------------------------------------------------------

    async def grant(
        self,
        user_ref: str,
        consent_type: str,
        *,
        scope: list[str] | None = None,
        from_role: str | None = None,
        to_role: str | None = None,
        ttl_days: int | None = None,
        evidence: str = "ui",
    ) -> str:
        """Зафиксировать согласие. Возвращает consent_id (UUID)."""
        import sqlalchemy as sa

        evidence_hash = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=ttl_days) if ttl_days else None
        )
        query = sa.text(
            """
            INSERT INTO consents (user_ref, consent_type, scope, from_role, to_role,
                                  granted_at, expires_at, evidence_hash)
            VALUES (:user_ref, :consent_type, :scope::jsonb, :from_role, :to_role,
                    now(), :expires_at, :evidence_hash)
            RETURNING id
            """
        )
        async with self._session_factory() as session:
            consent_id = (
                await session.execute(
                    query,
                    {
                        "user_ref": user_ref,
                        "consent_type": consent_type,
                        "scope": _json(scope or []),
                        "from_role": from_role,
                        "to_role": to_role,
                        "expires_at": expires_at,
                        "evidence_hash": evidence_hash,
                    },
                )
            ).scalar()
            await session.commit()
        await self._invalidate(user_ref, consent_type, from_role, to_role)
        if self._audit:
            await self._audit.append(
                "consent.granted",
                user_ref,
                {"consent_id": str(consent_id), "consent_type": consent_type,
                 "scope": scope or [], "from_role": from_role, "to_role": to_role},
            )
        return str(consent_id)

    async def revoke(self, user_ref: str, consent_id: str) -> None:
        """Отозвать согласие (152-ФЗ ст. 9 ч. 2, ст. 21)."""
        import sqlalchemy as sa

        async with self._session_factory() as session:
            row = (
                await session.execute(
                    sa.text(
                        "UPDATE consents SET revoked_at = now() "
                        "WHERE id = :id AND user_ref = :user_ref AND revoked_at IS NULL "
                        "RETURNING consent_type, from_role, to_role"
                    ),
                    {"id": consent_id, "user_ref": user_ref},
                )
            ).first()
            await session.commit()
        if row:
            await self._invalidate(user_ref, row[0], row[1], row[2])
        if self._audit:
            await self._audit.append(
                "consent.revoked",
                user_ref,
                {"consent_id": consent_id, "consent_type": row[0]},
            )

    # -- чтение ------------------------------------------------------------

    async def check(
        self,
        user_ref: str,
        consent_type: str,
        *,
        from_role: str | None = None,
        to_role: str | None = None,
        scope: str | None = None,
    ) -> bool:
        """Проверить активное согласие. Кэш Redis 60 с, источник — PostgreSQL."""
        cache_key = _cache_key(user_ref, consent_type, from_role, to_role, scope)
        if self._redis is not None:
            cached = await self._redis.get(cache_key)
            if cached is not None:
                return cached == b"1" or cached == "1"

        allowed = await self._find(
            user_ref, consent_type,
            from_role=from_role, to_role=to_role, scope=scope,
        ) is not None

        if self._redis is not None:
            await self._redis.setex(cache_key, self.CACHE_TTL_SECONDS, b"1" if allowed else b"0")
        return allowed

    async def active_consent_id(
        self,
        user_ref: str,
        consent_type: str,
        *,
        from_role: str | None = None,
        to_role: str | None = None,
    ) -> str | None:
        return await self._find(
            user_ref, consent_type, from_role=from_role, to_role=to_role,
        )

    async def list_active(self, user_ref: str) -> list[dict[str, Any]]:
        import sqlalchemy as sa

        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    sa.text(
                        "SELECT id, consent_type, scope, from_role, to_role, granted_at, expires_at "
                        "FROM consents WHERE user_ref = :u AND revoked_at IS NULL "
                        "AND (expires_at IS NULL OR expires_at > now())"
                    ),
                    {"u": user_ref},
                )
            ).mappings()
            return [dict(r) for r in rows]

    # -- внутреннее ----------------------------------------------------------

    async def _invalidate(self, user_ref, consent_type, from_role, to_role) -> None:
        if self._redis is None:
            return
        pattern = f"consent:{user_ref}:{consent_type}:*"
        keys = [k async for k in self._redis.scan_iter(match=pattern, count=100)]
        if keys:
            await self._redis.delete(*keys)


def _cache_key(user_ref, consent_type, from_role, to_role, scope) -> str:
    return (
        f"consent:{user_ref}:{consent_type}:"
        f"{from_role or '-'}:{to_role or '-'}:{scope or '-'}"
    )


def _json(value: list[str]) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)
