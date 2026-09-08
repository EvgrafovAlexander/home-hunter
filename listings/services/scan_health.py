import logging
from dataclasses import dataclass
from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone

from listings.models import Scan, SearchQuery, SourceHealthAlert
from listings.services.polling import polling_enabled

logger = logging.getLogger(__name__)
STALE_AFTER = timedelta(hours=6)
PROBLEM_STATES = {"failed", "failed_3", "stale"}


@dataclass(frozen=True)
class SourceHealth:
    state: str
    label: str
    detail: str


def source_health(source: str) -> SourceHealth:
    scans = list(Scan.objects.filter(source=source).order_by("-started_at", "-id")[:20])
    if not scans:
        return SourceHealth("unknown", "Нет данных", "Опросы ещё не запускались")
    if scans[0].status == Scan.Status.RUNNING:
        return SourceHealth("running", "Выполняется", "Сейчас идёт опрос площадки")
    failures = 0
    for scan in scans:
        if scan.status != Scan.Status.FAILED:
            break
        failures += 1
    if failures >= 3:
        return SourceHealth("failed_3", "Ошибка", f"{failures} ошибки подряд")
    if failures:
        return SourceHealth("failed", "Внимание", "Последний опрос завершился ошибкой")
    last_success = next((scan for scan in scans if scan.status == Scan.Status.SUCCESS), None)
    if not last_success or not last_success.finished_at or last_success.finished_at < timezone.now() - STALE_AFTER:
        return SourceHealth("stale", "Данные устарели", "Нет успешного опроса за последние 6 часов")
    return SourceHealth("healthy", "В норме", "Последний опрос завершился успешно")


def send_telegram_message(text: str) -> bool:
    if not settings.TG_BOT_TOKEN or not settings.TG_CHAT_ID:
        return False
    proxies = {"http": settings.TELEGRAM_PROXY_URL, "https": settings.TELEGRAM_PROXY_URL} \
        if settings.TELEGRAM_PROXY_URL else None
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{settings.TG_BOT_TOKEN}/sendMessage",
            data={"chat_id": settings.TG_CHAT_ID, "text": text}, proxies=proxies, timeout=20,
        )
        response.raise_for_status()
        return True
    except requests.RequestException:
        logger.exception("Telegram notification failed")
        return False


def check_source_health(source: str) -> SourceHealth:
    if not polling_enabled(source, Scan.Mode.FAST):
        return SourceHealth("disabled", "Отключено", "Опросы отключены в интерфейсе")
    health = source_health(source)
    if health.state == "running":
        return health
    alert, created = SourceHealthAlert.objects.get_or_create(source=source, defaults={"state": health.state})
    previous_state = None if created else alert.state
    if not created and previous_state != health.state:
        alert.state = health.state
        alert.save(update_fields=["state", "updated_at"])
    should_notify = health.state in PROBLEM_STATES and (created or previous_state != health.state)
    if health.state == "healthy" and previous_state in PROBLEM_STATES:
        should_notify = True
    if should_notify:
        label = SearchQuery.Source(source).label
        prefix = "✅ Восстановление" if health.state == "healthy" else "⚠️ Проблема"
        send_telegram_message(f"{prefix}: {label}\n{health.detail}")
    return health
