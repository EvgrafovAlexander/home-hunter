"""Страница исторической динамики рынка и проверка параметров запроса."""

from django import forms
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .models import SearchQuery
from .services.market_trends import market_trends_context


class MarketFiltersForm(forms.Form):
    source = forms.ChoiceField(label="Источник", choices=SearchQuery.Source.choices, initial="cian")
    district = forms.CharField(label="Район", required=False, max_length=255)
    microdistrict = forms.CharField(label="Микрорайон", required=False, max_length=255)
    rooms = forms.IntegerField(label="Комнат", required=False, min_value=0, max_value=20)
    area_min = forms.FloatField(label="Площадь от, м²", required=False, min_value=0, max_value=10000)
    area_max = forms.FloatField(label="До, м²", required=False, min_value=0, max_value=10000)
    weeks = forms.TypedChoiceField(label="Период", choices=((12, "12 недель"), (26, "26 недель"),
                                                          (52, "52 недели")), coerce=int, initial=12)

    def clean(self):
        data = super().clean()
        if data.get("area_min") is not None and data.get("area_max") is not None:
            if data["area_min"] > data["area_max"]:
                self.add_error("area_max", "Верхняя граница должна быть не меньше нижней.")
        return data


@login_required
def market_trends(request):
    """Показывает графики выбранного источника без смешивания дублей площадок."""
    data = request.GET.copy()
    data.setdefault("source", "cian")
    data.setdefault("weeks", "12")
    form = MarketFiltersForm(data)
    context = {"form": form}
    if form.is_valid():
        context.update(market_trends_context(form.cleaned_data))
    return render(request, "listings/market_trends.html", context)
