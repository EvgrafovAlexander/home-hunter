"""Личные просмотры: доступ, связи, повторные посещения и безопасные фото."""

from io import BytesIO
from unittest.mock import patch

from PIL import Image
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from listings.forms_viewings import ApartmentForm, EntryForm
from listings.models import Listing, ViewingApartment, ViewingEntry, ViewingPhoto, ViewingTelegramPublication
from listings.services.viewing_photos import prepare_viewing_photos, viewing_media_lock
from listings.services.viewing_telegram import _post_text
from listings.services.viewings import (
    apartment_initial, apartment_queryset, change_listing_link, linked_apartments,
    resolve_viewing_listing, save_apartment, save_viewing_entry, update_viewing_photo,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return get_user_model().objects.create_user(username="viewer")


@pytest.fixture
def apartment(owner):
    return ViewingApartment.objects.create(owner=owner, title="Квартира у парка", address="Ленина, 10")


@pytest.fixture
def listing():
    return Listing.objects.create(source="cian", external_id="viewing", title="Квартира", address="Мира, 12",
                                  price=7_000_000, rooms=2, area=60, url="https://example.test/flat")


def photo_upload(name="photo.jpg", *, size=(200, 100), exif=False):
    output = BytesIO()
    picture = Image.new("RGB", size, "#427966")
    metadata = Image.Exif()
    if exif:
        metadata[274] = 6
        metadata[315] = "private metadata"
    picture.save(output, "JPEG", exif=metadata)
    return SimpleUploadedFile(name, output.getvalue(), content_type="image/jpeg")


def entry_data(**kwargs):
    return {"kind": "note", "status": "", "happened_at": None, "body": "Тихий двор", **kwargs}


def saved_photo(owner, apartment):
    entry = save_viewing_entry(owner, apartment.pk, entry_data(), prepare_viewing_photos([photo_upload()]))
    return entry.photos.get()


def test_create_link_prefill_and_manual_fields_do_not_follow_listing(owner, listing):
    initial = apartment_initial(listing)
    form = ApartmentForm({**initial, "decision": "thinking"})
    assert form.is_valid(), form.errors
    apartment = save_apartment(owner, form.cleaned_data, listing=listing)
    assert apartment.asking_price == listing.price
    assert linked_apartments(owner, listing).get() == apartment
    change_listing_link(owner, apartment.pk, listing)
    assert apartment.listing_links.count() == 1
    listing.price = 8_000_000
    listing.save()
    apartment.refresh_from_db()
    assert apartment.asking_price == 7_000_000
    change_listing_link(owner, apartment.pk, listing, remove=True)
    assert not apartment.listing_links.exists()
    assert apartment.address == "Мира, 12"


def test_apartment_statuses_include_viewing_progress_and_off_market():
    values = dict(ViewingApartment.Decision.choices)
    assert values[ViewingApartment.Decision.THINKING] == "Не просмотрена"
    assert values[ViewingApartment.Decision.CONTACT] == "Нужно связаться"
    assert values[ViewingApartment.Decision.VIEWING_PLANNED] == "Просмотр назначен"
    assert values[ViewingApartment.Decision.VIEWED] == "Просмотрена"
    assert values[ViewingApartment.Decision.OFF_MARKET] == "Снята с продажи"


def test_viewing_detail_queues_telegram_publication(owner, apartment, settings, client):
    settings.TG_VIEWINGS_CHANNEL_ID = "-1004489313379"
    client.force_login(owner)
    response = client.post(reverse("viewing_detail", args=[apartment.pk]), {"action": "publish_telegram"})
    assert response.status_code == 302
    publication = ViewingTelegramPublication.objects.get(apartment=apartment)
    assert publication.status == ViewingTelegramPublication.Status.PENDING
    assert publication.channel_id == settings.TG_VIEWINGS_CHANNEL_ID


def test_viewing_telegram_post_contains_filled_source_links(owner, apartment, settings):
    settings.PUBLIC_BASE_URL = "http://home.test"
    apartment.cian_url = "https://cian.example/flat?id=1"
    apartment.avito_url = "https://avito.example/flat"
    apartment.save(update_fields=("cian_url", "avito_url"))
    text = _post_text(apartment)
    assert 'href="https://cian.example/flat?id=1"' in text
    assert 'href="https://avito.example/flat"' in text
    assert "Домклик" not in text


@pytest.mark.parametrize("reference", ["999999", "abc", "0", "9" * 20, "https://evil.test/listings/1/", "/admin/", "１２３"])
def test_invalid_listing_references(reference):
    with pytest.raises(ValidationError):
        resolve_viewing_listing(reference, allowed_hosts={"home.test"})


def test_listing_id_and_local_url_resolve(listing):
    for reference in (str(listing.pk), f"/listings/{listing.pk}/", f"https://home.test/listings/{listing.pk}/"):
        assert resolve_viewing_listing(reference, allowed_hosts={"home.test"}) == listing


def test_apartment_conflict_and_archive(owner, apartment):
    form = ApartmentForm({"title": "Новое имя", "decision": "archived", "version": 1}, instance=apartment)
    assert form.is_valid(), form.errors
    save_apartment(owner, form.cleaned_data, apartment_id=apartment.pk)
    with pytest.raises(ValidationError, match="другой вкладке"):
        save_apartment(owner, form.cleaned_data, apartment_id=apartment.pk)
    assert not apartment_queryset(owner).exists()
    assert apartment_queryset(owner, decision="archived", query="Новое").count() == 1


def test_visits_require_time_and_notes_can_be_photo_only(owner, apartment):
    assert not EntryForm({"kind": "visit", "status": "planned"}).is_valid()
    note = EntryForm({"kind": "note", "body": ""})
    assert not note.is_valid()
    for status in ("planned", "completed", "cancelled"):
        form = EntryForm({"kind": "visit", "status": status, "happened_at": "2026-11-06T15:00", "body": "Повторный просмотр"})
        assert form.is_valid(), form.errors
        save_viewing_entry(owner, apartment.pk, form.cleaned_data, form.prepared_photos)
    assert apartment.entries.count() == 3
    form = EntryForm({"kind": "note"}, {"photos": [photo_upload()]})
    assert form.is_valid(), form.errors
    save_viewing_entry(owner, apartment.pk, form.cleaned_data, form.prepared_photos)
    assert apartment.entries.count() == 4


def test_photo_processing_orientation_metadata_and_size(owner):
    prepared = prepare_viewing_photos([photo_upload(size=(2400, 1200), exif=True)])
    with Image.open(BytesIO(prepared[0][0])) as full:
        assert full.size == (960, 1920)
        assert not full.getexif()
        assert full.format == "JPEG"
    with Image.open(BytesIO(prepared[0][1])) as thumb:
        assert thumb.size == (240, 480)


def test_photo_validation_rejects_bad_batch_and_oversized_files(owner):
    with pytest.raises(ValidationError):
        prepare_viewing_photos([photo_upload(), SimpleUploadedFile("fake.jpg", b"not an image")])
    with pytest.raises(ValidationError, match="12"):
        prepare_viewing_photos([photo_upload() for _ in range(13)])
    upload = photo_upload()
    upload.size = 11 * 1024 * 1024
    with pytest.raises(ValidationError, match="10 МБ"):
        prepare_viewing_photos([upload])


def test_file_cleanup_on_database_failure(owner, apartment, settings):
    with patch.object(ViewingPhoto, "save", side_effect=RuntimeError("database failure")):
        with pytest.raises(RuntimeError):
            save_viewing_entry(owner, apartment.pk, entry_data(), prepare_viewing_photos([photo_upload()]))
    assert not apartment.entries.exists()
    assert not list(settings.MEDIA_ROOT.rglob("*.jpg"))


def test_photo_cover_caption_and_deletion(owner, apartment, django_capture_on_commit_callbacks):
    first = saved_photo(owner, apartment)
    second = saved_photo(owner, apartment)
    apartment.refresh_from_db()
    assert apartment.cover_photo_id == first.pk
    update_viewing_photo(owner, second.pk, action="cover")
    update_viewing_photo(owner, second.pk, action="caption", caption="Вид из окна")
    second.refresh_from_db()
    assert second.caption == "Вид из окна"
    with django_capture_on_commit_callbacks(execute=True):
        update_viewing_photo(owner, second.pk, action="delete")
    apartment.refresh_from_db()
    assert apartment.cover_photo_id == first.pk
    assert not second.image.storage.exists(second.image.name)
    assert not second.thumbnail.storage.exists(second.thumbnail.name)


def test_backup_lock_blocks_photo_writes(owner, apartment):
    with viewing_media_lock(exclusive=True):
        with pytest.raises(ValidationError, match="копируются"):
            save_viewing_entry(owner, apartment.pk, entry_data(), [])
    assert not apartment.entries.exists()


def test_owner_isolation_every_route(client, owner, apartment, listing):
    photo = saved_photo(owner, apartment)
    stranger = get_user_model().objects.create_user(username="stranger", is_staff=True)
    client.force_login(stranger)
    urls = [reverse("viewing_detail", args=[apartment.pk]), reverse("viewing_edit", args=[apartment.pk]),
            reverse("viewing_entry_edit", args=[apartment.pk, photo.entry_id]),
            reverse("viewing_photo", args=[photo.pk, "full"]), reverse("viewing_photo", args=[photo.pk, "thumb"])]
    for url in urls:
        assert client.get(url).status_code == 404
    for url, data in ((urls[0], {"action": "entry", "kind": "note", "body": "unauthorized"}),
                      (urls[1], {"title": "changed", "version": 1, "decision": "thinking"}),
                      (urls[2], {"kind": "note", "body": "changed", "version": 1}),
                      (urls[0], {"action": "link", "reference": listing.pk}),
                      (reverse("viewing_photo_update", args=[photo.pk]), {"action": "delete"})):
        assert client.post(url, data).status_code == 404
    assert not linked_apartments(stranger, listing).exists()
    assert apartment.title not in client.get(reverse("viewing_list")).content.decode()
    client.logout()
    for url in urls:
        assert client.get(url).status_code == 302


def test_full_user_flow_and_private_photo_response(client, owner, listing):
    client.force_login(owner)
    response = client.get(reverse("viewing_create"), {"reference": listing.pk})
    assert response.context["form"].initial["asking_price"] == 7_000_000
    response = client.post(reverse("viewing_create"), {"title": "Мой вариант", "decision": "thinking", "seed_listing": listing.pk})
    apartment = ViewingApartment.objects.get(owner=owner)
    assert response.status_code == 302
    detail = reverse("viewing_detail", args=[apartment.pk])
    response = client.post(detail, {"action": "entry", "kind": "note", "body": "<script>alert(1)</script>", "photos": [photo_upload()]})
    assert response.status_code == 302
    html = client.get(detail).content.decode()
    assert "&lt;script&gt;" in html
    photo = apartment.photos.get()
    response = client.get(reverse("viewing_photo", args=[photo.pk, "thumb"]))
    assert response.status_code == 200
    assert response["Content-Type"] == "image/jpeg"
    assert response["Cache-Control"] == "private, no-store"
    assert b"".join(response.streaming_content).startswith(b"\xff\xd8")
    assert client.get(reverse("viewing_photo_update", args=[photo.pk])).status_code == 405
    response = client.get(reverse("viewing_list"))
    assert "Мой вариант" in response.content.decode()
    entry = photo.entry
    edit = reverse("viewing_entry_edit", args=[apartment.pk, entry.pk])
    assert client.post(edit, {"kind": "note", "body": "Новое впечатление", "version": 1}).status_code == 302
    assert client.post(edit, {"kind": "note", "body": "Старая форма", "version": 1}).status_code == 400


def test_invalid_upload_does_not_save_entry(client, owner, apartment):
    client.force_login(owner)
    response = client.post(reverse("viewing_detail", args=[apartment.pk]), {
        "action": "entry", "kind": "note", "body": "Заметка", "photos": SimpleUploadedFile("bad.jpg", b"oops"),
    })
    assert response.status_code == 400
    assert not apartment.entries.exists()


def test_navigation_moves_shortlist_into_more(client, owner):
    owner.is_staff = True
    owner.save()
    client.force_login(owner)
    html = client.get(reverse("viewing_list")).content.decode()
    primary, menu = html.split('<div class="nav-menu">', 1)
    assert "Мои просмотры" in primary
    assert "Лучшие варианты" not in primary
    assert "Лучшие варианты" in menu


def test_create_apartment_with_photos(client, owner, listing):
    client.force_login(owner)
    response = client.get(reverse("viewing_create"))
    assert b'multipart/form-data' in response.content
    assert b'name="photos"' in response.content
    response = client.post(reverse("viewing_create"), {
        "title": "С фотографиями", "decision": "thinking", "seed_listing": listing.pk,
        "photos": [photo_upload(), photo_upload("second.jpg")],
    })
    apartment = ViewingApartment.objects.get(owner=owner)
    assert response.status_code == 302
    assert apartment.photos.count() == 2
    assert apartment.cover_photo_id == apartment.photos.order_by("created_at").first().pk
    assert apartment.listing_links.get().listing == listing
    assert apartment.entries.get().photos.count() == 2
    response = client.get(reverse("viewing_photo", args=[apartment.cover_photo_id, "full"]))
    assert response.status_code == 200
    response.close()


def test_create_rejects_invalid_photo_without_partial_card(client, owner, listing, settings):
    client.force_login(owner)
    response = client.post(reverse("viewing_create"), {
        "title": "Ошибка фото", "decision": "thinking", "seed_listing": listing.pk,
        "photos": [photo_upload(), SimpleUploadedFile("bad.jpg", b"broken")],
    })
    assert response.status_code == 400
    assert "photos" in response.context["form"].errors
    assert not ViewingApartment.objects.exists()
    assert not list(settings.MEDIA_ROOT.rglob("*.jpg"))


def test_create_photo_storage_failure_rolls_back_everything(client, owner, listing, settings):
    client.force_login(owner)
    storage = ViewingPhoto._meta.get_field("image").storage
    original_save = storage.save
    calls = 0

    def fail_second_file(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("storage unavailable")
        return original_save(*args, **kwargs)

    with patch.object(storage, "save", side_effect=fail_second_file):
        response = client.post(reverse("viewing_create"), {
            "title": "Ошибка хранилища", "decision": "thinking", "seed_listing": listing.pk,
            "photos": [photo_upload()],
        })
    assert response.status_code == 400
    assert "Карточка не создана" in response.content.decode()
    assert not ViewingApartment.objects.exists()
    assert not ViewingEntry.objects.exists()
    assert not ViewingPhoto.objects.exists()
    assert not listing.viewing_links.exists()
    assert not list(settings.MEDIA_ROOT.rglob("*.jpg"))


def test_description_and_external_links_create_edit_and_escape(client, owner):
    client.force_login(owner)
    data = {"title": "Своя карточка", "decision": "thinking", "description": "<script>bad</script>\nВид во двор",
            "cian_url": "https://ufa.cian.ru/sale/flat/332349477/", "avito_url": "https://www.avito.ru/ufa/kvartiry/123",
            "domclick_url": "https://ufa.domclick.ru/card/sale__flat__123"}
    assert client.post(reverse("viewing_create"), data).status_code == 302
    apartment = ViewingApartment.objects.get(owner=owner)
    assert apartment.description == data["description"]
    assert apartment.cian_url == data["cian_url"]
    assert not apartment.listing_links.exists()
    html = client.get(reverse("viewing_detail", args=[apartment.pk])).content.decode()
    assert "&lt;script&gt;bad&lt;/script&gt;" in html
    assert '<script>bad</script>' not in html
    assert all(f'href="{data[name]}"' in html for name in ("cian_url", "avito_url", "domclick_url"))
    assert 'rel="noopener noreferrer"' in html
    data.update(version=apartment.version, description="Обновлённое описание", cian_url="", avito_url="", domclick_url="")
    assert client.post(reverse("viewing_edit", args=[apartment.pk]), data).status_code == 302
    apartment.refresh_from_db()
    assert apartment.description == "Обновлённое описание"
    assert not apartment.cian_url and not apartment.avito_url and not apartment.domclick_url


@pytest.mark.parametrize("field,value", [
    ("cian_url", "https://cian.ru.evil.test/123"), ("cian_url", "https://evilcian.ru/123"),
    ("avito_url", "https://cian.ru/123"), ("domclick_url", "javascript:alert(1)"),
    ("avito_url", "ftp://avito.ru/123"), ("cian_url", "https://user:secret@cian.ru/123"),
])
def test_rejects_wrong_or_unsafe_source_links(field, value):
    form = ApartmentForm({"title": "Квартира", "decision": "thinking", field: value})
    assert not form.is_valid()
    assert field in form.errors


def test_comments_and_visits_have_separate_lists_and_errors(client, owner, apartment):
    client.force_login(owner)
    url = reverse("viewing_detail", args=[apartment.pk])
    for index in range(21):
        ViewingEntry.objects.create(apartment=apartment, kind="note", body=f"Заметка {index}")
    visit = ViewingEntry.objects.create(apartment=apartment, kind="visit", status="planned", body="Просмотр",
                                       happened_at=timezone.now())
    response = client.get(url, {"comments_page": 2})
    assert response.context["comments"].paginator.count == 21
    assert len(response.context["comments"]) == 1
    assert list(response.context["entries"]) == [visit]
    assert all(entry.kind == "note" for entry in response.context["comments"])
    response = client.post(url, {"action": "entry", "kind": "note", "body": ""})
    assert response.status_code == 400
    assert "body" in response.context["comment_form"].errors
    assert not response.context["entry_form"].is_bound
    response = client.post(url, {"action": "entry", "kind": "visit", "status": "planned"})
    assert response.status_code == 400
    assert "happened_at" in response.context["entry_form"].errors
    assert not response.context["comment_form"].is_bound


def test_edit_adds_photos_and_preserves_cover(client, owner, apartment):
    original = saved_photo(owner, apartment)
    client.force_login(owner)
    url = reverse("viewing_edit", args=[apartment.pk])
    response = client.get(url)
    assert b'name="photos"' in response.content
    assert b'multipart/form-data' in response.content
    data = {"title": "Обновлённая карточка", "decision": "thinking", "version": apartment.version}
    assert client.post(url, {**data, "photos": [photo_upload()]}).status_code == 302
    apartment.refresh_from_db()
    assert apartment.title == data["title"]
    assert apartment.photos.count() == 2
    assert apartment.cover_photo_id == original.pk
    assert original.image.storage.exists(original.image.name)
    assert client.post(url, {**data, "photos": [photo_upload()]}).status_code == 400
    assert apartment.photos.count() == 2


def test_edit_photo_failure_preserves_card_and_existing_files(client, owner, apartment, settings):
    original = saved_photo(owner, apartment)
    apartment.refresh_from_db()
    title, version = apartment.title, apartment.version
    before_files = set(settings.MEDIA_ROOT.rglob("*.jpg"))
    client.force_login(owner)
    with patch.object(ViewingPhoto, "save", side_effect=OSError("storage failure")):
        response = client.post(reverse("viewing_edit", args=[apartment.pk]), {
            "title": "Не должно сохраниться", "decision": "thinking", "version": version, "photos": [photo_upload()],
            "delete_photos": [str(original.pk)],
        })
    assert response.status_code == 400
    apartment.refresh_from_db()
    assert (apartment.title, apartment.version) == (title, version)
    assert apartment.photos.get().pk == original.pk
    assert set(settings.MEDIA_ROOT.rglob("*.jpg")) == before_files


def test_edit_removes_selected_photo_after_commit(client, owner, apartment, django_capture_on_commit_callbacks):
    first = saved_photo(owner, apartment)
    second = saved_photo(owner, apartment)
    client.force_login(owner)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(reverse("viewing_edit", args=[apartment.pk]), {
            "title": apartment.title, "decision": "thinking", "version": apartment.version,
            "delete_photos": [str(first.pk)], "photos": [photo_upload()],
        })
        assert response.status_code == 302
        assert first.image.storage.exists(first.image.name)
    apartment.refresh_from_db()
    assert apartment.photos.count() == 2
    assert apartment.cover_photo_id == second.pk
    assert not first.image.storage.exists(first.image.name)
    assert second.image.storage.exists(second.image.name)


def test_delete_confirmation_and_owner_access(client, owner, apartment, listing, django_capture_on_commit_callbacks):
    photo = saved_photo(owner, apartment)
    change_listing_link(owner, apartment.pk, listing)
    url = reverse("viewing_delete", args=[apartment.pk])
    client.force_login(owner)
    response = client.get(url)
    assert response.status_code == 200
    assert "Да, удалить карточку" in response.content.decode()
    assert ViewingApartment.objects.filter(pk=apartment.pk).exists()
    assert client.post(url, {}).status_code == 400
    assert client.post(url, {"version": apartment.version + 1}).status_code == 400
    stranger = get_user_model().objects.create_user(username="delete-stranger")
    client.force_login(stranger)
    assert client.get(url).status_code == 404
    assert client.post(url, {"version": apartment.version}).status_code == 404
    client.force_login(owner)
    with django_capture_on_commit_callbacks(execute=True):
        assert client.post(url, {"version": apartment.version}).status_code == 302
        assert photo.image.storage.exists(photo.image.name)
    assert not ViewingApartment.objects.filter(pk=apartment.pk).exists()
    assert not ViewingEntry.objects.filter(apartment_id=apartment.pk).exists()
    assert not photo.image.storage.exists(photo.image.name)
    assert Listing.objects.filter(pk=listing.pk).exists()


def test_edit_cannot_delete_another_apartments_photo(client, owner, apartment):
    other = ViewingApartment.objects.create(owner=owner, title="Другая квартира")
    photo = saved_photo(owner, other)
    client.force_login(owner)
    response = client.post(reverse("viewing_edit", args=[apartment.pk]), {
        "title": "Подмена", "decision": "thinking", "version": apartment.version, "delete_photos": [str(photo.pk)],
    })
    assert response.status_code == 400
    assert "delete_photos" in response.context["form"].errors
    assert ViewingPhoto.objects.filter(pk=photo.pk).exists()
    apartment.refresh_from_db()
    assert apartment.title != "Подмена"
