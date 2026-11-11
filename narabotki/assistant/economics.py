"""ROI-калькулятор — unit-экономика эффекта от ЯдроСбера.

Модель затрат: стоимость токенов (по модели из model_router), инфраструктура
(Redis/Postgres/CPU), поддержка (человеко-часы на обращение).

Модель выгоды: снижение churn → LTV, сокращение повторных обращений → экономия
на поддержке, cross-sell uplift.

Сценарии: sensitivity-анализ ±20% на ключевые метрики.

Все денежные расчёты — Decimal (без float).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rub(value: float | int | str | Decimal) -> Decimal:
    """Привести к Decimal с 2 знаками (рубли, копейки)."""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class CostModel:
    """Затраты на инфраструктуру и поддержку."""

    # Стоимость токенов (рублей за 1000 токенов, по модели)
    cost_per_1k_tokens_lite: Decimal = Decimal("0.50")
    cost_per_1k_tokens_pro: Decimal = Decimal("2.00")
    cost_per_1k_tokens_reasoning: Decimal = Decimal("5.00")

    # Инфраструктура (рублей в месяц)
    infra_redis_monthly: Decimal = Decimal("15000.00")
    infra_postgres_monthly: Decimal = Decimal("30000.00")
    infra_cpu_monthly: Decimal = Decimal("50000.00")

    # Поддержка (человеко-часы на обращение, рублей в час)
    support_hours_per_contact: Decimal = Decimal("0.25")
    support_hourly_rate: Decimal = Decimal("2000.00")

    # Количество обращений в месяц
    monthly_contacts: int = 100_000

    # Распределение запросов по моделям (доля)
    share_lite: Decimal = Decimal("0.60")
    share_pro: Decimal = Decimal("0.30")
    share_reasoning: Decimal = Decimal("0.10")

    # Среднее количество токенов на запрос
    avg_tokens_per_request: int = 500


@dataclass
class BenefitModel:
    """Модель выгоды от внедрения."""

    # Снижение churn (%)
    churn_reduction_percent: Decimal = Decimal("5.0")

    # LTV одного клиента (рублей)
    ltv_per_client: Decimal = Decimal("50_000.00")

    # Количество клиентов
    total_clients: int = 1_000_000

    # Сокращение повторных обращений (%)
    repeat_contact_reduction_percent: Decimal = Decimal("30.0")

    # Базовое количество повторных обращений в месяц
    monthly_repeat_contacts: int = 20_000

    # Cross-sell uplift (%)
    cross_sell_uplift_percent: Decimal = Decimal("3.0")

    # Средняя маржа с кросс-продажи (рублей)
    cross_sell_margin_per_client: Decimal = Decimal("500.00")


@dataclass
class Scenario:
    """Параметры сценария для sensitivity-анализа."""

    name: str
    cost_multiplier: Decimal = Decimal("1.0")
    benefit_multiplier: Decimal = Decimal("1.0")
    description: str = ""


@dataclass
class ROICalculation:
    """Результат расчёта ROI."""

    scenario_name: str
    monthly_costs_rub: Decimal
    monthly_benefits_rub: Decimal
    monthly_net_rub: Decimal
    annual_net_rub: Decimal
    npv_rub: Decimal
    payback_period_months: float
    roi_percent: Decimal
    sensitivity: dict[str, "ROICalculation"] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_name": self.scenario_name,
            "monthly_costs_rub": float(self.monthly_costs_rub),
            "monthly_benefits_rub": float(self.monthly_benefits_rub),
            "monthly_net_rub": float(self.monthly_net_rub),
            "annual_net_rub": float(self.annual_net_rub),
            "npv_rub": float(self.npv_rub),
            "payback_period_months": self.payback_period_months,
            "roi_percent": float(self.roi_percent),
            "sensitivity": {
                k: {
                    "monthly_net_rub": float(v.monthly_net_rub),
                    "npv_rub": float(v.npv_rub),
                    "roi_percent": float(v.roi_percent),
                }
                for k, v in self.sensitivity.items()
            },
        }


class EconomicsService:
    """Сервис расчёта unit-экономики ЯдроСбера."""

    def __init__(
        self,
        cost_model: CostModel | None = None,
        benefit_model: BenefitModel | None = None,
    ) -> None:
        self.cost_model = cost_model or CostModel()
        self.benefit_model = benefit_model or BenefitModel()

    def calculate_monthly_costs(self) -> dict[str, Decimal]:
        """Расчёт ежемесячных затрат."""
        cm = self.cost_model

        # Затраты на токены
        total_tokens = cm.monthly_contacts * cm.avg_tokens_per_request
        token_cost_lite = (
            Decimal(total_tokens) * cm.share_lite / Decimal(1000)
        ) * cm.cost_per_1k_tokens_lite
        token_cost_pro = (
            Decimal(total_tokens) * cm.share_pro / Decimal(1000)
        ) * cm.cost_per_1k_tokens_pro
        token_cost_reasoning = (
            Decimal(total_tokens) * cm.share_reasoning / Decimal(1000)
        ) * cm.cost_per_1k_tokens_reasoning
        total_token_cost = token_cost_lite + token_cost_pro + token_cost_reasoning

        # Инфраструктура
        infra_cost = (
            cm.infra_redis_monthly
            + cm.infra_postgres_monthly
            + cm.infra_cpu_monthly
        )

        # Поддержка
        support_cost = (
            Decimal(cm.monthly_contacts)
            * cm.support_hours_per_contact
            * cm.support_hourly_rate
        )

        return {
            "tokens_lite_rub": token_cost_lite,
            "tokens_pro_rub": token_cost_pro,
            "tokens_reasoning_rub": token_cost_reasoning,
            "tokens_total_rub": total_token_cost,
            "infrastructure_rub": infra_cost,
            "support_rub": support_cost,
            "total_rub": total_token_cost + infra_cost + support_cost,
        }

    def calculate_monthly_benefits(self) -> dict[str, Decimal]:
        """Расчёт ежемесячной выгоды."""
        bm = self.benefit_model

        # Выгода от снижения churn → сохранение LTV
        churn_benefit = (
            Decimal(bm.total_clients)
            * bm.churn_reduction_percent
            / Decimal(100)
            / Decimal(12)
        ) * bm.ltv_per_client

        # Выгода от сокращения повторных обращений
        repeat_saved = (
            Decimal(bm.monthly_repeat_contacts)
            * bm.repeat_contact_reduction_percent
            / Decimal(100)
        )
        support_saved = repeat_saved * (
            self.cost_model.support_hours_per_contact
            * self.cost_model.support_hourly_rate
        )

        # Выгода от cross-sell uplift
        cross_sell_benefit = (
            Decimal(bm.total_clients)
            * bm.cross_sell_uplift_percent
            / Decimal(100)
            / Decimal(12)
        ) * bm.cross_sell_margin_per_client

        return {
            "churn_retention_rub": churn_benefit,
            "support_saved_rub": support_saved,
            "cross_sell_rub": cross_sell_benefit,
            "total_rub": churn_benefit + support_saved + cross_sell_benefit,
        }

    def calculate_roi(
        self,
        *,
        scenario: Scenario | None = None,
    ) -> ROICalculation:
        """Расчёт ROI для заданного сценария."""
        cm = self.cost_model
        bm = self.benefit_model

        cost_mult = scenario.cost_multiplier if scenario else Decimal("1.0")
        benefit_mult = scenario.benefit_multiplier if scenario else Decimal("1.0")

        costs = self.calculate_monthly_costs()
        benefits = self.calculate_monthly_benefits()

        monthly_costs = costs["total_rub"] * cost_mult
        monthly_benefits = benefits["total_rub"] * benefit_mult
        monthly_net = monthly_benefits - monthly_costs
        annual_net = monthly_net * 12

        # NPV (простая модель, дисконтная ставка 12% годовых = 1% в месяц)
        monthly_discount = Decimal("0.01")
        npv = Decimal("0")
        for month in range(1, 13):
            npv += monthly_net / ((Decimal("1") + monthly_discount) ** month)

        # Период окупаемости (месяцы)
        if monthly_net > 0:
            initial_cost = monthly_costs * 3  # предположение: 3 месяца разработки
            payback_months = float(initial_cost / monthly_net)
        else:
            payback_months = float("inf")

        # ROI (%)
        if monthly_costs > 0:
            roi_percent = (annual_net / (monthly_costs * 12)) * Decimal(100)
        else:
            roi_percent = Decimal("0")

        return ROICalculation(
            scenario_name=scenario.name if scenario else "базовый",
            monthly_costs_rub=monthly_costs,
            monthly_benefits_rub=monthly_benefits,
            monthly_net_rub=monthly_net,
            annual_net_rub=annual_net,
            npv_rub=npv,
            payback_period_months=round(payback_months, 1),
            roi_percent=roi_percent,
        )

    def calculate_sensitivity(self) -> dict[str, ROICalculation]:
        """Sensitivity-анализ ±20% на ключевые метрики."""
        scenarios = [
            Scenario(
                name="базовый",
                cost_multiplier=Decimal("1.0"),
                benefit_multiplier=Decimal("1.0"),
                description="Базовый сценарий",
            ),
            Scenario(
                name="затраты_+20%",
                cost_multiplier=Decimal("1.2"),
                benefit_multiplier=Decimal("1.0"),
                description="Затраты на 20% выше",
            ),
            Scenario(
                name="затраты_-20%",
                cost_multiplier=Decimal("0.8"),
                benefit_multiplier=Decimal("1.0"),
                description="Затраты на 20% ниже",
            ),
            Scenario(
                name="выгода_+20%",
                cost_multiplier=Decimal("1.0"),
                benefit_multiplier=Decimal("1.2"),
                description="Выгода на 20% выше",
            ),
            Scenario(
                name="выгода_-20%",
                cost_multiplier=Decimal("1.0"),
                benefit_multiplier=Decimal("0.8"),
                description="Выгода на 20% ниже",
            ),
            Scenario(
                name="пессимистичный",
                cost_multiplier=Decimal("1.2"),
                benefit_multiplier=Decimal("0.8"),
                description="Пессимистичный сценарий",
            ),
            Scenario(
                name="оптимистичный",
                cost_multiplier=Decimal("0.8"),
                benefit_multiplier=Decimal("1.2"),
                description="Оптимистичный сценарий",
            ),
        ]

        result: dict[str, ROICalculation] = {}
        for scenario in scenarios:
            result[scenario.name] = self.calculate_roi(scenario=scenario)

        return result

    def roi_report(self) -> dict[str, Any]:
        """Полный отчёт ROI с sensitivity-анализом."""
        base = self.calculate_roi()
        sensitivity = self.calculate_sensitivity()

        return {
            "base": base.to_dict(),
            "sensitivity": {
                name: calc.to_dict() for name, calc in sensitivity.items()
            },
            "cost_breakdown": {
                k: float(v) for k, v in self.calculate_monthly_costs().items()
            },
            "benefit_breakdown": {
                k: float(v) for k, v in self.calculate_monthly_benefits().items()
            },
            "generated_at": _now(),
            "note": "Все цифры — сценарная модель, точные затраты не раскрываются.",
        }
