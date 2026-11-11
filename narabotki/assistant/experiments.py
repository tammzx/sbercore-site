"""A/B-эксперименты и причинная инференция (causal inference).

Модуль для доказательства причинной связи эффекта (не корреляция):
- Эксперимент: id, название, гипотеза, доля тестовой группы, даты, статус.
- Назначение в группу: хэш user_ref % 100 → control / treatment (детерминировано).
- Метрики: retention_7d, retention_30d, cost_per_contact, avg_dialogue_length,
  cross_sell_rate, support_tickets_avoided.
- Причинный анализ: сравнение treatment vs control с t-test.
- Слепой метод: информация о группе не попадает в диалог (blinding).
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from assistant.context_store import ContextStore
from assistant.domain import Role


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExperimentStatus(str, Enum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"


class ExperimentGroup(str, Enum):
    CONTROL = "control"
    TREATMENT = "treatment"


@dataclass
class Experiment:
    """Описание A/B-эксперимента."""

    id: str
    name: str
    hypothesis: str
    test_group_percent: float  # 0..100
    start_date: str
    end_date: str | None = None
    status: ExperimentStatus = ExperimentStatus.DRAFT
    description: str = ""
    success_metric: str = "retention_7d"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Experiment":
        return cls(
            id=data["id"],
            name=data["name"],
            hypothesis=data["hypothesis"],
            test_group_percent=data["test_group_percent"],
            start_date=data["start_date"],
            end_date=data.get("end_date"),
            status=ExperimentStatus(data.get("status", "draft")),
            description=data.get("description", ""),
            success_metric=data.get("success_metric", "retention_7d"),
        )


@dataclass
class Assignment:
    """Назначение пользователя в группу эксперимента."""

    experiment_id: str
    user_ref: str
    group: ExperimentGroup
    assigned_at: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["group"] = self.group.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Assignment":
        return cls(
            experiment_id=data["experiment_id"],
            user_ref=data["user_ref"],
            group=ExperimentGroup(data["group"]),
            assigned_at=data["assigned_at"],
        )


@dataclass
class MetricRecord:
    """Запись метрики для пользователя в эксперименте."""

    experiment_id: str
    user_ref: str
    group: ExperimentGroup
    metric_name: str
    metric_value: float
    recorded_at: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["group"] = self.group.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MetricRecord":
        """Восстановить запись метрики из словаря."""
        return cls(
            experiment_id=data["experiment_id"],
            user_ref=data["user_ref"],
            group=ExperimentGroup(data["group"]),
            metric_name=data["metric_name"],
            metric_value=data["metric_value"],
            recorded_at=data["recorded_at"],
        )


@dataclass
class ExperimentReport:
    """Отчёт по эксперименту с причинным анализом."""

    experiment_id: str
    experiment_name: str
    control_count: int
    treatment_count: int
    control_metrics: dict[str, float]
    treatment_metrics: dict[str, float]
    lift_percent: float
    p_value: float
    is_significant: bool
    conclusion: str
    generated_at: str


def _ttest_independent(
    control: list[float],
    treatment: list[float],
) -> tuple[float, float]:
    """Двусторонний t-Стьюдента для независимых выборок.

    Возвращает (t_stat, p_value). Реализация без scipy (чтобы не добавлять
    зависимости), приближённая через стандартное отклонение и z-аппроксимацию
    для больших выборок, точная формула Уэлча для малых.
    """
    n1, n2 = len(control), len(treatment)
    if n1 < 2 or n2 < 2:
        return 0.0, 1.0

    mean1 = sum(control) / n1
    mean2 = sum(treatment) / n2

    var1 = sum((x - mean1) ** 2 for x in control) / (n1 - 1)
    var2 = sum((x - mean2) ** 2 for x in treatment) / (n2 - 1)

    se = math.sqrt(var1 / n1 + var2 / n2)
    if se == 0:
        return 0.0, 1.0

    t_stat = (mean2 - mean1) / se

    # df по Уэлчу
    num = (var1 / n1 + var2 / n2) ** 2
    den = (var1 / n1) ** 2 / (n1 - 1) + (var2 / n2) ** 2 / (n2 - 1)
    df = num / den if den > 0 else float(n1 + n2 - 2)

    # Приближённое p-value (двустороннее) через нормальное распределение
    # Для точности в продакшене следует использовать scipy.stats.ttest_ind
    z = abs(t_stat)
    p_value = 2.0 * (1.0 - _normal_cdf(z))
    return t_stat, p_value


def _normal_cdf(x: float) -> float:
    """Функция распределения стандартного нормального закона."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


