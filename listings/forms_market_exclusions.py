"""Формы ручного исключения объявления из рыночной выборки."""

from django import forms
from .models import MarketExclusion


class MarketExclusionForm(forms.Form):
    reason = forms.ChoiceField(label="Причина", choices=MarketExclusion.Reason.choices)
    comment = forms.CharField(label="Комментарий", required=False, max_length=2000,
                              widget=forms.Textarea(attrs={"rows": 3}))
