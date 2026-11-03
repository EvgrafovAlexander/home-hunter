"""Формы личных квартир, просмотров и фотографий."""

from django import forms
from django.core.exceptions import ValidationError

from .models import ViewingApartment, ViewingEntry
from .services.viewing_photos import prepare_viewing_photos


class ApartmentForm(forms.ModelForm):
    version = forms.IntegerField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = ViewingApartment
        fields = ("title", "address", "district", "rooms", "area", "floor", "floors_total", "asking_price",
                  "negotiated_price", "price_note", "contact_name", "contact_phone", "contact_role", "decision",
                  "rating", "pros", "cons", "questions")
        widgets = {name: forms.Textarea(attrs={"rows": 3}) for name in ("price_note", "pros", "cons", "questions")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["version"].initial = self.instance.version
            self.fields["version"].required = True
        self.fields["title"].widget.attrs["placeholder"] = "Например, квартира с террасой на Ленина"
        for name in ("price_note", "pros", "cons", "questions"):
            self.fields[name].max_length = 10000

    def clean(self):
        data = super().clean()
        if data.get("floor") is not None and data.get("floors_total") is not None:
            if data["floor"] > data["floors_total"]:
                self.add_error("floor", "Этаж не может быть выше этажности дома.")
        return data


class MultiplePhotoInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultiplePhotoField(forms.FileField):
    widget = MultiplePhotoInput

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else ([data] if data else [])
        return [super(MultiplePhotoField, self).clean(item) for item in files]


class EntryForm(forms.ModelForm):
    photos = MultiplePhotoField(label="Фотографии", required=False,
                                widget=MultiplePhotoInput(attrs={"accept": "image/jpeg,image/png,image/webp"}))
    version = forms.IntegerField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = ViewingEntry
        fields = ("kind", "status", "happened_at", "body")
        widgets = {"body": forms.Textarea(attrs={"rows": 4, "placeholder": "Что понравилось, что нужно проверить…"}),
                   "happened_at": forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["body"].max_length = 20000
        self.fields["happened_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        if self.instance.pk:
            self.fields["version"].initial = self.instance.version
            self.fields["version"].required = True

    def clean(self):
        data = super().clean()
        if data.get("kind") == "visit":
            if not data.get("happened_at"):
                self.add_error("happened_at", "Укажите дату и время просмотра.")
            if not data.get("status"):
                self.add_error("status", "Выберите статус просмотра.")
        else:
            data["status"], data["happened_at"] = "", None
            if not data.get("body") and not data.get("photos") and not (self.instance.pk and self.instance.photos.exists()):
                self.add_error("body", "Добавьте комментарий или фотографию.")
        try:
            self.prepared_photos = prepare_viewing_photos(data.get("photos", []))
        except ValidationError as exc:
            self.add_error("photos", exc)
        return data


class ListingReferenceForm(forms.Form):
    reference = forms.CharField(label="ID или ссылка HomeHunter", max_length=1000,
                               widget=forms.TextInput(attrs={"placeholder": "Например, 123 или /listings/123/"}))


class PhotoCaptionForm(forms.Form):
    caption = forms.CharField(label="Подпись", max_length=200, required=False)
