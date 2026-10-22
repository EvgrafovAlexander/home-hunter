"""Правила включения и временные ограничения фоновых опросов."""

from django.conf import settings
from django.utils import timezone
from listings.models import Scan, SearchQuery, SourcePollingControl


def cian_polling_allowed(moment=None) -> bool:
    """Проверяет дневное окно CIAN, включая продолжительные циклы."""
    hour = timezone.localtime(moment or timezone.now()).hour
    return settings.CIAN_POLL_START_HOUR <= hour < settings.CIAN_POLL_END_HOUR


def default_polling_enabled(source: str, mode: str) -> bool:
    """Возвращает безопасный режим по умолчанию для источника и типа скана."""
    return not (source == SearchQuery.Source.AVITO and mode == Scan.Mode.FULL)


def polling_enabled(source: str, mode: str = Scan.Mode.FAST) -> bool:
    """Возвращает, разрешён ли режим опроса для источника."""
    control, _ = SourcePollingControl.objects.get_or_create(
        source=source, mode=mode, defaults={"enabled": default_polling_enabled(source, mode)},
    )
    return control.enabled
