"""Хранилище контекстов: Redis (горячие данные, TTL) + pgvector (долгосрочная история).

Обёртка над app.core.context_manager.ContextStore с реализацией на Redis.
"""
from __future__ import annotations

import json
from typing import Any

from app.core.context_manager import ContextStore as _ContextStore


class ContextStore(_ContextStore):
    """Хранилище контекстов в Redis с TTL.

    Ключи: ctx:{namespace} -> JSON-состояние.
    TTL по умолчанию 15 минут (900 секунд) для сессий.
    """

    def __init__(self, redis_client, ttl_seconds: int = 15 * 60) -> None:
        self._redis = redis_client
        self._ttl = ttl_seconds

    async def load(self, namespace: str) -> dict[str, Any]:
        raw = await self._redis.get(f"ctx:{namespace}")
        if raw is None:
            return {}
        return json.loads(raw)

    async def save(
        self,
        namespace: str,
        state: dict[str, Any],
        ttl_seconds: int | None = None,
    ) -> None:
        await self._redis.setex(
            f"ctx:{namespace}",
            ttl_seconds or self._ttl,
            json.dumps(state, ensure_ascii=False),
        )
