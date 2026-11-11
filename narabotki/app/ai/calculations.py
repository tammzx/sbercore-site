"""Финансовые расчёты вне LLM («code gate»).

LLM не выполняет арифметику: если ответ требует вычислений, граф вызывает
эти функции — строго Decimal, без float (деньги).
"""
from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP

_MONEY_RE = re.compile(r"\b\d+(?:[.,]\d+)?\b")


def to_decimal(value: str | int | float) -> Decimal:
    """Безопасное приведение к Decimal (запятая → точка)."""
    return Decimal(str(value).replace(",", ".").replace(" ", ""))


def annuity_payment(amount: str | Decimal, annual_rate_percent: str | Decimal, months: int) -> Decimal:
    """Аннуитетный платёж по кредиту.

    i — месячная ставка; при нулевой ставке — равные доли.
    """
    amount_d = Decimal(amount)
    months_int = int(months)
    if amount_d <= 0 or months_int <= 0:
        raise ValueError("Сумма и срок должны быть положительными")
    monthly_rate = to_decimal(annual_rate_percent) / Decimal(100) / Decimal(12)

    if monthly_rate == 0:
        payment = amount_d / Decimal(months_int)
    else:
        factor = (Decimal(1) + monthly_rate) ** months_int
        payment = amount_d * monthly_rate * factor / (factor - Decimal(1))

    return payment.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def percent_of(value: str | Decimal, percent: str | Decimal) -> Decimal:
    """Процент от суммы (например, комиссия)."""
    result = to_decimal(value) * to_decimal(percent) / Decimal(100)
    return result.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def extract_numbers(text: str) -> list[Decimal]:
    """Извлечь числа из вопроса пользователя для передачи в code gate."""
    return [to_decimal(m.group(0)) for m in _MONEY_RE.finditer(text)]


def needs_calculation(text: str) -> bool:
    """Эвристика: требует ли вопрос вычислений (ставка, платёж, процент)."""
    triggers = ("рассчита", "платёж", "платеж", "процент", "ставк", "переплат", "аннуитет")
    return any(t in text.lower() for t in triggers)
