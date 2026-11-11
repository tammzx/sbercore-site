"""API «СберОдин»: FastAPI-роутер с эндпоинтами воронки, стоимости, согласий.

Включает все базовые эндпоинты (/chat, /roles/*, /consents/*) и новые:
    GET    /funnel/status
    POST   /funnel/transition
    GET    /cost/report
    GET    /metrics
    POST   /feedback
    POST   /consents
    GET    /consents
    POST   /consents/{id}/withdraw
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import UserSession, get_role_context_manager, get_user_session
from app.core.context_manager import RoleContextManager
from app.core.exceptions import ConsentRequiredError, RoleNotAvailableError
from app.core.roles import Role as AppRole, ConsentType as AppConsentType

from assistant.domain import Role, ConsentScope
from assistant.digital_folder import DigitalFolderService, FactCategory, FactSource
from assistant.economics import EconomicsService
from assistant.experiments import ExperimentsService
from assistant.explainability import ExplainabilityService
from assistant.guardrails import get_guardrails_service
from assistant.ip_funnel import FunnelService, FunnelStage, TriggerEvent
from assistant.model_router import ModelRouter
from assistant.proactive import ProactiveService
from assistant.profiling import ProfilingService, UserProfile, Recommendation

router = APIRouter(prefix="/v1", tags=["sber-one"])


# ---------------------------------------------------------------------------
# Фаннел
# ---------------------------------------------------------------------------

class FunnelStatusResponse(BaseModel):
    current_role: str
    current_stage: str
    available_stages: list[str]


class TransitionRequest(BaseModel):
    to_stage: str
    reason: str
    data_source: str = "funnel_detector"


class TransitionResponse(BaseModel):
    from_stage: str
    to_stage: str
    reason: str
    timestamp: str
    consent_id: str | None
    data_source: str


@router.get("/funnel/status", response_model=FunnelStatusResponse)
async def funnel_status(
    rcm: RoleContextManager = Depends(get_role_context_manager),
) -> FunnelStatusResponse:
    """Текущий статус воронки роста пользователя."""
    # Создаём сервис воронки на лету (в проде — singleton из app.state)
    from assistant.ip_funnel import FunnelService

    # Временный store и consent для работы без полной инфраструктуры
    from tests.fakes import FakeStore, FakeConsents

    store = FakeStore()
    consents = FakeConsents()
    funnel = FunnelService(rcm, consents, store)
    status = funnel.get_status()
    return FunnelStatusResponse(
        current_role=status["current_role"],
        current_stage=status["current_stage"],
        available_stages=status["available_stages"],
    )


@router.post("/funnel/transition", response_model=TransitionResponse)
async def funnel_transition(
    body: TransitionRequest,
    rcm: RoleContextManager = Depends(get_role_context_manager),
    request: Request = None,
) -> TransitionResponse:
    """Переход на новую стадию воронки (требует согласия CROSS_RETRIEVAL_RETAIL)."""
    from assistant.ip_funnel import FunnelService
    from tests.fakes import FakeStore, FakeConsents

    store = FakeStore()
    consents = FakeConsents()
    funnel = FunnelService(rcm, consents, store)

    try:
        stage = FunnelStage(body.to_stage)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Неизвестная стадия: {body.to_stage}") from exc

    try:
        transition = await funnel.transition(stage, body.reason, body.data_source)
    except ConsentRequiredError as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "consent_required",
                "consent_type": exc.scope.value,
                "scope": "retail->biz",
                "consent_url": "/v1/consents/grant?consent_type=cross_retrieval_retail&scope=retail->biz",
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return TransitionResponse(
        from_stage=transition.from_stage.value,
        to_stage=transition.to_stage.value,
        reason=transition.reason,
        timestamp=transition.timestamp,
        consent_id=transition.consent_id,
        data_source=transition.data_source,
    )


# ---------------------------------------------------------------------------
# Стоимость и метрики
# ---------------------------------------------------------------------------

class CostReportResponse(BaseModel):
    total_cost_rub: float
    total_tokens: int
    total_requests: int
    cache_hits: int
    cache_hit_rate: float
    by_role: dict[str, float]
    by_model: dict[str, float]
    by_scenario: dict[str, float]
    currency: str
    note: str


class MetricsResponse(BaseModel):
    requests_total: int
    cache_hits: int
    avg_latency_ms: float
    errors_total: int
    by_role: dict[str, int]


@router.get("/cost/report", response_model=CostReportResponse)
async def cost_report(
    rcm: RoleContextManager = Depends(get_role_context_manager),
) -> CostReportResponse:
    """Отчёт о стоимости запросов: по ролям, моделям, сценариям, кэш-хитам."""
    from assistant.model_router import ModelRouter
    from tests.fakes import FakeGigaChat, FakeStore

    store = FakeStore()
    gigachat = FakeGigaChat()
    router_ = ModelRouter(gigachat, store, rcm)
    report = router_.cost_report()
    return CostReportResponse(**report)


@router.get("/metrics", response_model=MetricsResponse)
async def metrics(
    rcm: RoleContextManager = Depends(get_role_context_manager),
) -> MetricsResponse:
    """Базовые метрики работы ассистента."""
    return MetricsResponse(
        requests_total=0,
        cache_hits=0,
        avg_latency_ms=0.0,
        errors_total=0,
        by_role={"retail": 0, "business": 0, "employee": 0},
    )


# ---------------------------------------------------------------------------
# Обратная связь
# ---------------------------------------------------------------------------

class FeedbackRequest(BaseModel):
    role: str
    question: str
    rating: str = Field(..., pattern="^(helpful|not_helpful)$")
    reason: str | None = None


class FeedbackResponse(BaseModel):
    status: str
    recorded_at: str


@router.post("/feedback", response_model=FeedbackResponse)
async def feedback(
    body: FeedbackRequest,
    rcm: RoleContextManager = Depends(get_role_context_manager),
) -> FeedbackResponse:
    """Собрать обратную связь: helpful / not_helpful + причина."""
    from datetime import datetime, timezone

    # Аудит обратной связи через rcm
    await rcm._audit("feedback.received", {
        "role": body.role,
        "question": body.question[:100],
        "rating": body.rating,
        "reason": body.reason,
    })
    return FeedbackResponse(
        status="recorded",
        recorded_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Согласия (CRUD)
# ---------------------------------------------------------------------------

class ConsentGrantRequest(BaseModel):
    consent_type: str
    scope: list[str] = Field(default_factory=list)
    from_role: str | None = None
    to_role: str | None = None
    ttl_days: int | None = Field(default=None, le=730)


@router.post("/consents")
async def grant_consent(
    body: ConsentGrantRequest,
    request: Request,
    session: UserSession = Depends(get_user_session),
) -> dict:
    """Выдать согласие на обработку ПДн."""
    try:
        consent_type = AppConsentType(body.consent_type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Неизвестный тип согласия: {body.consent_type}") from exc

    consent_id = await request.app.state.consents.grant(
        session.user_ref,
        body.consent_type,
        scope=body.scope,
        from_role=body.from_role,
        to_role=body.to_role,
        ttl_days=body.ttl_days,
        evidence="api",
    )
    return {"consent_id": consent_id, "status": "granted"}


@router.get("/consents")
async def list_consents(
    request: Request,
    session: UserSession = Depends(get_user_session),
) -> list[dict]:
    """Список активных согласий пользователя."""
    consents = await request.app.state.consents.list_active(session.user_ref)
    return [
        {
            "consent_id": str(c["id"]),
            "consent_type": c["consent_type"],
            "scope": c.get("scope", []),
            "from_role": c.get("from_role"),
            "to_role": c.get("to_role"),
            "granted_at": str(c.get("granted_at")),
            "expires_at": str(c["expires_at"]) if c.get("expires_at") else None,
        }
        for c in consents
    ]


@router.post("/consents/{consent_id}/withdraw")
async def withdraw_consent(
    consent_id: str,
    request: Request,
    session: UserSession = Depends(get_user_session),
) -> dict:
    """Отозвать согласие (мгновенно, с инвалидацией кэша)."""
    await request.app.state.consents.revoke(session.user_ref, consent_id)
    return {"status": "revoked", "consent_id": consent_id}


# ---------------------------------------------------------------------------
# Профилирование и рекомендации
# ---------------------------------------------------------------------------

class ProfileResponse(BaseModel):
    user_ref: str
    role: str
    segment: str
    preferences: list[str]
    behavior_patterns: list[dict[str, Any]]
    lifecycle_stage: str
    estimated_value_rub: float
    last_transaction_at: str | None
    total_transactions: int
    avg_transaction_rub: float
    recommended_products: list[str]
    updated_at: str


class ProfileAnalyzeRequest(BaseModel):
    transactions: list[dict[str, Any]]
    receipts: list[dict[str, Any]] = Field(default_factory=list)
    profile_info: dict[str, Any] = Field(default_factory=dict)


class RecommendationResponse(BaseModel):
    product: str
    reason: str
    confidence: float
    priority: int
    requires_consent: bool
    consent_scope: str | None


class RecommendationsRequest(BaseModel):
    context_question: str | None = None
    limit: int = Field(default=5, ge=1, le=20)


@router.get("/profile", response_model=ProfileResponse | None)
async def get_profile(
    rcm: RoleContextManager = Depends(get_role_context_manager),
) -> ProfileResponse | None:
    """Получить профиль пользователя для активной роли.

    Профилирование ведётся только для розницы и бизнеса (не для сотрудника).
    """
    role = rcm.active_role
    if role is None:
        return None
    # rcm.active_role возвращает assistant.domain.Role
    profiler = ProfilingService(rcm, _fake_consent_service())
    profile = await profiler.get_profile(role)
    if profile is None:
        return None
    return ProfileResponse(**profile.to_dict())


@router.post("/profile/analyze", response_model=ProfileResponse)
async def analyze_profile(
    body: ProfileAnalyzeRequest,
    rcm: RoleContextManager = Depends(get_role_context_manager),
) -> ProfileResponse:
    """Проанализировать транзакции и обновить профиль пользователя."""
    role = rcm.active_role
    if role is None:
        raise HTTPException(status_code=400, detail="Роль не выбрана")
    profiler = ProfilingService(rcm, _fake_consent_service())
    profile = await profiler.analyze_and_update(
        body.transactions,
        body.receipts,
        body.profile_info,
        role=role,
    )
    return ProfileResponse(**profile.to_dict())


@router.get("/recommendations", response_model=list[RecommendationResponse])
async def get_recommendations(
    context_question: str | None = None,
    limit: int = 5,
    rcm: RoleContextManager = Depends(get_role_context_manager),
) -> list[RecommendationResponse]:
    """Получить персонализированные рекомендации для активной роли."""
    role = rcm.active_role
    if role is None:
        return []
    profiler = ProfilingService(rcm, _fake_consent_service())
    recs = await profiler.get_recommendations(
        role=role,
        context_question=context_question,
        limit=limit,
    )
    return [
        RecommendationResponse(
            product=r.product,
            reason=r.reason,
            confidence=r.confidence,
            priority=r.priority,
            requires_consent=r.requires_consent,
            consent_scope=r.consent_scope.value if r.consent_scope else None,
        )
        for r in recs
    ]


# ---------------------------------------------------------------------------
# Вспомогательное: фейковый consent-service для API без полной инфраструктуры
# ---------------------------------------------------------------------------

class _FakeConsentService:
    """Заглушка для работы эндпоинтов профиля без реального ConsentService."""

    async def check(
        self,
        user_ref: str,
        scope: ConsentScope,
        *,
        from_role: str | None = None,
        to_role: str | None = None,
    ) -> bool:
        return True

    async def active_consent_id(
        self,
        user_ref: str,
        scope: ConsentScope,
        *,
        from_role: str | None = None,
        to_role: str | None = None,
    ) -> str | None:
        return None


# ---------------------------------------------------------------------------
# Цифровая папка
# ---------------------------------------------------------------------------

class FactResponse(BaseModel):
    id: str
    user_ref: str
    role: str
    category: str
    key: str
    value: Any
    source: str
    confidence: float
    created_at: str
    expires_at: str | None
    version: int
    archived: bool


class FactAddRequest(BaseModel):
    category: str
    key: str
    value: Any
    source: str = "dialogue"
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    ttl_seconds: int | None = None


@router.get("/folder", response_model=list[FactResponse])
async def get_folder(
    rcm: RoleContextManager = Depends(get_role_context_manager),
) -> list[FactResponse]:
    """Получить все факты цифровой папки для активной роли."""
    role = rcm.active_role
    if role is None:
        return []
    folder = DigitalFolderService(rcm, _fake_store())
    facts = await folder.get_all_facts(role=role)
    return [FactResponse(**f.to_dict()) for f in facts]


@router.post("/folder", response_model=FactResponse)
@router.post("/folder/facts", response_model=FactResponse)
async def add_fact(
    body: FactAddRequest,
    rcm: RoleContextManager = Depends(get_role_context_manager),
) -> FactResponse:
    """Добавить факт в цифровую папку активной роли."""
    role = rcm.active_role
    if role is None:
        raise HTTPException(status_code=400, detail="Роль не выбрана")
    try:
        category = FactCategory(body.category)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Неизвестная категория: {body.category}") from exc
    try:
        source = FactSource(body.source)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Неизвестный источник: {body.source}") from exc

    folder = DigitalFolderService(rcm, _fake_store())
    fact = await folder.add_fact(
        category=category,
        key=body.key,
        value=body.value,
        source=source,
        confidence=body.confidence,
        role=role,
        ttl_seconds=body.ttl_seconds,
    )
    return FactResponse(**fact.to_dict())


@router.delete("/folder/{fact_id}")
@router.delete("/folder/facts/{fact_id}")
async def delete_fact(
    fact_id: str,
    rcm: RoleContextManager = Depends(get_role_context_manager),
) -> dict[str, str]:
    """Архивировать факт цифровой папки."""
    role = rcm.active_role
    if role is None:
        raise HTTPException(status_code=400, detail="Роль не выбрана")
    folder = DigitalFolderService(rcm, _fake_store())
    deleted = await folder.delete_fact(fact_id, role=role)
    if not deleted:
        raise HTTPException(status_code=404, detail="Факт не найден")
    return {"status": "archived", "fact_id": fact_id}


# ---------------------------------------------------------------------------
# Проактивность
# ---------------------------------------------------------------------------

class ProactiveMessageResponse(BaseModel):
    message_id: str
    trigger_id: str | None
    role: str
    text: str
    priority: int
    suggested_action: str | None
    suggested_action_params: dict[str, Any]
    requires_consent: bool
    consent_scope: str | None
    created_at: str


class ProactiveDismissRequest(BaseModel):
    """Тело запроса на отклонение проактивного сообщения."""

    message_id: str


@router.get("/proactive/next", response_model=list[ProactiveMessageResponse])
@router.get("/proactive/pending", response_model=list[ProactiveMessageResponse])
async def get_proactive_pending(
    rcm: RoleContextManager = Depends(get_role_context_manager),
) -> list[ProactiveMessageResponse]:
    """Получить список непрочитанных проактивных сообщений."""
    role = rcm.active_role
    if role is None:
        return []
    proactive = ProactiveService(rcm, _fake_folder(), _fake_funnel(), store)
    messages = await proactive.get_pending(role=role)
    return [
        ProactiveMessageResponse(
            message_id=m.message_id,
            trigger_id=m.trigger_id,
            role=m.role.value,
            text=m.text,
            priority=m.priority,
            suggested_action=m.suggested_action,
            suggested_action_params=m.suggested_action_params,
            requires_consent=m.requires_consent,
            consent_scope=m.consent_scope.value if m.consent_scope else None,
            created_at=m.created_at,
        )
        for m in messages
    ]


@router.post("/proactive/{message_id}/dismiss")
async def dismiss_proactive(
    message_id: str,
    rcm: RoleContextManager = Depends(get_role_context_manager),
) -> dict[str, str]:
    """Отклонить проактивное сообщение."""
    role = rcm.active_role
    if role is None:
        raise HTTPException(status_code=400, detail="Роль не выбрана")
    proactive = ProactiveService(rcm, _fake_folder(), _fake_funnel(), store)
    dismissed = await proactive.dismiss(message_id, role=role)
    if not dismissed:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    return {"status": "dismissed", "message_id": message_id}


@router.post("/proactive/dismiss")
async def dismiss_proactive_by_body(
    body: ProactiveDismissRequest,
    rcm: RoleContextManager = Depends(get_role_context_manager),
) -> dict[str, str]:
    """Отклонить проактивное сообщение (упрощённый путь из ТЗ)."""
    role = rcm.active_role
    if role is None:
        raise HTTPException(status_code=400, detail="Роль не выбрана")
    proactive = ProactiveService(rcm, _fake_folder(), _fake_funnel(), store)
    dismissed = await proactive.dismiss(body.message_id, role=role)
    if not dismissed:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    return {"status": "dismissed", "message_id": body.message_id}


@router.post("/proactive/{message_id}/act")
async def act_proactive(
    message_id: str,
    rcm: RoleContextManager = Depends(get_role_context_manager),
) -> dict[str, Any]:
    """Выполнить suggested_action проактивного сообщения."""
    role = rcm.active_role
    if role is None:
        raise HTTPException(status_code=400, detail="Роль не выбрана")
    proactive = ProactiveService(rcm, _fake_folder(), _fake_funnel(), store)
    result = await proactive.act(message_id, role=role)
    if result is None:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    return result


# ---------------------------------------------------------------------------
# A/B-эксперименты
# ---------------------------------------------------------------------------

class ExperimentCreateRequest(BaseModel):
    name: str
    hypothesis: str
    test_group_percent: float = Field(default=50.0, ge=0.0, le=100.0)
    start_date: str
    end_date: str | None = None
    description: str = ""
    success_metric: str = "retention_7d"


class ExperimentResponse(BaseModel):
    id: str
    name: str
    hypothesis: str
    test_group_percent: float
    start_date: str
    end_date: str | None
    status: str
    description: str
    success_metric: str


@router.get("/experiments", response_model=list[ExperimentResponse])
async def list_experiments() -> list[ExperimentResponse]:
    """Список всех экспериментов."""
    exp_service = ExperimentsService(_fake_store())
    experiments = await exp_service.list_experiments()
    return [
        ExperimentResponse(
            id=e.id,
            name=e.name,
            hypothesis=e.hypothesis,
            test_group_percent=e.test_group_percent,
            start_date=e.start_date,
            end_date=e.end_date,
            status=e.status.value,
            description=e.description,
            success_metric=e.success_metric,
        )
        for e in experiments
    ]


@router.post("/experiments", response_model=ExperimentResponse)
async def create_experiment(
    body: ExperimentCreateRequest,
) -> ExperimentResponse:
    """Создать новый эксперимент."""
    exp_service = ExperimentsService(_fake_store())
    experiment = await exp_service.create_experiment(
        name=body.name,
        hypothesis=body.hypothesis,
        test_group_percent=body.test_group_percent,
        start_date=body.start_date,
        end_date=body.end_date,
        description=body.description,
        success_metric=body.success_metric,
    )
    await exp_service.start_experiment(experiment.id)
    return ExperimentResponse(
        id=experiment.id,
        name=experiment.name,
        hypothesis=experiment.hypothesis,
        test_group_percent=experiment.test_group_percent,
        start_date=experiment.start_date,
        end_date=experiment.end_date,
        status=experiment.status.value,
        description=experiment.description,
        success_metric=experiment.success_metric,
    )


@router.get("/experiments/{experiment_id}/report")
async def get_experiment_report(
    experiment_id: str,
) -> dict[str, Any]:
    """Отчёт по эксперименту с причинным анализом (t-test)."""
    exp_service = ExperimentsService(_fake_store())
    report = await exp_service.get_report(experiment_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Эксперимент не найден")
    return {
        "experiment_id": report.experiment_id,
        "experiment_name": report.experiment_name,
        "control_count": report.control_count,
        "treatment_count": report.treatment_count,
        "control_metrics": report.control_metrics,
        "treatment_metrics": report.treatment_metrics,
        "lift_percent": report.lift_percent,
        "p_value": report.p_value,
        "is_significant": report.is_significant,
        "conclusion": report.conclusion,
        "generated_at": report.generated_at,
    }


# ---------------------------------------------------------------------------
# ROI-калькулятор
# ---------------------------------------------------------------------------

@router.post("/economics/calculate")
async def calculate_economics(
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Расчёт ROI/NPV/payback (параметры — сценарная модель)."""
    economics_service = EconomicsService()
    report = economics_service.roi_report()
    if body:
        report["requested_parameters"] = body
    return report


