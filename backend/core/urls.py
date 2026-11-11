from django.urls import path
from django.views.decorators.http import require_GET
from django.http import JsonResponse


@require_GET
def health(request):
    return JsonResponse({"status": "ok"})


@require_GET
def overview(request):
    return JsonResponse({
        "label": "Инвестиционная гипотеза для экосистемы",
        "product": "sbercore",
        "display_name": "ЯдроСбер",
        "purpose": "Персональный слой оркестрации AI-сценариев, операций и измерения бизнес-эффекта",
        "evidence_status": "concept",
    })


@require_GET
def economics(request):
    return JsonResponse({
        "status": "team_scenario_model",
        "currency": "RUB",
        "horizon": [2027, 2028, 2029],
        "investment_bn": [2.40, 1.80, 1.45],
        "realizable_effect_bn": [0.80, 3.52, 9.16],
        "net_effect_bn": [-1.60, 1.72, 7.71],
        "cumulative_effect_bn": [-1.60, 0.12, 7.83],
        "stress_case": {
            "effect_share": 0.5,
            "cumulative_effect_bn": [-2.00, -2.04, 1.09],
            "payback_year": 2029,
        },
        "note": "Сценарная модель команды; допущения требуют проверки на данных Сбера. Эффект API исключён из окупаемости.",
        "components": [
            {
                "key": "productivity",
                "name": "Реализуемый эффект труда",
                "sign": "+",
                "description": "0,67 / 2,88 / 7,78 млрд ₽ по годам; засчитывается только подтверждённая доля конвертации высвобождённого времени в результат.",
            },
            {
                "key": "business_clients",
                "name": "Маржинальный вклад бизнес-клиентов",
                "sign": "+",
                "description": "0,13 / 0,64 / 1,38 млрд ₽ по годам; сценарная оценка вклада дополнительных активных клиентов.",
            },
            {
                "key": "platform_cost",
                "name": "Разработка, эксплуатация и контроль платформы",
                "sign": "−",
                "description": "2,40 / 1,80 / 1,45 млрд ₽ затрат в 2027–2029 годах.",
            },
        ],
        "formula": "incremental_margin + verified_productivity_effect - total_project_cost",
    })


urlpatterns = [
    path("api/health/", health),
    path("api/overview/", overview),
    path("api/economics/", economics),
]
