"""Маршрутизатор запросов между моделями с кэшем и учётом стоимости.

Классифицирует запрос по сложности (simple/medium/complex/reasoning),
маршрутизирует на подходящую модель (Lite / Pro / GigaChat 3.5 Reasoning),
кеширует ответы в Redis с изоляцией по namespace и role, учитывает токены
и оценочную стоимость.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from assistant.domain import Role


class ComplexityLevel(str, Enum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"
    REASONING = "reasoning"


class ModelType(str, Enum):
    LITE = "Lite"                    # локальная/лёгкая модель
    PRO = "Pro"                      # GigaChat-3-Pro
    REASONING = "GigaChat-3.5-Reasoning"  # сложное рассуждение


# Маршрутизация сложности → модель
_COMPLEXITY_TO_MODEL: dict[ComplexityLevel, ModelType] = {
    ComplexityLevel.SIMPLE: ModelType.LITE,
    ComplexityLevel.MEDIUM: ModelType.PRO,
    ComplexityLevel.COMPLEX: ModelType.REASONING,
    ComplexityLevel.REASONING: ModelType.REASONING,
}

# Ориентировочная стоимость 1 000 токенов (рубли, сценарная модель)
_COST_PER_1K_TOKENS: dict[ModelType, float] = {
    ModelType.LITE: 0.50,
    ModelType.PRO: 2.00,
    ModelType.REASONING: 5.00,
}

# Ключевые слова для классификации
_COMPLEXITY_KEYWORDS: dict[ComplexityLevel, list[str]] = {
    ComplexityLevel.REASONING: [
        "расчёт", "анализ", "сравнение", "сравни", "выбери", "реши", "рассчитай",
        "оптимизац", "стратег", "прогноз", "модель", "расшифровка",
        "дебет", "налог", "НДС", "бухгалтер",
    ],
    ComplexityLevel.COMPLEX: [
        "кредит", "ипотека", "вклад", "страхование", "инвестиц",
        "открыть счёт", "ИП", "самозанят", "налоговая",
    ],
    ComplexityLevel.MEDIUM: [
        "перевод", "перевести", "оплата", "баланс", "продукт", "услуга",
    ],
    ComplexityLevel.SIMPLE: [
        "привет", "помощь", "поддержка", "статус", "где",
    ],
}


@dataclass
class TokenUsage:
    """Учёт токенов и стоимости одного запроса."""

    prompt: int = 0
    completion: int = 0
    total: int = 0
    retries: int = 0
    cache_hit: bool = False

    def cost_rub(self, model: ModelType) -> float:
        """Ориентировочная стоимость в рублях (сценарий 2027–2029)."""
        rate = _COST_PER_1K_TOKENS.get(model, 1.0)
        return (self.total / 1000.0) * rate


@dataclass
class CostRecord:
    """Запись о стоимости одного запроса для отчёта."""

    role: str
    model: str
    scenario: str
    tokens: TokenUsage
    cost_rub: float
    timestamp: str


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def classify_complexity(question: str) -> ComplexityLevel:
    """Классифицировать сложность вопроса по ключевым словам и длине.

    Возвращает ComplexityLevel. Детерминированно — без вызова LLM.
    """
    text = question.lower()
    length = len(text)

    # Ключевые слова сравниваются в нижнем регистре
    reasoning_kw = [k.lower() for k in _COMPLEXITY_KEYWORDS[ComplexityLevel.REASONING]]
    complex_kw = [k.lower() for k in _COMPLEXITY_KEYWORDS[ComplexityLevel.COMPLEX]]
    medium_kw = [k.lower() for k in _COMPLEXITY_KEYWORDS[ComplexityLevel.MEDIUM]]

    # Прямые указания на сложное рассуждение
    for kw in reasoning_kw:
        if kw in text:
            return ComplexityLevel.REASONING

    # Сложные бизнес-категории
    for kw in complex_kw:
        if kw in text:
            return ComplexityLevel.COMPLEX

    # Средняя сложность — opérationные вопросы
    for kw in medium_kw:
        if kw in text:
            return ComplexityLevel.MEDIUM

    # Короткие приветствия и справочные — простые
    if length < 60:
        return ComplexityLevel.SIMPLE

    return ComplexityLevel.MEDIUM


def model_for_complexity(level: ComplexityLevel) -> ModelType:
    """Вернуть модель для уровня сложности."""
    return _COMPLEXITY_TO_MODEL[level]


def normalize_query(question: str) -> str:
    """Нормализовать вопрос для ключа кэша (регистр, пробелы, знаки препинания)."""
    return re.sub(r"[^\w\s]", "", question.lower().strip())


def cache_key(namespace: str, role: str, question: str, model_version: str) -> str:
    """Сформировать ключ кэша: namespace + role + normalized_query + model_version.

    Гарантирует изоляцию: разные namespace и role — разные ключи.
    """
    norm = normalize_query(question)
    digest = hashlib.sha256(
        f"{namespace}:{role}:{norm}:{model_version}".encode("utf-8")
    ).hexdigest()
    return f"cache:{namespace}:{role}:{digest[:16]}"


class ModelRouter:
    """Маршрутизатор запросов между моделями.

    Args:
        gigachat: клиент GigaChat (assistant.gigachat.GigaChatClient)
        store: хранилище контекстов (ContextStore) — используется как Redis-кэш
        rcm: менеджер ролевого контекста (для namespace и role)
    """

    def __init__(self, gigachat, store, rcm) -> None:
        self._gigachat = gigachat
        self._store = store
        self._rcm = rcm
        self._cost_records: list[CostRecord] = []

    async def route_and_generate(
        self,
        question: str,
        *,
        role: Role,
        scenario: str = "dialogue",
        model_version: str = "v1",
    ) -> dict[str, Any]:
        """Запросить ответ с маршрутизацией по сложности и кэшированием.

        Возвращает словарь с полями:
            answer, model, complexity, tokens, cost_rub, cache_hit, source
        """
        namespace = self._rcm.unified_namespace
        complexity = classify_complexity(question)
        model = model_for_complexity(complexity)
        key = cache_key(namespace, role.value, question, model_version)

        # Проверка кэша
        cached = await self._store.load(key)
        if cached:
            tokens = TokenUsage(
                prompt=cached.get("prompt", 0),
                completion=cached.get("completion", 0),
                total=cached.get("total", 0),
                cache_hit=True,
            )
            record = CostRecord(
                role=role.value,
                model=model.value,
                scenario=scenario,
                tokens=tokens,
                cost_rub=tokens.cost_rub(model),
                timestamp=_now(),
            )
            self._cost_records.append(record)
            return {
                "answer": cached.get("answer", ""),
                "model": model.value,
                "complexity": complexity.value,
                "tokens": {
                    "prompt": tokens.prompt,
                    "completion": tokens.completion,
                    "total": tokens.total,
                    "retries": tokens.retries,
                    "cache_hit": True,
                },
                "cost_rub": record.cost_rub,
                "source": "cache",
            }

        # Генерация через GigaChat
        response = await self._gigachat.generate(
            [{"role": "user", "content": question}],
            role=role,
            model=model.value,
        )
        answer = response["choices"][0]["message"]["content"]

        # Подсчёт токенов (оценочно: ~4 символа = 1 токен для русского языка)
        prompt_tokens = max(1, len(question) // 4)
        completion_tokens = max(1, len(answer) // 4)
        total_tokens = prompt_tokens + completion_tokens

        tokens = TokenUsage(
            prompt=prompt_tokens,
            completion=completion_tokens,
            total=total_tokens,
            retries=0,
            cache_hit=False,
        )

        record = CostRecord(
            role=role.value,
            model=model.value,
            scenario=scenario,
            tokens=tokens,
            cost_rub=tokens.cost_rub(model),
            timestamp=_now(),
        )
        self._cost_records.append(record)

        # Сохраняем в кэш
        await self._store.save(key, {
            "answer": answer,
            "prompt": prompt_tokens,
            "completion": completion_tokens,
            "total": total_tokens,
        })

        return {
            "answer": answer,
            "model": model.value,
            "complexity": complexity.value,
            "tokens": {
                "prompt": prompt_tokens,
                "completion": completion_tokens,
                "total": total_tokens,
                "retries": 0,
                "cache_hit": False,
            },
            "cost_rub": record.cost_rub,
            "source": "gigachat",
        }

    def cost_report(self) -> dict[str, Any]:
        """Отчёт о стоимости: по ролям, моделям, сценариям, кэш-хитам."""
        total_cost = sum(r.cost_rub for r in self._cost_records)
        total_tokens = sum(r.tokens.total for r in self._cost_records)
        cache_hits = sum(1 for r in self._cost_records if r.tokens.cache_hit)
        total_requests = len(self._cost_records)

        by_role: dict[str, float] = {}
        by_model: dict[str, float] = {}
        by_scenario: dict[str, float] = {}

        for r in self._cost_records:
            by_role[r.role] = by_role.get(r.role, 0.0) + r.cost_rub
            by_model[r.model] = by_model.get(r.model, 0.0) + r.cost_rub
            by_scenario[r.scenario] = by_scenario.get(r.scenario, 0.0) + r.cost_rub

        return {
            "total_cost_rub": round(total_cost, 4),
            "total_tokens": total_tokens,
            "total_requests": total_requests,
            "cache_hits": cache_hits,
            "cache_hit_rate": round(cache_hits / total_requests, 4) if total_requests else 0.0,
            "by_role": {k: round(v, 4) for k, v in by_role.items()},
            "by_model": {k: round(v, 4) for k, v in by_model.items()},
            "by_scenario": {k: round(v, 4) for k, v in by_scenario.items()},
            "currency": "RUB",
            "note": "Сценарная модель тарифов 2027–2029, точные затраты не раскрываются.",
        }

    def records(self) -> list[CostRecord]:
        """Вернуть все записи о стоимости."""
        return list(self._cost_records)
