import json
import logging
from dataclasses import dataclass
from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone

from listings.models import CianDetailHealthAlert, CianDetailPollProgress, CianDetailPollState, Scan, SearchQuery, SourceHealthAlert
from listings.services.polling import polling_enabled

logger = logging.getLogger(__name__)
STALE_AFTER = timedelta(hours=6)
PROBLEM_STATES = {"failed", "failed_3", "stale"}
DETAIL_STALE_AFTER = timedelta(minutes=20)


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


def send_telegram_message(text: str, reply_markup=None) -> bool:
    if not settings.TG_BOT_TOKEN or not settings.TG_CHAT_ID:
        return False
    proxies = {"http": settings.TELEGRAM_PROXY_URL, "https": settings.TELEGRAM_PROXY_URL} \
        if settings.TELEGRAM_PROXY_URL else None
    try:
        data = {"chat_id": settings.TG_CHAT_ID, "text": text}
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        response = requests.post(
            f"https://api.telegram.org/bot{settings.TG_BOT_TOKEN}/sendMessage",
            data=data, proxies=proxies, timeout=20,
        )
        response.raise_for_status()
        return True
    except requests.RequestException:
        logger.exception("Telegram notification failed")
        return False


def send_telegram_photo(photo_url: str, caption: str, reply_markup=None) -> bool:
    if not settings.TG_BOT_TOKEN or not settings.TG_CHAT_ID or not photo_url:
        return False
    proxies = {"http": settings.TELEGRAM_PROXY_URL, "https": settings.TELEGRAM_PROXY_URL} \
        if settings.TELEGRAM_PROXY_URL else None
    try:
        data = {"chat_id": settings.TG_CHAT_ID, "photo": photo_url, "caption": caption}
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        response = requests.post(
            f"https://api.telegram.org/bot{settings.TG_BOT_TOKEN}/sendPhoto",
            data=data,
            proxies=proxies, timeout=20,
        )
        response.raise_for_status()
        return True
    except requests.RequestException:
        logger.warning("Telegram photo notification failed; falling back to text", exc_info=True)
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


def cian_detail_health() -> SourceHealth:
    """Return detail-worker health without persisting or notifying."""
    progress = CianDetailPollProgress.objects.filter(source="cian").first()
    latest_error = CianDetailPollState.objects.exclude(last_error="").order_by("-updated_at").first()
    now = timezone.now()
    if not progress:
        health = SourceHealth("unknown", "Нет данных", "Detail-опрос ещё не запускался")
    elif latest_error and latest_error.updated_at >= progress.updated_at:
        health = SourceHealth("error", "Ошибка", f"Последняя ошибка detail: {latest_error.last_error[:240]}")
    elif now - progress.updated_at > DETAIL_STALE_AFTER:
        health = SourceHealth("stale", "Опрос остановился", "Нет прогресса detail-опроса более 20 минут")
    else:
        health = SourceHealth("healthy", "В норме", "Detail-опрос обновлялся недавно")
    return health


def check_cian_detail_health() -> SourceHealth:
    """Detect a blocked/stopped CIAN detail worker independently of scan runs."""
    health = cian_detail_health()
    alert, created = CianDetailHealthAlert.objects.get_or_create(pk=1, defaults={"state": health.state})
    previous = None if created else alert.state
    if previous != health.state:
        alert.state = health.state
        alert.save(update_fields=("state", "updated_at"))
    if health.state in {"error", "stale"} and (created or previous != health.state):
        send_telegram_message(f"⚠️ CIAN detail: {health.label}\n{health.detail}")
    elif health.state == "healthy" and previous in {"error", "stale"}:
        send_telegram_message(f"✅ CIAN detail восстановлен\n{health.detail}")
    return health
