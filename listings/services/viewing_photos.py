"""Проверяет и уменьшает личные фото; не определяет доступ и не изменяет ORM."""

from contextlib import contextmanager
import fcntl
from io import BytesIO
from pathlib import Path
import warnings

from django.conf import settings

from django.core.exceptions import ValidationError
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BATCH = 12
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 32_000_000


def prepare_viewing_photos(uploads):
    """Проверяет пакет целиком и возвращает JPEG без EXIF и миниатюры без записи файлов."""
    if len(uploads) > MAX_BATCH:
        raise ValidationError("За один раз можно добавить до 12 фотографий.")
    prepared = []
    for upload in uploads:
        if upload.size > MAX_FILE_BYTES:
            raise ValidationError("Каждая фотография должна быть не больше 10 МБ.")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(upload) as source:
                    if source.format not in {"JPEG", "PNG", "WEBP"}:
                        raise ValidationError("Поддерживаются JPEG, PNG и WebP. HEIC нужно предварительно преобразовать.")
                    if source.width * source.height > MAX_PIXELS:
                        raise ValidationError("Разрешение фотографии не должно превышать 32 мегапикселя.")
                    source.load()
                    oriented = ImageOps.exif_transpose(source)
                    oriented.thumbnail((1920, 1920))
                    rgba = oriented.convert("RGBA")
                    picture = Image.new("RGB", rgba.size, "white")
                    picture.paste(rgba, mask=rgba.getchannel("A"))
                    full = BytesIO()
                    picture.save(full, format="JPEG", quality=85, optimize=True)
                    picture.thumbnail((480, 480))
                    thumb = BytesIO()
                    picture.save(thumb, format="JPEG", quality=80, optimize=True)
                    prepared.append((full.getvalue(), thumb.getvalue()))
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
                Image.DecompressionBombWarning) as exc:
            raise ValidationError("Не удалось прочитать фотографию. Выберите исправный JPEG, PNG или WebP.") from exc
        finally:
            upload.seek(0)
    return prepared


@contextmanager
def viewing_media_lock(*, exclusive=False):
    """Согласует изменения фото и полный backup через lock в общем media volume."""
    root = Path(settings.MEDIA_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".viewings.lock").open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValidationError("Фотографии сейчас сохраняются или копируются. Повторите через минуту.") from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
