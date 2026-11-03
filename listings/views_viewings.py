"""Личные квартиры и просмотры: формы, страницы и закрытая выдача фотографий."""

from urllib.parse import urlsplit

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .forms_viewings import ApartmentForm, EntryForm, ListingReferenceForm, PhotoCaptionForm
from .models import Listing, ViewingApartment, ViewingEntry, ViewingPhoto
from .services.viewings import (
    apartment_initial, apartment_queryset, change_listing_link, linked_apartments,
    resolve_viewing_listing, save_apartment, save_viewing_entry, update_viewing_photo,
)


def _listing(request, reference):
    hosts = {urlsplit("//" + request.get_host()).hostname}
    hosts.update(host for host in settings.ALLOWED_HOSTS if host != "*" and not host.startswith("."))
    if getattr(settings, "PUBLIC_BASE_URL", ""):
        hosts.add(urlsplit(settings.PUBLIC_BASE_URL).hostname)
    return resolve_viewing_listing(reference, allowed_hosts=hosts)


def _owned(request, pk):
    return get_object_or_404(apartment_queryset(request.user, decision="all"), pk=pk)


@login_required
@require_GET
def viewing_list(request):
    query = request.GET.get("q", "").strip()[:200]
    decision = request.GET.get("decision", "")
    apartments = apartment_queryset(request.user, query=query, decision=decision)
    return render(request, "listings/viewings/list.html", {
        "page": Paginator(apartments, 18).get_page(request.GET.get("page")), "query": query,
        "decision": decision, "decisions": ViewingApartment.Decision.choices,
    })


@login_required
@require_http_methods(["GET", "POST"])
def viewing_create(request):
    reference = request.POST.get("seed_listing", "") if request.method == "POST" else request.GET.get("reference", "")
    reference_form = ListingReferenceForm({"reference": reference}) if reference else ListingReferenceForm()
    listing = None
    if reference and reference_form.is_valid():
        try:
            listing = _listing(request, reference_form.cleaned_data["reference"])
        except ValidationError as exc:
            reference_form.add_error("reference", exc)
    form = ApartmentForm(request.POST if request.method == "POST" else None,
                         initial=apartment_initial(listing) if listing else None)
    if request.method == "POST" and form.is_valid():
        if reference and not listing:
            form.add_error(None, "Не удалось привязать объявление. Проверьте его ID.")
        else:
            try:
                apartment = save_apartment(request.user, form.cleaned_data, listing=listing)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Квартира добавлена. Теперь можно записать впечатления и загрузить фотографии.")
                return redirect("viewing_detail", pk=apartment.pk)
    return render(request, "listings/viewings/apartment_form.html", {
        "form": form, "reference_form": reference_form, "listing": listing,
        "linked": linked_apartments(request.user, listing) if listing else [],
    }, status=400 if request.method == "POST" else 200)


@login_required
@require_http_methods(["GET", "POST"])
def viewing_edit(request, pk):
    apartment = _owned(request, pk)
    form = ApartmentForm(request.POST if request.method == "POST" else None, instance=apartment)
    if request.method == "POST" and form.is_valid():
        try:
            save_apartment(request.user, form.cleaned_data, apartment_id=pk)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Сведения сохранены.")
            return redirect("viewing_detail", pk=pk)
    return render(request, "listings/viewings/apartment_form.html", {"form": form, "apartment": apartment},
                  status=400 if request.method == "POST" else 200)


@login_required
@require_http_methods(["GET", "POST"])
def viewing_detail(request, pk):
    apartment = _owned(request, pk)
    entry_form = EntryForm()
    reference_form = ListingReferenceForm(request.GET if "reference" in request.GET else None)
    listing = None
    error = ""
    if request.method == "GET" and reference_form.is_bound and reference_form.is_valid():
        try:
            listing = _listing(request, reference_form.cleaned_data["reference"])
        except ValidationError as exc:
            reference_form.add_error("reference", exc)
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "entry":
                entry_form = EntryForm(request.POST, request.FILES)
                if entry_form.is_valid():
                    save_viewing_entry(request.user, pk, entry_form.cleaned_data, entry_form.prepared_photos)
                    messages.success(request, "Запись сохранена.")
                    return redirect("viewing_detail", pk=pk)
            elif action in {"link", "unlink"}:
                reference_form = ListingReferenceForm(request.POST)
                if reference_form.is_valid():
                    listing = _listing(request, reference_form.cleaned_data["reference"])
                    change_listing_link(request.user, pk, listing, remove=action == "unlink")
                    messages.success(request, "Связь с объявлением обновлена.")
                    return redirect("viewing_detail", pk=pk)
            else:
                error = "Неизвестное действие."
        except ValidationError as exc:
            error = " ".join(exc.messages)
        except OSError:
            error = "Не удалось сохранить фотографии. Попробуйте ещё раз; запись не была изменена."
    entries = apartment.entries.prefetch_related("photos")
    return render(request, "listings/viewings/detail.html", {
        "apartment": apartment, "entry_form": entry_form, "reference_form": reference_form,
        "listing": listing, "linked": linked_apartments(request.user, listing) if listing else [],
        "links": apartment.listing_links.select_related("listing"), "photos": apartment.photos.all(),
        "entries": Paginator(entries, 20).get_page(request.GET.get("page")), "error": error,
    }, status=400 if request.method == "POST" else 200)


@login_required
@require_http_methods(["GET", "POST"])
def viewing_entry_edit(request, pk, entry_id):
    apartment = _owned(request, pk)
    entry = get_object_or_404(ViewingEntry, pk=entry_id, apartment=apartment)
    form = EntryForm(request.POST if request.method == "POST" else None, request.FILES or None, instance=entry)
    if request.method == "POST" and form.is_valid():
        try:
            save_viewing_entry(request.user, pk, form.cleaned_data, form.prepared_photos, entry_id=entry.pk)
        except ValidationError as exc:
            form.add_error(None, exc)
        except OSError:
            form.add_error(None, "Не удалось сохранить фотографии. Попробуйте ещё раз.")
        else:
            messages.success(request, "Запись обновлена.")
            return redirect("viewing_detail", pk=pk)
    return render(request, "listings/viewings/entry_form.html", {"form": form, "apartment": apartment},
                  status=400 if request.method == "POST" else 200)


@login_required
@require_GET
def viewing_photo(request, photo_id, variant):
    photo = get_object_or_404(ViewingPhoto, pk=photo_id, apartment__owner=request.user)
    if variant not in {"full", "thumb"}:
        raise Http404
    field = photo.thumbnail if variant == "thumb" else photo.image
    try:
        response = FileResponse(field.open("rb"), content_type="image/jpeg")
    except FileNotFoundError as exc:
        raise Http404 from exc
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@require_POST
def viewing_photo_update(request, photo_id):
    photo = get_object_or_404(ViewingPhoto, pk=photo_id, apartment__owner=request.user)
    form = PhotoCaptionForm(request.POST)
    if form.is_valid():
        try:
            update_viewing_photo(request.user, photo.pk, action=request.POST.get("action"), caption=form.cleaned_data["caption"])
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, "Фотография обновлена.")
    else:
        messages.error(request, "Подпись должна быть не длиннее 200 символов.")
    return redirect("viewing_detail", pk=photo.apartment_id)
