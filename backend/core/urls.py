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
        "product": "SberCore",
        "purpose": "Общее ядро для оркестрации AI-сценариев и измерения бизнес-эффекта",
        "evidence_status": "concept",
    })


@require_GET
def economics(request):
    return JsonResponse({
        "status": "illustrative_model",
        "components": [
            {
                "key": "uplift",
                "name": "Дополнительный маржинальный доход",
                "sign": "+",
                "description": "Инкрементальная маржа против контрольной группы, а не атрибуция всех продаж кампании.",
            },
            {
                "key": "savings",
                "name": "Снижение стоимости коммуникаций и интеграций",
                "sign": "+",
                "description": "Подтверждённая экономия с учётом стоимости каналов, повторных разработок и операций.",
            },
            {
                "key": "platform_cost",
                "name": "Разработка, эксплуатация и governance ядра",
                "sign": "−",
                "description": "Полная стоимость владения: команда, инфраструктура, безопасность, поддержка и контроль.",
            },
        ],
        "formula": "incremental_margin + verified_savings - total_cost_of_ownership",
        "financial_estimates": None,
    })


urlpatterns = [
    path("api/health/", health),
    path("api/overview/", overview),
    path("api/economics/", economics),
]
