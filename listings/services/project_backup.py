"""Полный архив PostgreSQL и личных фото с проверяемым manifest, без HTTP."""

import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import tarfile
import tempfile

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from listings.models import ViewingPhoto
from .database_backup import BackupError, UnlinkingFile, create_database_backup
from .viewing_photos import viewing_media_lock


def _checksum(file_obj):
    digest = hashlib.sha256()
    for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def create_project_backup():
    """Создаёт один архив БД и media; изменения фото блокируются до завершения копирования."""
    fd, path = tempfile.mkstemp(prefix="home_hunter_full_", suffix=".tar.gz")
    os.close(fd)
    database_file = None
    try:
        with viewing_media_lock(exclusive=True):
            database_file, _, _ = create_database_backup()
            root = Path(settings.MEDIA_ROOT).resolve()
            required = {name for pair in ViewingPhoto.objects.values_list("image", "thumbnail") for name in pair}
            if any(not name or not (root / name).is_file() for name in required):
                raise BackupError("Не найдены некоторые фотографии. Полный backup не создан: проверьте media volume.")
            files = [(Path(database_file.name), "database.dump")]
            for file_path in sorted(root.rglob("*")):
                if file_path.is_symlink():
                    raise BackupError("В каталоге фотографий обнаружена символьная ссылка. Проверьте media volume.")
                if file_path.is_file() and file_path.name != ".viewings.lock":
                    files.append((file_path, "media/" + file_path.relative_to(root).as_posix()))
            manifest = {"format": "home-hunter-full", "version": 1,
                        "created_at": timezone.now().isoformat(), "files": []}
            with tarfile.open(path, "w:gz", compresslevel=1) as archive:
                for file_path, archive_name in files:
                    with file_path.open("rb") as source:
                        checksum = _checksum(source)
                    manifest["files"].append({"path": archive_name, "size": file_path.stat().st_size, "sha256": checksum})
                    archive.add(file_path, arcname=archive_name, recursive=False)
                payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
                info = tarfile.TarInfo("manifest.json")
                info.size, info.mode = len(payload), 0o600
                archive.addfile(info, BytesIO(payload))
        filename = timezone.localtime().strftime("home_hunter_full_%Y-%m-%d_%H-%M-%S.tar.gz")
        return UnlinkingFile(open(path, "rb"), path), filename, os.path.getsize(path)
    except Exception as exc:
        Path(path).unlink(missing_ok=True)
        if isinstance(exc, BackupError):
            raise
        if isinstance(exc, ValidationError):
            raise BackupError(" ".join(exc.messages)) from exc
        raise BackupError("Не удалось создать полный backup. Проверьте доступ к фотографиям и свободное место.") from exc
    finally:
        if database_file is not None:
            database_file.close()


def verify_project_backup(path, *, extract_to=None):
    """Проверяет состав и SHA-256; извлекает только в пустой каталог без замены БД."""
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if len(set(names)) != len(names) or any(not member.isfile() for member in members):
            raise BackupError("Архив содержит повторяющиеся имена или неподдерживаемые записи.")
        if "manifest.json" not in names or archive.getmember("manifest.json").size > 10 * 1024 * 1024:
            raise BackupError("В архиве отсутствует корректный manifest.")
        with archive.extractfile("manifest.json") as file_obj:
            manifest = json.load(file_obj)
        if not isinstance(manifest, dict):
            raise BackupError("Некорректный manifest.")
        if manifest.get("format") != "home-hunter-full" or manifest.get("version") != 1:
            raise BackupError("Неизвестный формат полного backup.")
        records = manifest.get("files")
        if not isinstance(records, list) or any(not isinstance(record, dict) or
                not isinstance(record.get("path"), str) or not isinstance(record.get("size"), int) or
                not isinstance(record.get("sha256"), str) for record in records):
            raise BackupError("Некорректный список файлов в manifest.")
        expected = [record["path"] for record in records]
        if (len(set(expected)) != len(expected) or "database.dump" not in expected
                or set(names) != set(expected) | {"manifest.json"}):
            raise BackupError("Состав архива не соответствует manifest.")
        for record in records:
            name = record["path"]
            if name != "database.dump" and (not name.startswith("media/") or ".." in name.split("/") or "\\" in name):
                raise BackupError("Недопустимый путь в архиве.")
            if archive.getmember(name).size != record["size"]:
                raise BackupError("Размер файла не совпадает с manifest.")
            with archive.extractfile(name) as file_obj:
                if _checksum(file_obj) != record["sha256"]:
                    raise BackupError("Контрольная сумма файла не совпадает. Архив повреждён.")
        if extract_to:
            destination = Path(extract_to)
            if destination.is_symlink() or (destination.exists() and (not destination.is_dir() or any(destination.iterdir()))):
                raise BackupError("Для извлечения нужен новый или пустой каталог.")
            destination.mkdir(parents=True, exist_ok=True)
            archive.extractall(destination, filter="data")
    return manifest
