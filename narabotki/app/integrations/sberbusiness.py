"""Клиент SberBusiness API (счета, платежи) — роль business.

⚠️ Доступ требует договора со Сбером. Данные счетов — под режимом
банковской тайны (395-1 ст. 26): используются только в business-контуре
и только при согласии banking_data_analysis (проверяет вызывающий код).
"""
from __future__ import annotations

import time
from typing import Any

import httpx

BASE_URL = "https://api.sberbank.ru"  # прод; для тестов — эмулятор Сбера


class SberBusinessClient:
    """Клиент SberBusiness API с кэшированием токена (client_credentials)."""

    def __init__(self, client_id: str, client_secret: str, *, verify_ssl: bool = True) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._verify = verify_ssl
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    async def _get_token(self) -> str:
        """Токен по client_credentials (обновление за 60 с до истечения)."""
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        async with httpx.AsyncClient(verify=self._verify, timeout=15.0) as http:
            response = await http.post(
                f"{BASE_URL}/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": "accounts payments",
                },
            )
            response.raise_for_status()
            data = response.json()
        self._token = data["access_token"]
        self._token_expires_at = time.monotonic() + max(int(data.get("expires_in", 300)) - 60, 30)
        return self._token

    async def get_accounts(self, user_id: str) -> list[dict[str, Any]]:
        """Счета предпринимателя. Требуется согласие banking_data_analysis (152-ФЗ)."""
        token = await self._get_token()
        async with httpx.AsyncClient(verify=self._verify, timeout=15.0) as http:
            response = await http.get(
                f"{BASE_URL}/accounts",
                headers={"Authorization": f"Bearer {token}"},
                params={"user_id": user_id},
            )
            response.raise_for_status()
        return response.json().get("accounts", [])

    async def create_payment_draft(self, user_id: str, payment_data: dict[str, Any]) -> dict[str, Any]:
        """Создать черновик платёжного поручения.

        Подпись/подтверждение — только пользователем в СберБизнес
        (ассистент платежи не подписывает).
        """
        token = await self._get_token()
        async with httpx.AsyncClient(verify=self._verify, timeout=15.0) as http:
            response = await http.post(
                f"{BASE_URL}/payments/draft",
                headers={"Authorization": f"Bearer {token}"},
                json={"user_id": user_id, **payment_data},
            )
            response.raise_for_status()
            draft = response.json()
        return {
            "draft_id": draft["id"],
            "status": "pending_confirmation",
            "confirmation_url": f"/confirm/payment/{draft['id']}",
        }
