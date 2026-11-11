"""Общие фейковые инфраструктурные реализации для тестов (без сети и БД)."""
from __future__ import annotations

from typing import Any


class FakeRedis:
    """Минимальный async-клиент Redis (get/setex/delete/scan_iter)."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def setex(self, key: str, ttl: int, value: Any) -> None:
        self.data[key] = value

    async def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    async def delete(self, *keys: str) -> None:
        for k in keys:
            self.data.pop(k, None)

    async def scan_iter(self, match: str = "", count: int = 100):
        import fnmatch

        for k in list(self.data):
            if fnmatch.fnmatch(k, match):
                yield k


class FakeStore:
    """ContextStore в памяти — namespace-схема как в проде."""

    def __init__(self) -> None:
        self.data: dict[str, dict] = {}

    async def load(self, namespace: str) -> dict:
        return self.data.get(namespace, {})

    async def save(self, namespace: str, state: dict, ttl_seconds: int | None = None) -> None:
        self.data[namespace] = state


class FakeConsents:
    """ConsentPort в памяти. Семантика повторяет PostgresConsentService:
    каждое grant — отдельная запись согласия; check ищет хотя бы одну
    активную запись, подходящую по паре ролей и scope."""

    def __init__(self) -> None:
        self._entries: list[dict] = []
        self._counter = 0

    def _matching(self, user_ref, consent_type, from_role, to_role, scope):
        for entry in self._entries:
            if entry["user_ref"] != user_ref or entry["consent_type"] != consent_type:
                continue
            if entry["revoked"]:
                continue
            if entry["from_role"] != (from_role or None) or entry["to_role"] != (to_role or None):
                continue
            if scope is not None and scope not in entry["scope"]:
                continue
            yield entry

    async def check(self, user_ref, consent_type, *, from_role=None, to_role=None, scope=None) -> bool:
        return next(self._matching(user_ref, consent_type, from_role, to_role, scope), None) is not None

    async def active_consent_id(self, user_ref, consent_type, *, from_role=None, to_role=None):
        entry = next(self._matching(user_ref, consent_type, from_role, to_role, None), None)
        return entry["id"] if entry else None

    async def grant(self, user_ref, consent_type, *, scope=None, from_role=None, to_role=None,
                    ttl_days=None, evidence="ui") -> str:
        self._counter += 1
        consent_id = f"consent-{self._counter}"
        self._entries.append({
            "id": consent_id,
            "user_ref": user_ref,
            "consent_type": consent_type,
            "scope": scope or [],
            "from_role": from_role,
            "to_role": to_role,
            "revoked": False,
        })
        return consent_id

    async def list_active(self, user_ref: str) -> list[dict]:
        """Активные согласия (интерфейс PostgresConsentService)."""
        return [
            {
                "id": e["id"],
                "consent_type": e["consent_type"],
                "scope": e["scope"],
                "from_role": e["from_role"],
                "to_role": e["to_role"],
                "granted_at": "sandbox",
                "expires_at": None,
            }
            for e in self._entries
            if e["user_ref"] == user_ref and not e["revoked"]
        ]

    async def revoke(self, user_ref: str, consent_id: str) -> None:
        """Отзыв по consent_id — сигнатура как у PostgresConsentService."""
        for e in self._entries:
            if e["id"] == consent_id and e["user_ref"] == user_ref:
                e["revoked"] = True

    async def revoke_by_type(self, user_ref, consent_type, *, from_role=None, to_role=None) -> None:
        """Отзыв по типу (удобство тестов)."""
        for entry in self._matching(user_ref, consent_type, from_role, to_role, None):
            entry["revoked"] = True


class FakeRag:
    """RagIndex в памяти с namespace-изоляцией (для песочницы и тестов)."""

    def __init__(self, fake_gigachat: "FakeGigaChat | None" = None) -> None:
        self._gigachat = fake_gigachat or FakeGigaChat()
        self.docs: dict[str, list[str]] = {}

    async def upsert(self, *, namespace: str, role, user_ref: str, texts: list[str]) -> int:
        self.docs.setdefault(namespace, []).extend(texts)
        return len(texts)

    async def search(self, namespace: str, query: str, *, k: int = 5):
        from app.ai.rag import SearchHit

        contents = self.docs.get(namespace, [])
        return [SearchHit(content=c, namespace=namespace, score=0.9) for c in contents[:k]]


class FakeGigaChat:
    """GigaChat-клиент без сети: детерминированные ответы и векторы."""

    async def generate(self, messages, **kwargs):
        return {"choices": [{"message": {"content": f"ЭХО: {messages[-1]['content']}"}}]}

    async def get_embeddings(self, texts, **kwargs):
        # детерминированный «вектор» 8-мерный для тестов
        return [
            [float((hash(t) >> i) & 1) for i in range(8)]
            for t in texts
        ]
