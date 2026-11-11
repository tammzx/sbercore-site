"""Защита от prompt injection в LangGraph-графе.

Детектор инъекций:
- Регулярные паттерны («игнорируй предыдущие инструкции», «раскрой системный промпт»,
  DAN-подобные, «ты теперь другой ассистент» и т.п.).
- Эвристики (слишком много спецсимволов, инструкции на английском в русском диалоге,
  попытки переопределить роль).

При срабатывании: ответ «Я не могу выполнить эту просьбу», лог в audit,
инкремент счётчика подозрительной активности для пользователя.

API: GET /v1/guardrails/stats — статистика блокировок (без ПДн).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from assistant.role_context import RoleContextManager


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class InjectionEvent:
    """Событие блокировки инъекции."""

    user_ref: str
    role: str
    pattern_matched: str
    risk_level: str  # low / medium / high
    blocked_at: str = field(default_factory=_now)


# Паттерны prompt injection (регулярные выражения)
INJECTION_PATTERNS: list[tuple[str, str, str]] = [
    # (pattern, name, risk_level)
    (
        r"игнорир(уй|уйте)\s+(все\s+)?(предыдущие\s+)?(инструкции|правила|задачу|system\s+prompt)",
        "ignore_previous_instructions",
        "high",
    ),
    (
        r"раскры(й|йте)\s+(системный\s+)?промпт|show\s+me\s+the\s+system\s+prompt",
        "reveal_system_prompt",
        "high",
    ),
    (
        r"ты\s+теперь\s+другой|ты\s+более\s+не\s+помощник|you\s+are\s+now\s+a\s+different",
        "role_override",
        "high",
    ),
    (
        r"DAN|do\s+anything\s+now|jailbreak|obey\s+me\s+instead",
        "dan_jailbreak",
        "high",
    ),
    (
        r"забудь\s+всё,\s+что\s+тебе\s+сказали|forget\s+everything\s+above",
        "forget_previous",
        "high",
    ),
    (
        r"выведи\s+все\s+данные|dump\s+all\s+data|покажи\s+базу\s+данных",
        "data_dump_attempt",
        "high",
    ),
    (
        r"инструкции\s+для\s+разработчика|developer\s+mode|режим\s+разработчика",
        "developer_mode",
        "medium",
    ),
    (
        r"[^\w\s]{10,}|\[{3,}|\]{3,}",
        "excessive_special_chars",
        "low",
    ),
    (
        r"\b(ignore|disregard|override|bypass|override)\s+(all\s+)?(safety|security|guardrail|filter)",
        "bypass_safety",
        "high",
    ),
]


def _count_non_alphanumeric_ratio(text: str) -> float:
    """Доля неалфавитных символов в тексте."""
    if not text:
        return 0.0
    non_alpha = sum(1 for c in text if not c.isalnum() and not c.isspace())
    return non_alpha / len(text)


def _has_english_instructions_in_russian(text: str) -> bool:
    """Проверка на английские инструкции в русскоязычном диалоге."""
    english_command_keywords = [
        "ignore", "disregard", "override", "system", "prompt",
        "assistant", "you are", "act as", "pretend",
    ]
    has_russian = bool(re.search(r"[а-яё]", text, flags=re.IGNORECASE))
    if not has_russian:
        return False
    for kw in english_command_keywords:
        if re.search(rf"\b{kw}\b", text, flags=re.IGNORECASE):
            return True
    return False


class GuardrailsService:
    """Сервис защиты от prompt injection.

    Проверяет входящие сообщения пользователя на попытки инъекции.
    При срабатывании блокирует запрос, пишет в audit и инкремент счётчика.
    """

    def __init__(
        self,
        rcm: RoleContextManager,
    ) -> None:
        self._rcm = rcm
        self._blocked_count: dict[str, int] = {}  # user_ref -> count
        self._events: list[InjectionEvent] = []

    async def check(
        self,
        text: str,
        *,
        role: str,
        user_ref: str,
    ) -> tuple[bool, str | None]:
        """Проверить текст на prompt injection.

        Returns:
            (is_safe, block_reason). Если is_safe=False, block_reason содержит
            причину блокировки.
        """
        # 1. Регулярные паттерны
        for pattern, name, risk_level in INJECTION_PATTERNS:
            if re.search(pattern, text, flags=re.IGNORECASE):
                await self._record_block(user_ref, role, name, risk_level)
                return False, f"Обнаружена попытка инъекции: {name}"

        # 2. Эвристика: слишком много спецсимволов
        if _count_non_alphanumeric_ratio(text) > 0.5:
            await self._record_block(user_ref, role, "excessive_special_chars", "low")
            return False, "Сообщение содержит слишком много спецсимволов"

        # 3. Эвристика: английские инструкции в русском диалоге
        if _has_english_instructions_in_russian(text):
            await self._record_block(user_ref, role, "english_instructions", "medium")
            return False, "Обнаружены посторонние инструкции на английском"

        return True, None

    async def _record_block(
        self,
        user_ref: str,
        role: str,
        pattern_name: str,
        risk_level: str,
    ) -> None:
        """Записать событие блокировки в audit и счётчик."""
        self._blocked_count[user_ref] = self._blocked_count.get(user_ref, 0) + 1
        event = InjectionEvent(
            user_ref=user_ref,
            role=role,
            pattern_matched=pattern_name,
            risk_level=risk_level,
        )
        self._events.append(event)

        # Пишем в audit через RoleContextManager
        try:
            await self._rcm._audit_log.append(  # type: ignore[attr-defined]
                "guardrail.blocked",
                user_ref,
                {
                    "role": role,
                    "pattern": pattern_name,
                    "risk_level": risk_level,
                    "blocked_count": self._blocked_count[user_ref],
                },
            )
        except Exception:
            # В демо не прерываем работу из-за ошибки аудита
            pass

    def get_stats(self) -> dict[str, Any]:
        """Статистика блокировок (без ПДн)."""
        total = len(self._events)
        by_risk: dict[str, int] = {}
        by_pattern: dict[str, int] = {}
        for event in self._events:
            by_risk[event.risk_level] = by_risk.get(event.risk_level, 0) + 1
            by_pattern[event.pattern_matched] = by_pattern.get(event.pattern_matched, 0) + 1

        return {
            "total_blocks": total,
            "blocked_users": len(self._blocked_count),
            "by_risk_level": by_risk,
            "by_pattern": by_pattern,
            "top_blocked_users": sorted(
                self._blocked_count.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:10],
        }

    def is_suspicious_user(self, user_ref: str, threshold: int = 5) -> bool:
        """Пользователь считается подозрительным, если блокировок >= threshold."""
        return self._blocked_count.get(user_ref, 0) >= threshold


# Экземпляр для использования в API (создаётся в main lifespan)
guardrails_service: GuardrailsService | None = None


def get_guardrails_service() -> GuardrailsService | None:
    """Получить экземпляр сервиса guardrails."""
    return guardrails_service
