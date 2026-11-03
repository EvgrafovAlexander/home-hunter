"""Полный backup сохраняет фото и БД согласованно и проверяет архив до распаковки."""

from io import BytesIO, StringIO
import json
from pathlib import Path
import tarfile
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse

from listings.models import ViewingPhoto
from listings.services.database_backup import BackupError, UnlinkingFile
from listings.services.project_backup import create_project_backup, verify_project_backup
from listings.services.viewing_photos import viewing_media_lock
from listings.tests.test_viewings import owner, apartment, saved_photo

pytestmark = pytest.mark.django_db


@pytest.fixture
def database_dump(tmp_path):
    def create():
        path = tmp_path / "test.dump"
        path.write_bytes(b"PGDMP-test-database")
        return UnlinkingFile(path.open("rb"), str(path)), "test.dump", path.stat().st_size
    with patch("listings.services.project_backup.create_database_backup", side_effect=create) as mocked:
        yield mocked


def test_backup_round_trip_includes_all_files_and_cleans_temp(owner, apartment, settings, tmp_path, database_dump):
    photo = saved_photo(owner, apartment)
    file_obj, filename, size = create_project_backup()
    archive = tmp_path / filename
    archive.write_bytes(file_obj.read())
    server_temp = Path(file_obj.name)
    file_obj.close()
    assert not server_temp.exists()
    assert not (tmp_path / "test.dump").exists()
    assert archive.stat().st_size == size
    manifest = verify_project_backup(archive, extract_to=tmp_path / "restored")
    assert len(manifest["files"]) == 3
    assert (tmp_path / "restored/database.dump").read_bytes() == b"PGDMP-test-database"
    for field in (photo.image, photo.thumbnail):
        assert (tmp_path / "restored/media" / field.name).read_bytes() == (settings.MEDIA_ROOT / field.name).read_bytes()
    assert not (tmp_path / "restored/media/.viewings.lock").exists()
    call_command("verify_project_backup", str(archive), stdout=StringIO())
    with pytest.raises(BackupError, match="пустой"):
        verify_project_backup(archive, extract_to=tmp_path / "restored")


def test_missing_photo_and_symlink_fail_backup(owner, apartment, settings, database_dump):
    photo = saved_photo(owner, apartment)
    photo.image.storage.delete(photo.image.name)
    with pytest.raises(BackupError, match="Не найдены"):
        create_project_backup()
    ViewingPhoto.objects.all().delete()
    (settings.MEDIA_ROOT / "link").symlink_to("/etc/hosts")
    with pytest.raises(BackupError, match="символьная"):
        create_project_backup()


def test_full_backup_does_not_start_while_photos_are_changing(owner, database_dump):
    with viewing_media_lock():
        with pytest.raises(BackupError, match="копируются"):
            create_project_backup()
    database_dump.assert_not_called()


def test_bad_archive_cannot_extract_outside_destination(tmp_path):
    path = tmp_path / "unsafe.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        data = b"bad"
        info = tarfile.TarInfo("../escape")
        info.size = len(data)
        archive.addfile(info, BytesIO(data))
    with pytest.raises(BackupError):
        verify_project_backup(path, extract_to=tmp_path / "restored")
    assert not (tmp_path / "escape").exists()
    with pytest.raises(CommandError):
        call_command("verify_project_backup", str(path), stdout=StringIO())


def test_checksum_mismatch_is_detected(owner, tmp_path, database_dump):
    file_obj, _, _ = create_project_backup()
    with tarfile.open(fileobj=file_obj, mode="r:gz") as source:
        contents = {member.name: source.extractfile(member).read() for member in source.getmembers()}
    file_obj.close()
    contents["database.dump"] = b"X" * len(contents["database.dump"])
    target = tmp_path / "corrupt.tar.gz"
    with tarfile.open(target, "w:gz") as archive:
        for name, data in contents.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, BytesIO(data))
    with pytest.raises(BackupError, match="сумма"):
        verify_project_backup(target)


def test_backup_download_is_staff_only_and_defaults_to_full(client, owner, database_dump):
    url = reverse("database_backup")
    client.force_login(owner)
    assert client.get(url).status_code == 302
    assert client.post(url).status_code == 302
    owner.is_staff = True
    owner.save()
    assert "Скачать полный backup" in client.get(url).content.decode()
    response = client.post(url)
    assert response.status_code == 200
    assert ".tar.gz" in response["Content-Disposition"]
    assert response["Cache-Control"] == "private, no-store"
    assert b"".join(response.streaming_content).startswith(b"\x1f\x8b")
