"""Личные Telegram-сводки завершённых полных опросов источников."""

from django.conf import settings
from django.utils import timezone

from listings.models import Scan
from listings.services.polling_schedule import next_poll_runs
from listings.services.scan_health import send_telegram_message


def send_full_scan_summary(source: str, started_at, *, command_error: str = "") -> None:
    """Отправляет итог полного batch-опроса только личным получателям Telegram."""
    scans = list(Scan.objects.filter(
        source=source, mode=Scan.Mode.FULL, started_at__gte=started_at,
    ).order_by("started_at", "id"))
    if command_error:
        overall = f"ошибка запуска: {command_error[:300]}"
    elif not scans:
        overall = "не выполнен: записи Scan не созданы"
    elif all(scan.status == Scan.Status.SUCCESS for scan in scans):
        overall = "успешно"
    else:
        overall = "завершён с ошибками"
    lines = [f"📊 Полный опрос {source.upper()}: {overall}"]
    for scan in scans:
        lines.append(
            f"• Scan {scan.pk}: {scan.status}; нов./обновл. {scan.new_items}/{scan.updated_items}; "
            f"страниц {scan.pages_scanned}"
            + (f"; ошибка: {scan.error[:240]}" if scan.error else "")
        )
    next_full = next((run for run in next_poll_runs(source, timezone.now()) if run[0] == Scan.Mode.FULL), None)
    if next_full:
        lines.append(f"Следующий полный опрос: {next_full[2].strftime('%d.%m.%Y %H:%M %Z')}")
    else:
        lines.append("Следующий полный опрос: расписание не определено")
    text = "\n".join(lines)
    for chat_id in getattr(settings, "TG_FULL_SCAN_CHAT_IDS", ()):
        send_telegram_message(text, chat_id=chat_id)
