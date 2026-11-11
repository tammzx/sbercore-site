"""Клиент GigaChat API — https://developers.sber.ru/docs/ru/gigachat/api-reference.

Особенности:
- OAuth-токен кэшируется и обновляется заранее (skew 60 с), под asyncio.Lock;
- TLS: в проде — сертификаты НУЦ Минцифры (GIGACHAT_CA_BUNDLE), verify=False запрещён;
- transport подменяем в тестах (httpx.MockTransport) — сеть не нужна;
- RqUID уникален на каждый запрос OAuth (требование API).
"""
from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator
from uuid import uuid4

import httpx

from app.core.exceptions import GigaChatError

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
BASE_URL = "https://gigachat.devices.sberbank.ru/api/v1"

TOKEN_SKEW_SECONDS = 60


class GigaChatClient:
    """Клиент генерации, стриминга и эмбеддингов GigaChat."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        scope: str = "GIGACHAT_API_PERS",
        *,
        ca_bundle: str | None = None,
        verify_ssl: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._verify: bool | str = ca_bundle or verify_ssl
        self._transport = transport
        self._timeout = timeout

        self._http: httpx.AsyncClient | None = None
        self._token: str | None = None
        self._token_expires: datetime | None = None
        self._token_lock = asyncio.Lock()

    # -- жизненный цикл ------------------------------------------------------

    async def _http_client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                timeout=self._timeout,
                verify=self._verify,
                transport=self._transport,
            )
        return self._http

    async def aclose(self) -> None:
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()

    # -- авторизация ----------------------------------------------------------

    def _basic_auth(self) -> str:
        raw = f"{self._client_id}:{self._client_secret}".encode("utf-8")
        return base64.b64encode(raw).decode("ascii")

    async def _ensure_token(self) -> str:
        if self._token and self._token_expires and self._token_expires > datetime.now(timezone.utc):
            return self._token

        async with self._token_lock:
            if self._token and self._token_expires and self._token_expires > datetime.now(timezone.utc):
                return self._token

            http = await self._http_client()
            try:
                response = await http.post(
                    OAUTH_URL,
                    headers={
                        "Authorization": f"Basic {self._basic_auth()}",
                        "RqUID": str(uuid4()),
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "application/json",
                    },
                    data={"scope": self._scope},
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise GigaChatError(f"Не удалось получить токен GigaChat: {exc}") from exc

            data = response.json()
            self._token = data["access_token"]
            # API возвращает expires_at (unix ts) — поддержим оба варианта на всякий случай
            expires_in = data.get("expires_in")
            if expires_in is None and "expires_at" in data:
                expires_in = int(data["expires_at"]) - int(datetime.now(timezone.utc).timestamp())
            self._token_expires = datetime.now(timezone.utc) + timedelta(
                seconds=max(int(expires_in or 1800) - TOKEN_SKEW_SECONDS, 30)
            )
            return self._token

    # -- API -------------------------------------------------------------------

    async def generate(
        self,
        messages: list[dict[str, str]],
        *,
        model: str = "GigaChat-3-Pro",
        temperature: float = 0.7,
        max_tokens: int = 2000,
        functions: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Синхронная генерация (chat/completions)."""
        token = await self._ensure_token()
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if functions:
            payload["functions"] = functions

        http = await self._http_client()
        try:
            response = await http.post(
                f"{BASE_URL}/chat/completions",
                headers=self._auth_headers(token),
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise GigaChatError(f"Ошибка генерации GigaChat: {exc}") from exc
        return response.json()

    async def generate_stream(
        self,
        messages: list[dict[str, str]],
        *,
        model: str = "GigaChat-3-Pro",
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> AsyncGenerator[str, None]:
        """Стриминг генерации (SSE) для real-time интерфейса."""
        token = await self._ensure_token()
        http = await self._http_client()
        async with http.stream(
            "POST",
            f"{BASE_URL}/chat/completions",
            headers=self._auth_headers(token),
            json={
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True,
            },
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                data = json.loads(line[6:])
                content = (
                    data.get("choices", [{}])[0].get("delta", {}).get("content")
                )
                if content:
                    yield content

    async def get_embeddings(self, texts: list[str], *, model: str = "EmbeddingsGigaR") -> list[list[float]]:
        """Эмбеддинги для RAG (вектор 1024)."""
        token = await self._ensure_token()
        http = await self._http_client()
        try:
            response = await http.post(
                f"{BASE_URL}/embeddings",
                headers=self._auth_headers(token),
                json={"model": model, "input": texts},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise GigaChatError(f"Ошибка эмбеддингов GigaChat: {exc}") from exc
        data = response.json()
        return [item["embedding"] for item in data["data"]]

    @staticmethod
    def _auth_headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
