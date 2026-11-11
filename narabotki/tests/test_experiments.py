"""Тесты A/B-экспериментов: детерминированность, распределение, blinding."""
from __future__ import annotations

from assistant.experiments import (
    ExperimentGroup,
    ExperimentsService,
)
from tests.fakes import FakeStore


async def _running_experiment(
    service: ExperimentsService,
    *,
    percent: float = 50.0,
) -> str:
    experiment = await service.create_experiment(
        name="Проактивные советы",
        hypothesis="Проактивные советы повышают retention_7d",
        test_group_percent=percent,
        start_date="2026-11-01T00:00:00+00:00",
    )
    await service.start_experiment(experiment.id)
    return experiment.id


async def test_assignment_is_deterministic() -> None:
    """Один и тот же пользователь всегда попадает в одну группу."""
    service = ExperimentsService(FakeStore())
    exp_id = await _running_experiment(service)

    first = await service.assign_user(exp_id, "user-42")
    second = await service.assign_user(exp_id, "user-42")

    assert first is second
    assert await service.get_user_group(exp_id, "user-42") is first


async def test_assignment_distribution() -> None:
    """При 50% тестовой группы распределение близко к половине."""
    service = ExperimentsService(FakeStore())
    exp_id = await _running_experiment(service, percent=50.0)

    groups = [
        await service.assign_user(exp_id, f"user-{i}") for i in range(400)
    ]
    treatment = sum(g is ExperimentGroup.TREATMENT for g in groups)

    assert 140 <= treatment <= 260


async def test_report_is_blinded() -> None:
    """Отчёт не раскрывает пользователей и их индивидуальные группы."""
    service = ExperimentsService(FakeStore())
    exp_id = await _running_experiment(service)

    for i in range(30):
        await service.record_metric(
            exp_id,
            f"control-{i}",
            ExperimentGroup.CONTROL,
            "retention_7d",
            0.20 + (i % 3) * 0.01,
        )
        await service.record_metric(
            exp_id,
            f"treatment-{i}",
            ExperimentGroup.TREATMENT,
            "retention_7d",
            0.40 + (i % 3) * 0.01,
        )

    report = await service.get_report(exp_id)
    assert report is not None

    serialized = report.__dict__
    assert "assignments" not in serialized
    assert all("user" not in str(key) for key in serialized)
    assert report.control_count == 30
    assert report.treatment_count == 30


async def test_significant_lift_detected() -> None:
    """Явный положительный эффект даёт значимый lift и p < 0.05."""
    service = ExperimentsService(FakeStore())
    exp_id = await _running_experiment(service)

    for i in range(40):
        await service.record_metric(
            exp_id,
            f"control-{i}",
            ExperimentGroup.CONTROL,
            "retention_7d",
            0.20 + (i % 3) * 0.01,
        )
        await service.record_metric(
            exp_id,
            f"treatment-{i}",
            ExperimentGroup.TREATMENT,
            "retention_7d",
            0.40 + (i % 3) * 0.01,
        )

    report = await service.get_report(exp_id)
    assert report is not None
    assert report.lift_percent > 0
    assert report.p_value < 0.05
    assert report.is_significant is True


async def test_report_without_data_is_not_significant() -> None:
    """Без метрик отчёт не делает ложного вывода о значимости."""
    service = ExperimentsService(FakeStore())
    exp_id = await _running_experiment(service)

    report = await service.get_report(exp_id)
    assert report is not None
    assert report.is_significant is False
    assert report.conclusion == "Недостаточно данных для вывода"
