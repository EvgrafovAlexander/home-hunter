"""Расчёт ближайших запусков фоновых опросов для web и мониторинга."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from listings.models import Scan, SearchQuery


SCHEDULE_TIME_ZONE = ZoneInfo("Europe/Moscow")
DISPLAY_TIME_ZONE = ZoneInfo("Asia/Yekaterinburg")


def _next_at(now, hour: int, minute: int):
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(days=1)


def _next_hourly_at(now, minute: int):
    candidate = now.replace(minute=minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(hours=1)


def _next_avito_fast_at(now):
    """Найти ближайший двухчасовой слот быстрого опроса Avito."""
    candidates = []
    for day_offset in range(2):
        day = now.date() + timedelta(days=day_offset)
        for hour in range(8, 23, 2):
            candidates.append(now.replace(
                year=day.year,
                month=day.month,
                day=day.day,
                hour=hour,
                minute=15,
                second=0,
                microsecond=0,
            ))
    return next(candidate for candidate in candidates if candidate > now)


def _next_avito_full(now):
    for offset in range(4):
        day = now.date() + timedelta(days=offset)
        if (day.day - 1) % 3 == 0:
            candidate = timezone.make_aware(datetime.combine(day, time(3, 30)), now.tzinfo)
            if candidate > now:
                return candidate


def next_poll_runs(source: str, now):
    """Вернуть ближайшие запуски источника в часовом поясе Екатеринбурга."""
    now = now.astimezone(SCHEDULE_TIME_ZONE)
    if source == SearchQuery.Source.AVITO:
        runs = [
            (Scan.Mode.FAST, "Быстрый", _next_avito_fast_at(now), "в течение 10 мин."),
            (Scan.Mode.FULL, "Полный", _next_avito_full(now), ""),
        ]
    elif source == SearchQuery.Source.CIAN:
        runs = [
            (Scan.Mode.FAST, "Быстрый", _next_hourly_at(now, 0), "в течение 5 мин."),
            (Scan.Mode.FULL, "Полный", _next_at(now, 0, 10), "в течение 20 мин."),
        ]
    elif source == SearchQuery.Source.DOMCLICK:
        runs = [(Scan.Mode.FAST, "Быстрый", _next_hourly_at(now, 15), "")]
    else:
        return []
    return [
        (mode, label, scheduled_at.astimezone(DISPLAY_TIME_ZONE), delay)
        for mode, label, scheduled_at, delay in runs
    ]


def polling_mode_locked(source: str, mode: str) -> bool:
    """Определить, запрещено ли ручное включение режима опроса."""
    return source == SearchQuery.Source.AVITO and mode == Scan.Mode.FULL