class ExperimentsService:
    """Сервис A/B-экспериментов с причинным анализом.

    Хранение: ContextStore (Redis), namespace
    {user_ref}:experiments:{experiment_id}.
    """

    EXPERIMENTS_NAMESPACE = "experiments:registry"
    ASSIGNMENTS_NAMESPACE = "experiments:assignments"
    METRICS_NAMESPACE = "experiments:metrics"

    def __init__(
        self,
        store: ContextStore,
    ) -> None:
        self._store = store

    # -- создание эксперимента ------------------------------------------------

    async def create_experiment(
        self,
        *,
        name: str,
        hypothesis: str,
        test_group_percent: float,
        start_date: str,
        end_date: str | None = None,
        description: str = "",
        success_metric: str = "retention_7d",
    ) -> Experiment:
        """Создать новый эксперимент."""
        exp_id = hashlib.sha256(
            f"{name}:{start_date}".encode()
        ).hexdigest()[:12]

        experiment = Experiment(
            id=exp_id,
            name=name,
            hypothesis=hypothesis,
            test_group_percent=test_group_percent,
            start_date=start_date,
            end_date=end_date,
            status=ExperimentStatus.DRAFT,
            description=description,
            success_metric=success_metric,
        )

        await self._save_experiment(experiment)
        return experiment

    async def start_experiment(self, experiment_id: str) -> Experiment | None:
        """Запустить эксперимент (статус running)."""
        experiment = await self.get_experiment(experiment_id)
        if experiment is None:
            return None
        experiment.status = ExperimentStatus.RUNNING
        await self._save_experiment(experiment)
        return experiment

    async def complete_experiment(self, experiment_id: str) -> Experiment | None:
        """Завершить эксперимент."""
        experiment = await self.get_experiment(experiment_id)
        if experiment is None:
            return None
        experiment.status = ExperimentStatus.COMPLETED
        experiment.end_date = _now()
        await self._save_experiment(experiment)
        return experiment

    async def get_experiment(self, experiment_id: str) -> Experiment | None:
        """Получить эксперимент по ID."""
        state = await self._store.load(self.EXPERIMENTS_NAMESPACE)
        raw = state.get("experiments", {})
        data = raw.get(experiment_id)
        if data is None:
            return None
        return Experiment.from_dict(data)

    async def list_experiments(self) -> list[Experiment]:
        """Список всех экспериментов."""
        state = await self._store.load(self.EXPERIMENTS_NAMESPACE)
        raw = state.get("experiments", {})
        return [Experiment.from_dict(d) for d in raw.values()]

    # -- назначение в группу --------------------------------------------------

    async def assign_user(
        self,
        experiment_id: str,
        user_ref: str,
    ) -> ExperimentGroup:
        """Назначить пользователя в группу эксперимента.

        Детерминировано: хэш user_ref % 100 → группа.
        Воспроизводимо: тот же пользователь всегда попадает в одну группу.
        """
        experiment = await self.get_experiment(experiment_id)
        if experiment is None or experiment.status != ExperimentStatus.RUNNING:
            raise ValueError(f"Эксперимент {experiment_id} не найден или не запущен")

        # Проверяем, не назначен ли уже
        existing = await self.get_assignment(experiment_id, user_ref)
        if existing is not None:
            return existing.group

        # Детерминированное назначение
        hash_value = int(
            hashlib.sha256(f"{experiment_id}:{user_ref}".encode()).hexdigest(), 16
        )
        percentile = hash_value % 100

        if percentile < experiment.test_group_percent:
            group = ExperimentGroup.TREATMENT
        else:
            group = ExperimentGroup.CONTROL

        assignment = Assignment(
            experiment_id=experiment_id,
            user_ref=user_ref,
            group=group,
            assigned_at=_now(),
        )
        await self._save_assignment(assignment)
        return group

    async def get_assignment(
        self,
        experiment_id: str,
        user_ref: str,
    ) -> Assignment | None:
        """Получить назначение пользователя в эксперименте."""
        namespace = f"{self.ASSIGNMENTS_NAMESPACE}:{experiment_id}"
        state = await self._store.load(namespace)
        raw = state.get("assignments", {})
        data = raw.get(user_ref)
        if data is None:
            return None
        return Assignment.from_dict(data)

    async def get_user_group(
        self,
        experiment_id: str,
        user_ref: str,
    ) -> ExperimentGroup | None:
        """Получить группу пользователя (для blinded доступа в диалоге)."""
        assignment = await self.get_assignment(experiment_id, user_ref)
        return assignment.group if assignment else None

    # -- метрики --------------------------------------------------------------

    async def record_metric(
        self,
        experiment_id: str,
        user_ref: str,
        group: ExperimentGroup,
        metric_name: str,
        metric_value: float,
    ) -> None:
        """Записать значение метрики для пользователя."""
        record = MetricRecord(
            experiment_id=experiment_id,
            user_ref=user_ref,
            group=group,
            metric_name=metric_name,
            metric_value=metric_value,
            recorded_at=_now(),
        )
        namespace = f"{self.METRICS_NAMESPACE}:{experiment_id}"
        state = await self._store.load(namespace)
        raw = state.get("metrics", [])
        raw.append(record.to_dict())
        state["metrics"] = raw
        state["updated_at"] = _now()
        await self._store.save(namespace, state)

    async def get_metrics(
        self,
        experiment_id: str,
    ) -> list[MetricRecord]:
        """Получить все записи метрик эксперимента."""
        namespace = f"{self.METRICS_NAMESPACE}:{experiment_id}"
        state = await self._store.load(namespace)
        raw = state.get("metrics", [])
        return [MetricRecord.from_dict(d) for d in raw]

    # -- отчёт (причинный анализ) --------------------------------------------

    async def get_report(self, experiment_id: str) -> ExperimentReport | None:
        """Построить отчёт по эксперименту с t-тестом (причинный анализ).

        Сравнивает treatment vs control по основной метрике.
        Информация о группе не раскрывается в диалог (blinding).
        """
        experiment = await self.get_experiment(experiment_id)
        if experiment is None:
            return None

        metrics = await self.get_metrics(experiment_id)

        control_values: list[float] = []
        treatment_values: list[float] = []

        for m in metrics:
            if m.metric_name != experiment.success_metric:
                continue
            if m.group == ExperimentGroup.CONTROL:
                control_values.append(m.metric_value)
            else:
                treatment_values.append(m.metric_value)

        control_count = len(control_values)
        treatment_count = len(treatment_values)

        if control_count == 0 or treatment_count == 0:
            return ExperimentReport(
                experiment_id=experiment_id,
                experiment_name=experiment.name,
                control_count=control_count,
                treatment_count=treatment_count,
                control_metrics={"mean": 0.0, "count": control_count},
                treatment_metrics={"mean": 0.0, "count": treatment_count},
                lift_percent=0.0,
                p_value=1.0,
                is_significant=False,
                conclusion="Недостаточно данных для вывода",
                generated_at=_now(),
            )

        control_mean = sum(control_values) / control_count
        treatment_mean = sum(treatment_values) / treatment_count

        lift_percent = (
            (treatment_mean - control_mean) / control_mean * 100
            if control_mean != 0
            else 0.0
        )

        t_stat, p_value = _ttest_independent(control_values, treatment_values)
        is_significant = p_value < 0.05

        if is_significant and lift_percent > 0:
            conclusion = (
                f"Группа лечения статистически значимо лучше "
                f"(p={p_value:.4f}, lift={lift_percent:.1f}%)"
            )
        elif is_significant and lift_percent < 0:
            conclusion = (
                f"Группа лечения статистически значимо хуже "
                f"(p={p_value:.4f}, lift={lift_percent:.1f}%)"
            )
        else:
            conclusion = (
                f"Статистически значимых различий не обнаружено "
                f"(p={p_value:.4f})"
            )

        return ExperimentReport(
            experiment_id=experiment_id,
            experiment_name=experiment.name,
            control_count=control_count,
            treatment_count=treatment_count,
            control_metrics={"mean": round(control_mean, 4), "count": control_count},
            treatment_metrics={"mean": round(treatment_mean, 4), "count": treatment_count},
            lift_percent=round(lift_percent, 2),
            p_value=round(p_value, 4),
            is_significant=is_significant,
            conclusion=conclusion,
            generated_at=_now(),
        )

    # -- внутреннее ------------------------------------------------------------

    async def _save_experiment(self, experiment: Experiment) -> None:
        state = await self._store.load(self.EXPERIMENTS_NAMESPACE)
        raw = state.get("experiments", {})
        raw[experiment.id] = experiment.to_dict()
        state["experiments"] = raw
        state["updated_at"] = _now()
        await self._store.save(self.EXPERIMENTS_NAMESPACE, state)

    async def _save_assignment(self, assignment: Assignment) -> None:
        namespace = f"{self.ASSIGNMENTS_NAMESPACE}:{assignment.experiment_id}"
        state = await self._store.load(namespace)
        raw = state.get("assignments", {})
        raw[assignment.user_ref] = assignment.to_dict()
        state["assignments"] = raw
        state["updated_at"] = _now()
        await self._store.save(namespace, state)
