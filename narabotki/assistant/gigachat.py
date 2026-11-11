"""Клиент GigaChat API — offline-обёртка с ролевыми промптами и PII-маскированием.

В тестовом/песочничном режиме используется детерминированная эмуляция;
в проде — реальный HTTP-клиент к https://gigachat.devices.sberbank.ru/api/v1.
"""
from __future__ import annotations

from typing import Any

from app.ai.gigachat_client import GigaChatClient as _GigaChatClient
from assistant.domain import Role


class GigaChatClient:
    """Обёртка над GigaChat API с ролевыми системными промптами.

    Методы:
        generate(messages, role, model, temperature, max_tokens) -> dict
        get_embeddings(texts) -> list[list[float]]
        mask_pii(text) -> str
    """

    ROLE_SYSTEM_PROMPTS: dict[Role, str] = {
        Role.RETAIL: (
            "Ты — помощник частного клиента СберБанка. Отвечай на русском, "
            "кратко и точно. Никогда не раскрывай данные других клиентов. "
            "Финансовые расчёты выполняет отдельный калькулятор — не считай сам. "
            "Помечай сомнительные утверждения."
        ),
        Role.BUSINESS: (
            "Ты — помощник предпринимателя (СберБизнес). Помогай со счетами, "
            "платежами, отчётностью. Не раскрывай сведения, составляющие "
            "банковскую тайну, в общих примерах. Реквизиты в ответах маскируй."
        ),
        Role.EMPLOYEE: (
            "Ты — помощник сотрудника Сбера (внутренний HR-контур). Используй "
            "только служебные данные, разрешённые ролью. Не размещай персональные "
            "данные коллег во внешних инструментах. Основание обработки — "
            "трудовой договор (ТК РФ ст. 86)."
        ),
    }

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        scope: str = "GIGACHAT_API_PERS",
        *,
        ca_bundle: str | None = None,
        verify_ssl: bool = True,
        offline: bool = False,
    ) -> None:
        self._inner = _GigaChatClient(
            client_id=client_id,
            client_secret=client_secret,
            scope=scope,
            ca_bundle=ca_bundle,
            verify_ssl=verify_ssl,
        )
        self._offline = offline

    async def generate(
        self,
        messages: list[dict[str, str]],
        *,
        role: Role = Role.RETAIL,
        model: str = "GigaChat-3-Pro",
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> dict:
        """Сгенерировать ответ с ролевым системным промптом и маскированием PII."""
        system = self.ROLE_SYSTEM_PROMPTS.get(role, self.ROLE_SYSTEM_PROMPTS[Role.RETAIL])
        full_messages = [
            {"role": "system", "content": system},
            *messages,
        ]
        if self._offline:
            # Детерминированная эмуляция для тестов без сети
            last_user = messages[-1]["content"] if messages else ""
            return {"choices": [{"message": {"content": f"[GigaChat офлайн:{role.value}] {last_user}"}}]}
        return await self._inner.generate(
            full_messages, model=model, temperature=temperature, max_tokens=max_tokens
        )

    async def get_embeddings(self, texts: list[str], *, model: str = "EmbeddingsGigaR") -> list[list[float]]:
        """Получить эмбеддинги текстов."""
        if self._offline:
            return [
                [float((hash(t) >> i) & 1) for i in range(8)]
                for t in texts
            ]
        return await self._inner.get_embeddings(texts, model=model)

    @staticmethod
    def mask_pii(text: str) -> str:
        """Замаскировать реквизиты (счета, карты, телефоны, ИНН) в тексте."""
        from app.core.isolation import sanitize_for_transfer

        return sanitize_for_transfer(text)

    async def aclose(self) -> None:
        await self._inner.aclose()
