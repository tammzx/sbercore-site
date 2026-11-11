"""Тесты GigaChat-клиента на httpx.MockTransport (сеть не используется)."""
from __future__ import annotations

import json

import httpx
import pytest

from app.ai.gigachat_client import GigaChatClient
from app.core.exceptions import GigaChatError


def mock_transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def make_client(handler) -> GigaChatClient:
    return GigaChatClient(
        "test_id",
        "test_secret",
        scope="GIGACHAT_API_PERS",
        transport=mock_transport(handler),
    )


async def test_generate_with_token_refresh():
    """OAuth-токен получается автоматически и используется в генерации."""

    seen_auth_headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "ngw.devices.sberbank.ru":
            assert "Basic" in request.headers["Authorization"]
            assert "RqUID" in request.headers
            body = request.content.decode()
            assert "scope=GIGACHAT_API_PERS" in body
            return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 1800})
        if request.url.path.endswith("/chat/completions"):
            seen_auth_headers.append(request.headers["Authorization"])
            payload = json.loads(request.content)
            assert payload["messages"][-1]["content"] == "привет"
            return httpx.Response(200, json={"choices": [{"message": {"content": "ответ"}}]})
        return httpx.Response(404, text=request.url.path)

    client = make_client(handler)
    result = await client.generate([{"role": "user", "content": "привет"}])
    assert result["choices"][0]["message"]["content"] == "ответ"
    assert seen_auth_headers == ["Bearer tok-1"]
    await client.aclose()


async def test_token_is_cached():
    """Токен кэшируется: повторный вызов не запрашивает OAuth второй раз."""
    oauth_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal oauth_calls
        if request.url.host == "ngw.devices.sberbank.ru":
            oauth_calls += 1
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 1800})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = make_client(handler)
    await client.generate([{"role": "user", "content": "1"}])
    await client.generate([{"role": "user", "content": "2"}])
    assert oauth_calls == 1
    await client.aclose()


async def test_embeddings_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/embeddings"):
            payload = json.loads(request.content)
            assert payload["model"] == "EmbeddingsGigaR"
            return httpx.Response(
                200,
                json={"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]},
            )
        return httpx.Response(200, json={"access_token": "tok", "expires_in": 1800})

    client = make_client(handler)
    vectors = await client.get_embeddings(["текст 1", "текст 2"])
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    await client.aclose()


async def test_generate_error_wrapped():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "ngw.devices.sberbank.ru":
            return httpx.Response(401, json={"error": "bad credentials"})
        return httpx.Response(500)

    client = make_client(handler)
    with pytest.raises(GigaChatError):
        await client.generate([{"role": "user", "content": "x"}])
    await client.aclose()
