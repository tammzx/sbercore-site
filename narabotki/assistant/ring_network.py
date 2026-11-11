"""Кольцевая нейросеть (RingNetwork) — фоновый сервис извлечения фактов.

Сканирует диалоги пользователя, извлекает сущности (суммы, даты, намерения,
проблемы) и записывает их в цифровую папку (assistant/digital_folder.py).
При превышении порога количества фактов генерирует компактную сводку
для экономии контекста LLM.

Запуск: периодически (каждые N сообщений или по расписанию — в демо через
asyncio.sleep, без новых зависимостей).

Конфигурация:
    RING_ENABLED — включить/выключить кольцевую сеть
    RING_BATCH_SIZE — сколько сообщений сканировать за один проход
    RING_SUMMARY_THRESHOLD — порог количества фактов для суммаризации
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from assistant.digital_folder import (
    DigitalFolderService,
    Fact,
    FactCategory,
    FactSource,
)
from assistant.domain import Role
from assistant.role_context import RoleContextManager


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RingConfig:
    """Конфигурация кольцевой сети."""

    enabled: bool = True
    batch_size: int = 5      # сканировать последние N сообщений за проход
    summary_threshold: int = 10  # если фактов больше — генерировать сводку
    scan_interval_seconds: int = 30  # интервал между сканированиями (демо)


@dataclass
class ExtractedFact:
    """Извлечённый из диалога факт перед записью в папку."""

    category: FactCategory
    key: str
    value: Any
    source: FactSource = FactSource.RING
    confidence: float = 0.7


class RingNetwork:
    """Фоновая кольцевая нейросеть (лёгкая модель Lite).

    Сканирует последние сообщения из единого контекста пользователя,
    извлекает факты и записывает их в цифровую папку роли.
    """

    def __init__(
        self,
        rcm: RoleContextManager,
        folder_service: DigitalFolderService,
        config: RingConfig | None = None,
    ) -> None:
        self._rcm = rcm
        self._folder = folder_service
        self._config = config or RingConfig()
        self._running = False
        self._task: asyncio.Task | None = None

    # -- жизненный цикл --------------------------------------------------------

    async def start(self) -> None:
        """Запустить фоновое сканирование (демо: цикл с asyncio.sleep)."""
        if not self._config.enabled:
            return
        self._running = True
        self._task = asyncio.create_task(self._scan_loop())

    async def stop(self) -> None:
        """Остановить фоновое сканирование."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _scan_loop(self) -> None:
        """Основной цикл сканирования диалогов."""
        while self._running:
            try:
                await self.scan_and_extract()
            except Exception:
                # В демо не прерываем цикл из-за одной ошибки
                pass
            await asyncio.sleep(self._config.scan_interval_seconds)

    # -- извлечение фактов -----------------------------------------------------

    async def scan_and_extract(
        self,
        *,
        role: Role | None = None,
        max_messages: int | None = None,
    ) -> list[Fact]:
        """Просканировать сообщения и извлечь факты в цифровую папку.

        Возвращает список созданных/обновлённых фактов.
        """
        role = role or self._rcm.active_role
        if role is None:
            return []

        batch_size = max_messages or self._config.batch_size

        # Получаем контекст роли
        view = await self._rcm.get_context(role)
        messages = view["messages"][-batch_size:]

        extracted: list[ExtractedFact] = self._extract_from_messages(
            messages, role
        )

        created: list[Fact] = []
        for ef in extracted:
            fact = await self._folder.add_fact(
                category=ef.category,
                key=ef.key,
                value=ef.value,
                source=ef.source,
                confidence=ef.confidence,
                role=role,
            )
            created.append(fact)

        # Проверяем порог суммаризации
        all_facts = await self._folder.get_all_facts(role=role)
        if len(all_facts) >= self._config.summary_threshold:
            await self._summarize(role)

        return created

    def _extract_from_messages(
        self,
        messages: list[dict[str, Any]],
        role: Role,
    ) -> list[ExtractedFact]:
        """Извлечь сущности из списка сообщений (эвристика, без вызова LLM)."""
        facts: list[ExtractedFact] = []

        for msg in messages:
            content = msg.get("content", "")
            author = msg.get("author", "")

            # Пропускаем сообщения помощника — извлекаем только из пользовательских
            if author != "user":
                continue

            # Суммы (деньги)
            money_matches = re.findall(
                r"(\d[\d\s]*\d)\s*(?:руб|₽|rubles?)",
                content,
                flags=re.IGNORECASE,
            )
            for match in money_matches:
                clean = match.replace(" ", "").replace("\u00a0", "")
                try:
                    amount = float(clean)
                except ValueError:
                    continue
                facts.append(ExtractedFact(
                    category=FactCategory.FINANCE,
                    key=f"amount_{abs(hash(content + match)) % 100000}",
                    value=amount,
                    confidence=0.8,
                ))

            # Даты
            date_matches = re.findall(
                r"\d{1,2}\.\d{1,2}\.\d{4}",
                content,
            )
            for match in date_matches:
                facts.append(ExtractedFact(
                    category=FactCategory.PREFERENCES,
                    key=f"date_{match}",
                    value=match,
                    confidence=0.9,
                ))

            # Намерения (ключевые слова)
            intent_keywords = {
                "открыть вклад": (FactCategory.FINANCE, "intent_open_deposit", 0.85),
                "кредит": (FactCategory.FINANCE, "intent_credit", 0.8),
                "ипотека": (FactCategory.FINANCE, "intent_mortgage", 0.85),
                "страховка": (FactCategory.RISKS, "intent_insurance", 0.8),
                "ИП": (FactCategory.BUSINESS, "intent_ip", 0.9),
                "самозанятый": (FactCategory.BUSINESS, "intent_self_employed", 0.9),
                "налог": (FactCategory.BUSINESS, "intent_tax", 0.75),
                "просрочка": (FactCategory.RISKS, "risk_overdue", 0.9),
                "большая трата": (FactCategory.RISKS, "risk_large_spending", 0.7),
            }
            lower = content.lower()
            for phrase, (cat, key, conf) in intent_keywords.items():
                if phrase in lower:
                    facts.append(ExtractedFact(
                        category=cat,
                        key=key,
                        value=True,
                        confidence=conf,
                    ))

            # Семейные обстоятельства
            family_keywords = {
                "дети": "has_children",
                "ребёнок": "has_children",
                "пенсионер": "is_pensioner",
                "инвалид": "has_disability",
            }
            for phrase, key in family_keywords.items():
                if phrase in lower:
                    facts.append(ExtractedFact(
                        category=FactCategory.FAMILY,
                        key=key,
                        value=True,
                        confidence=0.85,
                    ))

        return facts

    # -- суммаризация ----------------------------------------------------------

    async def _summarize(self, role: Role) -> None:
        """Генерировать компактную сводку фактов и архивировать детали.

        Если фактов больше порога, создаём один обобщающий факт
        и архивируем старые детальные факты — экономия контекста для LLM.
        """
        all_facts = await self._folder.get_all_facts(role=role)
        if len(all_facts) < self._config.summary_threshold:
            return

        # Группируем по категориям
        by_category: dict[FactCategory, list[Fact]] = {}
        for fact in all_facts:
            by_category.setdefault(fact.category, []).append(fact)

        summary_parts: list[str] = []
        for cat, cat_facts in by_category.items():
            keys = [f.key for f in cat_facts[:5]]
            summary_parts.append(f"{cat.value}: {', '.join(keys)}")

        summary_text = "; ".join(summary_parts)

        # Архивируем старые детальные факты
        for fact in all_facts:
            if not fact.archived:
                fact.archived = True
                fact.version += 1

        # Сохраняем архивные факты ДО добавления сводки, иначе сводка потеряется
        namespace = self._folder._facts_namespace(role)  # type: ignore[attr-defined]
        facts_dict = {f.id: f for f in all_facts}
        await self._folder._save_facts(namespace, facts_dict)  # type: ignore[attr-defined]

        # Записываем сводку как новый факт
        await self._folder.add_fact(
            category=FactCategory.PREFERENCES,
            key=f"summary_{role.value}_{_now()[:10]}",
            value={"summary": summary_text, "fact_count": len(all_facts)},
            source=FactSource.RING,
            confidence=0.7,
            role=role,
            ttl_seconds=30 * 24 * 60 * 60,  # сводка живет 30 дней
        )
