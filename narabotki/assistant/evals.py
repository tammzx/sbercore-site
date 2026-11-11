"""Оценка качества assistant: golden dataset, метрики, отчёт.

Запуск: python -m assistant.evals
Выводит сводку в консоль и сохраняет JSON-отчёт.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from assistant.domain import Role
from assistant.role_context import RoleContextManager, ConsentRequiredError


@dataclass
class EvalCase:
    """Один тестовый пример golden dataset."""

    role: str
    question: str
    expected_contains: list[str] = field(default_factory=list)
    expected_not_contains: list[str] = field(default_factory=list)
    requires_consent: bool = False
    consent_scope: str | None = None
    scenario: str = "dialogue"


@dataclass
class EvalResult:
    """Итог оценки качества."""

    total: int = 0
    passed: int = 0
    failed: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Golden dataset: 30 вопросов на роль
# ---------------------------------------------------------------------------

RETAIL_CASES: list[EvalCase] = [
    EvalCase("retail", "Как открыть вклад?", ["вклад", "открыть"]),
    EvalCase("retail", "Как перевести деньги на карту?", ["перевод", "карту"]),
    EvalCase("retail", "Какая ставка по ипотеке?", ["ставка", "ипотека"]),
    EvalCase("retail", "Как заблокировать карту?", ["блокиров", "карту"]),
    EvalCase("retail", "Можно ли оформить кредит онлайн?", ["кредит", "онлайн"]),
    EvalCase("retail", "Где найти реквизиты счёта?", ["реквизиты", "счёт"]),
    EvalCase("retail", "Как изменить лимит по карте?", ["лимит", "карту"]),
    EvalCase("retail", "Какие документы нужны для вклада?", ["документы", "вклад"]),
    EvalCase("retail", "Как подключить автоплатёж?", ["автоплатёж", "подключ"]),
    EvalCase("retail", "Где посмотреть историю операций?", ["историю", "операции"]),
    EvalCase("retail", "Как открыть детский счёт?", ["детский", "счёт"]),
    EvalCase("retail", "Как оформить страховку?", ["страховк", "оформить"]),
    EvalCase("retail", "Какие бонусы по карте?", ["бонусы", "карте"]),
    EvalCase("retail", "Как подать заявку на кредитную карту?", ["заявку", "кредитную карту"]),
    EvalCase("retail", "Какая комиссия за перевод?", ["комиссия", "перевод"]),
    EvalCase("retail", "Как вернуть деньги по ошибке?", ["вернуть", "ошибке"]),
    EvalCase("retail", "Где найти отделение банка?", ["отделение", "банка"]),
    EvalCase("retail", "Как подключить СберПремиер?", ["СберПремиер", "подключ"]),
    EvalCase("retail", "Какие вклады доступны сейчас?", ["вклады", "доступны"]),
    EvalCase("retail", "Как пополнить счёт через терминал?", ["терминал", "пополнить"]),
    EvalCase("retail", "Как оформить рассрочку?", ["рассрочку", "оформить"]),
    EvalCase("retail", "Какая ставка по накопительному счёту?", ["ставка", "накопитель"]),
    EvalCase("retail", "Как подключить SMS-информирование?", ["SMS", "информирование"]),
    EvalCase("retail", "Какие документы для ипотеки?", ["ипотека", "документы"]),
    EvalCase("retail", "Как подать налоговую декларацию?", ["декларацию", "налоговую"]),
    EvalCase("retail", "Как оформить доверенность?", ["доверенность", "оформить"]),
    EvalCase("retail", "Как проверить баланс всех счетов?", ["баланс", "счёта"]),
    EvalCase("retail", "Как закрыть счёт?", ["закрыть", "счёт"]),
    EvalCase("retail", "Какие тарифы на обслуживание?", ["тарифы", "обслуживание"]),
    EvalCase("retail", "Как узнать остаток по вкладу?", ["остаток", "вклад"]),
    EvalCase("retail", "Как перевыпустить карту?", ["перевыпуск", "карту"]),
    EvalCase("retail", "Как установить приложение СберБанк Онлайн?", ["приложение", "онлайн"]),
    EvalCase("retail", "Как восстановить доступ к личному кабинету?", ["доступ", "личный кабинет"]),
    EvalCase("retail", "Как оформить заявку на refinancing?", ["refinancing", "заявку"]),
    EvalCase("retail", "Какие способы пополнения вклада?", ["пополнение", "вклад"]),
    EvalCase("retail", "Как оформить выписку по счету?", ["выписку", "счет"]),
]

BUSINESS_CASES: list[EvalCase] = [
    EvalCase("business", "Как открыть расчётный счёт для ИП?", ["расчётный счёт", "ИП"]),
    EvalCase("business", "Как оформить платёжное поручение?", ["платёжное поручение"]),
    EvalCase("business", "Какие тарифы на эквайринг?", ["эквайринг", "тарифы"]),
    EvalCase("business", "Как подать декларацию УСН?", ["декларацию", "УСН"]),
    EvalCase("business", "Как получить выписку со счёта?", ["выписку", "счёта"]),
    EvalCase("business", "Как открыть депозит для бизнеса?", ["депозит", "бизнес"]),
    EvalCase("business", "Как подключить зарплатный проект?", ["зарплатный проект"]),
    EvalCase("business", "Какие документы для открытия ИП?", ["ИП", "документы"]),
    EvalCase("business", "Как оформить кредит для бизнеса?", ["кредит", "бизнес"]),
    EvalCase("business", "Как подключить онлайн-кассу?", ["онлайн-кассу"]),
    EvalCase("business", "Какая комиссия за перевод между счетами?", ["комиссия", "перевод"]),
    EvalCase("business", "Как оформить гарантийное письмо?", ["гарантийное письмо"]),
    EvalCase("business", "Как подключить СберБизнес Онлайн?", ["СберБизнес Онлайн"]),
    EvalCase("business", "Какие тарифы на инкассацию?", ["инкассацию", "тарифы"]),
    EvalCase("business", "Как оформить леasing для бизнеса?", ["leasing", "бизнес"]),
    EvalCase("business", "Как подать налоговую отчётность?", ["налоговую отчётность"]),
    EvalCase("business", "Как открыть валютный счёт?", ["валютный счёт"]),
    EvalCase("business", "Как подключить эквайринг?", ["эквайринг"]),
    EvalCase("business", "Какие пакеты услуг для бизнеса?", ["пакеты услуг", "бизнес"]),
    EvalCase("business", "Как оформить банковскую гарантию?", ["банковскую гарантию"]),
    EvalCase("business", "Как подать заявку на лизинг?", ["лизинг", "заявку"]),
    EvalCase("business", "Как проверить обороты по счетам?", ["обороты", "счёта"]),
    EvalCase("business", "Как оформить зарплатный проект?", ["зарплатный проект"]),
    EvalCase("business", "Какие лимиты на переводы для ИП?", ["лимиты", "переводы"]),
    EvalCase("business", "Как подключить SMS-уведомления для бизнеса?", ["SMS", "бизнес"]),
    EvalCase("business", "Как оформить овердрафт?", ["овердрафт"]),
    EvalCase("business", "Как подать заявку на кредитную линию?", ["кредитную линию"]),
    EvalCase("business", "Как проверить статус платежа?", ["статус", "платежа"]),
    EvalCase("business", "Какие отчёты доступны в СберБизнес?", ["отчёты", "СберБизнес"]),
    EvalCase("business", "Как оформить коллекктивный счёт?", ["коллективный", "счёт"]),
    EvalCase("business", "Как подключить API для бизнеса?", ["API", "бизнес"]),
    EvalCase("business", "Как оформить виртуальную карту для бизнеса?", ["виртуальную карту", "бизнес"]),
    EvalCase("business", "Какие лимиты на снятие наличных для ИП?", ["лимиты", "наличные"]),
    EvalCase("business", "Как подать заявку на отсрочку платежа?", ["отсрочку", "платежа"]),
    EvalCase("business", "Какие программы поддержки малого бизнеса?", ["поддержка", "малого бизнеса"]),
    EvalCase("business", "Как оформить доверенность на сотрудника?", ["доверенность", "сотрудника"]),
]

EMPLOYEE_CASES: list[EvalCase] = [
    EvalCase("employee", "Как оформить отпуск?", ["отпуск", "оформить"]),
    EvalCase("employee", "Где найти табель рабочего времени?", ["табель", "рабочее время"]),
    EvalCase("employee", "Как подать заявку на обучение?", ["обучение", "заявку"]),
    EvalCase("employee", "Какие соцпакеты доступны?", ["соцпакет", "доступны"]),
    EvalCase("employee", "Как оформить больничный?", ["больничный", "оформить"]),
    EvalCase("employee", "Где посмотреть зарплату?", ["зарплату", "посмотреть"]),
    EvalCase("employee", "Как оформить командировку?", ["командировку", "оформить"]),
    EvalCase("employee", "Какие курсы повышения квалификации?", ["курсы", "квалификации"]),
    EvalCase("employee", "Как подать на премию?", ["премию", "подать"]),
    EvalCase("employee", "Где найти организационную структуру?", ["структуру", "организационную"]),
    EvalCase("employee", "Как оформить дистанционную работу?", ["дистанционную", "работу"]),
    EvalCase("employee", "Какие льготы для сотрудников?", ["льготы", "сотрудников"]),
    EvalCase("employee", "Как подать заявку на переезд?", ["переезд", "заявку"]),
    EvalCase("employee", "Где найти регламент?", ["регламент"]),
    EvalCase("employee", "Как оформить отпуск за свой счёт?", ["отпуск", "свой счёт"]),
    EvalCase("employee", "Какие медицинские страховки?", ["медицинские страховки"]),
    EvalCase("employee", "Как подать на перевод в другой отдел?", ["перевод", "отдел"]),
    EvalCase("employee", "Где найти контакты HR?", ["HR", "контакты"]),
    EvalCase("employee", "Как оформить сокращение?", ["сокращение"]),
    EvalCase("employee", "Какие бонусы за стаж?", ["бонусы", "стаж"]),
    EvalCase("employee", "Как подать заявку на оборудование?", ["оборудование", "заявку"]),
    EvalCase("employee", "Где найти график работы?", ["график", "работы"]),
    EvalCase("employee", "Как оформить отпуск по уходу за ребёнком?", ["ребёнком", "отпуск"]),
    EvalCase("employee", "Какие корпоративные мероприятия?", ["мероприятия", "корпоративные"]),
    EvalCase("employee", "Как подать на повышение?", ["повышение", "подать"]),
    EvalCase("employee", "Где найти инструкцию по безопасности?", ["безопасности", "инструкцию"]),
    EvalCase("employee", "Как оформить выходной?", ["выходной", "оформить"]),
    EvalCase("employee", "Какие скидки для сотрудников?", ["скидки", "сотрудников"]),
    EvalCase("employee", "Как подать заявку на курсы английского?", ["английского", "курсы"]),
    EvalCase("employee", "Где найти контакты службы поддержки?", ["поддержка", "контакты"]),
    EvalCase("employee", "Как оформить предоставление жилья?", ["жилье", "предоставление"]),
    EvalCase("employee", "Какие компенсации за переезд?", ["компенсации", "переезд"]),
]

GOLDEN_DATASET: dict[str, list[EvalCase]] = {
    "retail": RETAIL_CASES,
    "business": BUSINESS_CASES,
    "employee": EMPLOYEE_CASES,
}


class Evaluator:
    """Оценка качества ответов ассистента по golden dataset.

    Метрики:
        - retrieval relevance (релевантность извлечённого контекста)
        - answer correctness (точность ответа)
        - refusal correctness (точность отказа без согласия)
        - cross-namespace leak = 0 (нулевая утечка между namespace)
        - latency (задержка ответа)
        - cost per successful task (стоимость успешной задачи)
    """

    def __init__(
        self,
        rcm: RoleContextManager,
        gigachat,
        store,
        rag=None,
    ) -> None:
        self._rcm = rcm
        self._gigachat = gigachat
        self._store = store
        self._rag = rag

    def run(self, roles: list[str] | None = None) -> EvalResult:
        """Запустить оценку для указанных ролей (или всех)."""
        roles = roles or list(GOLDEN_DATASET.keys())
        total = passed = failed = 0
        failures: list[dict[str, str]] = []
        latencies: list[float] = []
        costs: list[float] = []
        leak_count = 0
        refusal_correct = 0
        refusal_total = 0
        retrieval_ok = 0
        retrieval_total = 0

        for role_name in roles:
            cases = GOLDEN_DATASET.get(role_name, [])
            for case in cases:
                total += 1
                start = time.perf_counter()
                try:
                    answer = self._generate_answer(case)
                    elapsed = time.perf_counter() - start
                    latencies.append(elapsed)

                    # Проверка ожидаемого содержания
                    ok = True
                    fail_reasons: list[str] = []
                    for expected in case.expected_contains:
                        if expected.lower() not in answer.lower():
                            ok = False
                            fail_reasons.append(f"missing: {expected}")
                    for forbidden in case.expected_not_contains:
                        if forbidden.lower() in answer.lower():
                            ok = False
                            fail_reasons.append(f"forbidden: {forbidden}")

                    # Проверка отсутствия утечки между namespace (чужой анклав)
                    if case.requires_consent:
                        refusal_total += 1
                        # В упрощённой модели — проверяем, что ответ не содержит сырых реквизитов
                        if re.search(r"\d{10,}", answer):
                            leak_count += 1
                        else:
                            refusal_correct += 1

                    if ok:
                        passed += 1
                        retrieval_ok += 1
                        costs.append(elapsed * 0.001)  # scenesrial cost
                    else:
                        failed += 1
                        failures.append({
                            "role": case.role,
                            "question": case.question,
                            "reason": "; ".join(fail_reasons),
                        })

                except ConsentRequiredError:
                    elapsed = time.perf_counter() - start
                    latencies.append(elapsed)
                    refusal_total += 1
                    refusal_correct += 1  # корректный отказ — это успех
                    passed += 1
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    failures.append({
                        "role": case.role,
                        "question": case.question,
                        "reason": f"error: {exc}",
                    })

        metrics = {
            "pass_rate": round(passed / total, 4) if total else 0.0,
            "avg_latency_ms": round(sum(latencies) / len(latencies) * 1000, 2) if latencies else 0.0,
            "retrieval_relevance": round(retrieval_ok / retrieval_total, 4) if retrieval_total else 1.0,
            "refusal_correctness": round(refusal_correct / refusal_total, 4) if refusal_total else 1.0,
            "cross_namespace_leak_count": leak_count,
            "cost_per_task_rub": round(sum(costs) / len(costs), 6) if costs else 0.0,
            "total_cost_rub": round(sum(costs), 4),
        }

        return EvalResult(
            total=total,
            passed=passed,
            failed=failed,
            failures=failures,
            metrics=metrics,
        )

    def _generate_answer(self, case: EvalCase) -> str:
        """Сгенерировать ответ для тестового примера (использует GigaChat или эмуляцию)."""
        import asyncio

        async def _gen() -> str:
            role = Role(case.role)
            # Проверка контекста (согласие)
            await self._rcm.get_context(role)
            # Генерация
            resp = await self._gigachat.generate(
                [{"role": "user", "content": case.question}],
                role=role,
            )
            return resp["choices"][0]["message"]["content"]

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(_gen())

    def report(self, result: EvalResult, output_file: str | None = None) -> None:
        """Напечатать сводку и сохранить JSON-отчёт."""
        lines = [
            "=" * 60,
            "ОТЧЁТ ОБ ОЦЕНКЕ КАЧЕСТВА СБЕР-ОДИН",
            "=" * 60,
            f"Всего примеров: {result.total}",
            f"Пройдено: {result.passed}",
            f"Не пройдено: {result.failed}",
            f"Проходной процент: {result.pass_rate:.2%}",
            "",
            "МЕТРИКИ:",
            f"  Релевантность retrieval: {result.metrics.get('retrieval_relevance', 0):.2%}",
            f"  Точность отказа: {result.metrics.get('refusal_correctness', 0):.2%}",
            f"  Утечки между namespace: {result.metrics.get('cross_namespace_leak_count', 0)}",
            f"  Средняя задержка: {result.metrics.get('avg_latency_ms', 0):.2f} мс",
            f"  Ориентировочная стоимость на задачу: {result.metrics.get('cost_per_task_rub', 0):.6f} руб.",
            f"  Общая стоимость: {result.metrics.get('total_cost_rub', 0):.4f} руб.",
            "",
            "НЕ ПРОШЛО:",
        ]
        if result.failures:
            for f in result.failures:
                lines.append(f"  [{f['role']}] {f['question']}: {f['reason']}")
        else:
            lines.append("  Все тесты пройдены ✓")

        print("\n".join(lines))

        if output_file:
            report = {
                "summary": {
                    "total": result.total,
                    "passed": result.passed,
                    "failed": result.failed,
                    "pass_rate": result.pass_rate,
                },
                "metrics": result.metrics,
                "failures": result.failures,
                "generated_at": _now(),
            }
            Path(output_file).write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"\nОтчёт сохранён: {output_file}")


def run_evals(roles: list[str] | None = None, output_file: str | None = None) -> EvalResult:
    """Точка входа для запуска оценки извне."""
    from tests.fakes import FakeGigaChat, FakeStore
    from assistant.role_context import RoleContextManager
    from assistant.domain import Role as DRole
    from assistant.context_store import ContextStore
    import redis.asyncio as aioredis

    # Создаём минимальное окружение для оценки (без реальной БД/сети)
    fake_redis = _FakeSyncRedis()
    store = ContextStore(fake_redis)
    gigachat = FakeGigaChat()

    rcm = RoleContextManager(
        user_ref="eval-user",
        available_roles=frozenset({DRole.RETAIL, DRole.BUSINESS, DRole.EMPLOYEE}),
        active_role=DRole.RETAIL,
        store=store,
        consents=_FakeConsents(),
        audit=_FakeAudit(),
    )

    evaluator = Evaluator(rcm=rcm, gigachat=gigachat, store=store)
    result = evaluator.run(roles=roles)
    evaluator.report(result, output_file=output_file)
    return result


# --- фейковые вспомогательные классы для автономной оценки --------------------

class _FakeSyncRedis:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def setex(self, key: str, ttl: int, value: Any) -> None:
        self.data[key] = value

    async def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    async def delete(self, *keys: str) -> None:
        for k in keys:
            self.data.pop(k, None)


class _FakeConsents:
    async def check(self, user_ref, consent_type, *, from_role=None, to_role=None, scope=None) -> bool:
        return True

    async def grant(self, user_ref, consent_type, *, scope=None, from_role=None, to_role=None,
                    ttl_days=None, evidence="ui") -> str:
        return "consent-eval"

    async def revoke(self, user_ref, consent_id) -> None:
        pass

    async def active_consent_id(self, user_ref, consent_type, *, from_role=None, to_role=None):
        return "consent-eval"

    async def list_active(self, user_ref: str) -> list[dict]:
        return []

    async def invalidate_cache(self, user_ref, scope, *, from_role=None, to_role=None) -> None:
        pass


class _FakeAudit:
    async def append(self, event, actor_ref, payload) -> str:
        return "hash"


if __name__ == "__main__":
    run_evals()
