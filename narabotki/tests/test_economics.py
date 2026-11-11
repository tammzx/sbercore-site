"""Тесты ROI-калькулятора: воспроизводимость, sensitivity, границы."""
from __future__ import annotations

from decimal import Decimal

from assistant.economics import BenefitModel, CostModel, EconomicsService


def test_report_is_reproducible() -> None:
    """Два расчёта с одинаковыми параметрами дают одинаковые числа."""
    first = EconomicsService().roi_report()
    second = EconomicsService().roi_report()

    first_base = {k: v for k, v in first["base"].items() if k != "generated_at"}
    second_base = {k: v for k, v in second["base"].items() if k != "generated_at"}

    assert first_base == second_base


def test_sensitivity_bounds() -> None:
    """Пессимистичный сценарий хуже базового, оптимистичный — лучше."""
    report = EconomicsService().roi_report()
    base = report["base"]
    sensitivity = report["sensitivity"]

    assert "пессимистичный" in sensitivity
    assert "оптимистичный" in sensitivity
    assert sensitivity["пессимистичный"]["npv_rub"] < base["npv_rub"]
    assert sensitivity["оптимистичный"]["npv_rub"] > base["npv_rub"]


def test_zero_benefit_gives_negative_roi() -> None:
    """При нулевой выгоде ROI отрицательный, окупаемость не наступает."""
    service = EconomicsService(
        benefit_model=BenefitModel(
            churn_reduction_percent=Decimal("0"),
            ltv_per_client=Decimal("0"),
            total_clients=0,
            repeat_contact_reduction_percent=Decimal("0"),
            monthly_repeat_contacts=0,
            cross_sell_uplift_percent=Decimal("0"),
            cross_sell_margin_per_client=Decimal("0"),
        )
    )
    base = service.roi_report()["base"]

    assert base["monthly_benefits_rub"] == 0.0
    assert base["monthly_net_rub"] < 0
    assert base["roi_percent"] < 0
    assert base["payback_period_months"] == float("inf")


def test_cost_breakdown_sums_to_total() -> None:
    """Разбивка затрат согласована с итоговой суммой."""
    report = EconomicsService().roi_report()
    breakdown = report["cost_breakdown"]

    aggregated = (
        breakdown["tokens_total_rub"]
        + breakdown["infrastructure_rub"]
        + breakdown["support_rub"]
    )
    assert abs(aggregated - breakdown["total_rub"]) < 0.01


def test_custom_cost_model_changes_result() -> None:
    """Изменение модели затрат меняет итоговый ROI."""
    base = EconomicsService().roi_report()["base"]

    expensive = EconomicsService(
        cost_model=CostModel(
            monthly_contacts=200_000,
            cost_per_1k_tokens_pro=Decimal("4.00"),
            cost_per_1k_tokens_reasoning=Decimal("10.00"),
        )
    ).roi_report()["base"]

    assert expensive["monthly_costs_rub"] > base["monthly_costs_rub"]
    assert expensive["roi_percent"] < base["roi_percent"]
