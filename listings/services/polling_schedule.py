"""Плановые запуски из timer-файлов репозитория, без обращения к systemd сервера.

Поддерживается ограниченное подмножество OnCalendar; неизвестные правила
не заменяются приблизительным расписанием. Разрешения запуска проверяет polling.
"""

import logging
import re
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings

from listings.models import Scan, SearchQuery


logger = logging.getLogger(__name__)
DISPLAY_TIME_ZONE = ZoneInfo("Asia/Yekaterinburg")
TIMER_DIRECTORY = Path(settings.BASE_DIR) / "deploy" / "systemd"
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _values(expression, lower, upper):
    if expression == "*":
        return range(lower, upper + 1)
    match = re.fullmatch(r"(\d+)(?:(\.\.|/)(\d+))?", expression)
    if not match:
        raise ValueError("Unsupported calendar component")
    start = int(match[1])
    end = int(match[3]) if match[2] == ".." else upper if match[2] == "/" else start
    step = int(match[3]) if match[2] == "/" else 1
    if not lower <= start <= end <= upper or step < 1:
        raise ValueError("Invalid calendar range")
    return range(start, end + 1, step)


def _next_calendar(expression, now):
    parts = expression.split()
    weekday = None
    if parts and parts[0] in _WEEKDAYS:
        weekday = _WEEKDAYS.index(parts.pop(0))
    if len(parts) != 3:
        raise ValueError("Calendar must include date, time and timezone")
    date_expr, clock, zone = parts
    # Пока в проекте только зоны без сезонных переводов времени.
    if zone not in {"Europe/Moscow", "Asia/Yekaterinburg", "UTC"}:
        raise ValueError("Unsupported calendar timezone")
    local = now.astimezone(ZoneInfo(zone))
    date_parts = date_expr.split("-")
    clock_parts = clock.split(":")
    if len(date_parts) != 3 or date_parts[:2] != ["*", "*"] or len(clock_parts) != 3:
        raise ValueError("Unsupported calendar date/time")
    days = _values(date_parts[2], 1, 31)
    hours, minutes, seconds = [_values(v, 0, maximum) for v, maximum in zip(clock_parts, (23, 59, 59))]
    for offset in range(370):
        day = local.date() + timedelta(days=offset)
        if day.day not in days or (weekday is not None and day.weekday() != weekday):
            continue
        for hour in hours:
            for minute in minutes:
                for second in seconds:
                    candidate = datetime.combine(day, time(hour, minute, second), local.tzinfo)
                    if candidate > local:
                        return candidate
    raise ValueError("No calendar occurrence within search horizon")


def timer_schedule(path: Path, now):
    """Возвращает ближайший календарный слот и задержку; неизвестный формат вызывает ValueError."""
    if now.tzinfo is None:
        raise ValueError("An aware timestamp is required")
    section = ""
    rules = []
    delay = 0
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("["):
            section = line
            continue
        if section != "[Timer]":
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator:
            raise ValueError("Invalid timer directive")
        if key == "OnCalendar":
            if value:
                rules.append(value)
            else:
                rules.clear()
        elif key == "RandomizedDelaySec":
            match = re.fullmatch(r"(\d+)(s|sec|min)?", value)
            if not match:
                raise ValueError("Unsupported randomized delay")
            delay = int(match[1]) * (60 if match[2] == "min" else 1)
        elif key not in {"Unit", "Persistent"}:
            raise ValueError(f"Unsupported timer directive: {key}")
    if not rules:
        raise ValueError("No calendar rules")
    return min(_next_calendar(rule, now) for rule in rules), delay


def next_poll_runs(source: str, now):
    """Возвращает плановые fast/full слоты; неизвестное расписание обозначается None."""
    modes = {
        SearchQuery.Source.AVITO: (Scan.Mode.FAST, Scan.Mode.FULL),
        SearchQuery.Source.CIAN: (Scan.Mode.FAST, Scan.Mode.FULL),
        SearchQuery.Source.DOMCLICK: (Scan.Mode.FAST,),
    }.get(source, ())
    runs = []
    for mode in modes:
        path = TIMER_DIRECTORY / f"home-hunter-{source}-{mode}.timer"
        label = "Быстрый" if mode == Scan.Mode.FAST else "Полный"
        try:
            scheduled, seconds = timer_schedule(path, now)
            delay = (f"задержка до {seconds // 60} мин." if seconds % 60 == 0
                     else f"задержка до {seconds} сек.") if seconds else ""
            runs.append((mode, label, scheduled.astimezone(DISPLAY_TIME_ZONE), delay))
        except (OSError, ValueError) as exc:
            logger.warning("Cannot calculate schedule from %s: %s", path.name, exc)
            runs.append((mode, label, None, "Расписание неизвестно"))
    return runs


def polling_mode_locked(source: str, mode: str) -> bool:
    """Определить, запрещено ли ручное включение режима опроса."""
    return source == SearchQuery.Source.AVITO and mode == Scan.Mode.FULL
