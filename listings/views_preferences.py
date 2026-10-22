"""HTTP-обработчики пользовательских настроек оценки объявлений."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .models import District, ScoringPreference
from .views import _decimal, _integer


@login_required
def scoring_settings(request):
    """Показать и сохранить личные правила и веса оценки квартир."""
    preference, _ = ScoringPreference.objects.get_or_create(user=request.user)
    if request.method == "POST":
        preference.min_area = _decimal(request.POST.get("min_area"))
        preference.max_area = _decimal(request.POST.get("max_area"))
        preference.min_kitchen_area = _decimal(request.POST.get("min_kitchen_area"))
        preference.max_price = _integer(request.POST.get("max_price"))
        preference.preferred_rooms = [
            value for value in request.POST.getlist("preferred_rooms") if value.isdigit()
        ]
        preference.floor_min = _integer(request.POST.get("floor_min"))
        preference.floor_max = _integer(request.POST.get("floor_max"))
        # Храним границы этажа в правильном порядке, даже если пользователь ввёл их наоборот.
        if preference.floor_min and preference.floor_max and preference.floor_min > preference.floor_max:
            preference.floor_min, preference.floor_max = preference.floor_max, preference.floor_min
        preference.preferred_districts = request.POST.getlist("preferred_districts")
        preference.preferred_repair_types = request.POST.getlist("preferred_repair_types")
        preference.prefer_photo = request.POST.get("prefer_photo") == "1"
        preference.require_lift = request.POST.get("require_lift") == "1"
        preference.require_balcony = request.POST.get("require_balcony") == "1"
        preference.prefer_furniture = request.POST.get("prefer_furniture") == "1"
        for field in (
            "market_weight",
            "freshness_weight",
            "data_weight",
            "floor_weight",
            "price_history_weight",
            "preference_weight",
        ):
            setattr(preference, field, min(100, max(0, _integer(request.POST.get(field)) or 0)))
        preference.save()
        return redirect("scoring_settings")
    return render(request, "listings/scoring_settings.html", {
        "preference": preference,
        "districts": District.objects.all(),
        "repair_types": (
            ("cosmetic", "Косметический"),
            ("euro", "Евроремонт"),
            ("withoutRepair", "Без ремонта"),
        ),
    })
