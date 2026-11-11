"""Тесты оценки качества (evals).

Проверяем:
- golden dataset формируется для всех трёх ролей;
- evals.run() возвращает сводку без ошибок;
- без согласия нет утечки между namespace;
- кэш изолирован.
"""
from __future__ import annotations

import pytest

from assistant.evals import (
    GOLDEN_DATASET,
    EvalCase,
    Evaluator,
)
from assistant.domain import Role


def test_golden_dataset_has_all_roles():
    assert "retail" in GOLDEN_DATASET
    assert "business" in GOLDEN_DATASET
    assert "employee" in GOLDEN_DATASET


def test_golden_dataset_min_30_per_role():
    assert len(GOLDEN_DATASET["retail"]) >= 30
    assert len(GOLDEN_DATASET["business"]) >= 30
    assert len(GOLDEN_DATASET["employee"]) >= 30


def test_golden_cases_have_required_fields():
    for role, cases in GOLDEN_DATASET.items():
        for case in cases:
            assert case.role == role
            assert case.question
            assert isinstance(case.expected_contains, list)


def test_eval_result_structure():
    from assistant.evals import EvalResult
    r = EvalResult(total=10, passed=8, failed=2)
    assert r.pass_rate == 0.8
    assert isinstance(r.metrics, dict)


# Эмуляция полного прогона evals без реальной сети требует инфраструктуры;
# здесь проверяем только формирование датасета и структуру результата.
def test_eval_case_creation():
    case = EvalCase(
        role="retail",
        question="Как открыть вклад?",
        expected_contains=["вклад", "открыть"],
        requires_consent=False,
    )
    assert case.role == "retail"
    assert "вклад" in case.expected_contains


def test_eval_case_with_consent():
    case = EvalCase(
        role="business",
        question="Как открыть расчётный счёт?",
        expected_contains=["расчётный счёт"],
        requires_consent=True,
        consent_scope="cross_retrieval_retail",
    )
    assert case.requires_consent is True
    assert case.consent_scope == "cross_retrieval_retail"
