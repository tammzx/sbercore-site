"""Псевдонимизация пользователя и ролевая namespace-схема изоляции.

Инвариант: любые данные роли доступны только по namespace
``{user_ref}:{role}`` — Redis-ключи, pgvector-namespace, SQL-фильтры.
Сквозной идентификации нет: в контуре ИИ гуляет только псевдоним.
"""
from __future__ import annotations

import hashlib
import hmac
import re

from app.core.roles import Role

# Длина псевдонима (hex) — достаточно для уникальности, необратим без ключа.
_PSEUDONYM_LEN = 32

# Реквизиты для маскирования: счета (20 цифр), карты (16), ИНН (10/12), телефоны (11) и т.п.
_LONG_DIGITS = re.compile(r"\d{10,}")


def pseudonymize(user_id: str, server_key: str) -> str:
    """HMAC-SHA256 от идентификатора пользователя (SberID).

    Сырой идентификатор не покидает auth-модуль: в векторной БД, сессиях,
    аудит-логе и промптах используется только псевдоним.
    """
    return hmac.new(
        server_key.encode("utf-8"),
        user_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:_PSEUDONYM_LEN]


def role_namespace(user_ref: str, role: Role) -> str:
    """Namespace изоляции роли (анклав): база для ключей Redis и pgvector."""
    return f"{user_ref}:{role.value}"


def unified_namespace(user_ref: str) -> str:
    """Единый namespace пользователя — общий (обесличенный) контекст всех ролей."""
    return f"{user_ref}:unified"


def sanitize_for_transfer(text: str) -> str:
    """Маскирование реквизитов (минимизация ПДн — 152-ФЗ ст. 5 ч. 5).

    Применяется: (1) при переносе контекста между ролями,
    (2) перед отправкой текста в LLM. Оставляем первые/последние 4 цифры.
    """
    return _LONG_DIGITS.sub(
        lambda m: f"{m.group(0)[:4]}****{m.group(0)[-4:]}",
        text,
    )


def sanitize_messages(messages: list[dict]) -> list[dict]:
    """Маскирование содержимого списка сообщений (без мутации входных данных)."""
    return [
        {**m, "content": sanitize_for_transfer(m.get("content", ""))}
        for m in messages
    ]
