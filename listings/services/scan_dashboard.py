"""Агрегирует operational-статистику сканеров и detail-опросов."""

from datetime import timedelta

from django.db.models import F

from listings.models import Scan


SCAN_STALE_AFTER = timedelta(hours=6)


def classify_scan_health(scans, enabled, now):
    """Классифицирует режим сканирования по последним запускам."""
    last_success = next((scan for scan in scans if scan.status == Scan.Status.SUCCESS), None)
    failed_in_a_row = 0
    for scan in scans:
        if scan.status != Scan.Status.FAILED:
            break
        failed_in_a_row += 1
    if not enabled:
        return "disabled", "Отключено", "Опросы отключены в интерфейсе.", last_success, failed_in_a_row
    if not scans:
        return "unknown", "Нет данных", "Опросы ещё не запускались.", last_success, failed_in_a_row
    if scans[0].status == Scan.Status.RUNNING:
        return "running", "Выполняется", "Сейчас идёт опрос площадки.", last_success, failed_in_a_row
    if failed_in_a_row >= 3:
        return "error", "Ошибка", f"{failed_in_a_row} ошибки подряд.", last_success, failed_in_a_row
    if scans[0].status == Scan.Status.FAILED:
        return "warning", "Внимание", "Последний опрос завершился ошибкой.", last_success, failed_in_a_row
    if not last_success or not last_success.finished_at or last_success.finished_at < now - SCAN_STALE_AFTER:
        return "warning", "Данные устарели", "Нет успешного опроса за последние 6 часов.", last_success, failed_in_a_row
    return "healthy", "В норме", "Последний опрос завершился успешно.", last_success, failed_in_a_row


def build_cian_detail_cycle(progress, active_ids, active_queryset, now):
    """Готовит контекст текущего CIAN detail-цикла для dashboard."""
    if not progress:
        return None
    active_set = set(active_ids)
    completed_ids = [pk for pk in progress.completed_listing_ids if pk in active_set]
    total = len(active_set)
    completed = min(len(completed_ids), total)
    elapsed_hours = max((now - progress.cycle_started_at).total_seconds() / 3600, 0)
    actual_rate = round(completed / elapsed_hours, 1) if completed and elapsed_hours >= 1 / 60 else None
    forecast_rate = actual_rate or 24
    eta = now + timedelta(hours=max(total - completed, 0) / forecast_rate)
    next_queue = list(active_queryset.exclude(pk__in=completed_ids).exclude(
        pk=progress.current_listing_id,
    ).select_related("cian_detail_state").order_by(
        F("cian_detail_state__last_checked_at").asc(nulls_first=True), "pk",
    )[:5])
    return {
        "total": total, "completed": completed, "remaining": max(total - completed, 0),
        "progress": min(100, round(100 * completed / total)) if total else 100,
        "started_at": progress.cycle_started_at, "current": progress.current_listing,
        "current_started_at": progress.current_started_at, "next_queue": next_queue,
        "actual_rate": actual_rate, "eta": eta, "last_completed_at": progress.last_completed_at,
    }