@router.get("/economics/roi")
async def get_economics_roi() -> dict[str, Any]:
    """Отчёт по unit-экономике: NPV, payback, sensitivity."""
    economics_service = EconomicsService()
    return economics_service.roi_report()


# ---------------------------------------------------------------------------
# Объяснимость
# ---------------------------------------------------------------------------

class ExplainResponse(BaseModel):
    message_id: str
    user_ref: str
    role: str
    data_sources: list[str]
    key_facts: list[dict[str, Any]]
    consent_id: str | None
    consent_scope: str | None
    explanation_text: str


@router.get("/explain/{message_id}", response_model=ExplainResponse)
async def get_explain(
    message_id: str,
    rcm: RoleContextManager = Depends(get_role_context_manager),
) -> ExplainResponse:
    """Получить объяснение («почему я это вижу») для сообщения."""
    role = rcm.active_role
    if role is None:
        raise HTTPException(status_code=400, detail="Роль не выбрана")
    explain_service = ExplainabilityService(rcm, _fake_folder())
    justification = await explain_service.explain(message_id, role=role)
    if justification is None:
        raise HTTPException(status_code=404, detail="Не удалось построить объяснение")
    return ExplainResponse(
        message_id=justification.message_id,
        user_ref=justification.user_ref,
        role=justification.role.value,
        data_sources=justification.data_sources,
        key_facts=justification.key_facts,
        consent_id=justification.consent_id,
        consent_scope=justification.consent_scope.value if justification.consent_scope else None,
        explanation_text=justification.explanation_text,
    )


# ---------------------------------------------------------------------------
# Guardrails статистика
# ---------------------------------------------------------------------------

@router.get("/guardrails/stats")
async def get_guardrails_stats() -> dict[str, Any]:
    """Статистика блокировок prompt injection (без ПДн)."""
    gs = get_guardrails_service()
    if gs is None:
        return {"total_blocks": 0, "blocked_users": 0, "by_risk_level": {}, "by_pattern": {}}
    return gs.get_stats()


@router.get("/health")
async def assistant_health() -> dict[str, Any]:
    """Проверка работоспособности контура «ЯдроСбер»."""
    return {
        "status": "ok",
        "service": "sber-one-assistant",
        "version": "2.3",
        "offline": True,
    }


# ---------------------------------------------------------------------------
# Вспомогательные фейковые сервисы для API без полной инфраструктуры
# ---------------------------------------------------------------------------

def _fake_store() -> FakeStore:
    return FakeStore()


def _fake_folder() -> DigitalFolderService:
    return DigitalFolderService(rcm=_fake_rcm(), store=_fake_store())


def _fake_funnel() -> FunnelService:
    return FunnelService(rcm=_fake_rcm(), consent_service=_fake_consent_service(), store=_fake_store())


def _fake_rcm() -> RoleContextManager:
    return RoleContextManager(
        user_ref="api-fake-user",
        available_roles=frozenset({Role.RETAIL, Role.BUSINESS, Role.EMPLOYEE}),
        active_role=Role.RETAIL,
        store=_fake_store(),
        consents=_fake_consent_service(),
        audit=_fake_audit(),
    )


def _fake_audit():
    class _FakeAudit:
        async def append(self, event, actor_ref, payload) -> str:
            return "hash"
    return _FakeAudit()

