"""HTTP-обработчик мониторинга запусков и состояния сборщиков."""

from math import ceil

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone

from .models import (
    CianDetailPollProgress,
    CianDetailPollState,
    CianFullScanCheckpoint,
    Listing,
    ListingSearchQuery,
    ManualDomclickJob,
    Scan,
    SearchQuery,
    SourcePollingControl,
)
from .services.polling import default_polling_enabled
from .services.scan_dashboard import build_cian_detail_cycle, classify_scan_health
from .services.scan_health import cian_detail_health
from .views import next_poll_runs, polling_mode_locked


@login_required
def scan_statistics(request):
    """Показать здоровье, историю и ближайшие запуски сборщиков."""
    if request.method == "POST":
        if request.POST.get("action") == "manual_domclick":
            active_job = ManualDomclickJob.objects.filter(
                status__in=[ManualDomclickJob.Status.QUEUED, ManualDomclickJob.Status.RUNNING],
            ).first()
            if not active_job:
                search = SearchQuery.objects.filter(source=SearchQuery.Source.DOMCLICK, enabled=True).first()
                if search:
                    ManualDomclickJob.objects.create(search_query=search)
            return redirect("scan_statistics")
        source = request.POST.get("source")
        mode = request.POST.get("mode")
        enabled = request.POST.get("enabled")
        if (source in SearchQuery.Source.values and mode in Scan.Mode.values and enabled in {"0", "1"}
                and not polling_mode_locked(source, mode)):
            SourcePollingControl.objects.update_or_create(
                source=source, mode=mode, defaults={"enabled": enabled == "1"},
            )
        return redirect("scan_statistics")

    sources = []
    now = timezone.now()
    controls = {(control.source, control.mode): control.enabled for control in SourcePollingControl.objects.all()}
    cian_checkpoints = {
        checkpoint.search_query_id: checkpoint
        for checkpoint in CianFullScanCheckpoint.objects.select_related("search_query")
    }
    cian_candidate_count = ListingSearchQuery.objects.filter(
        search_query__source=SearchQuery.Source.CIAN, missed_full_scans__gt=0,
    ).count()
    active_cian = Listing.objects.filter(source=SearchQuery.Source.CIAN, is_active=True)
    cian_active_ids = set(active_cian.values_list("pk", flat=True))
    cian_progress = CianDetailPollProgress.objects.select_related("current_listing").filter(source="cian").first()
    detail_health = cian_detail_health()
    detail_cycle = build_cian_detail_cycle(cian_progress, cian_active_ids, active_cian, now)
    for value, label in SearchQuery.Source.choices:
        recent_scans = list(Scan.objects.filter(source=value).select_related("search_query")
                            .order_by("-started_at", "-id")[:20])
        next_runs = [
            {"mode": mode, "label": run_label, "scheduled_at": scheduled_at, "delay": delay,
             "enabled": controls.get((value, mode), default_polling_enabled(value, mode)),
             "locked": polling_mode_locked(value, mode)}
            for mode, run_label, scheduled_at, delay in next_poll_runs(value, now)
        ]
        modes = []
        for run in next_runs:
            mode_scans = [scan for scan in recent_scans if scan.mode == run["mode"]]
            health, health_label, health_reason, last_success, failed_in_a_row = classify_scan_health(
                mode_scans, run["enabled"], now,
            )
            modes.append({
                **run, "scans": mode_scans[:3], "health": health, "health_label": health_label,
                "health_reason": health_reason, "last_success": last_success,
                "failed_in_a_row": failed_in_a_row,
            })
        source_health = next(
            (health for health in ("running", "error", "warning", "disabled", "healthy", "unknown")
             if any(mode["health"] == health for mode in modes)), "unknown",
        )
        full_cycle = None
        if value == SearchQuery.Source.CIAN:
            checkpoint = next((cian_checkpoints.get(scan.search_query_id) for scan in recent_scans
                               if scan.mode == Scan.Mode.FULL and cian_checkpoints.get(scan.search_query_id)), None)
            if checkpoint is None:
                checkpoint = next(iter(cian_checkpoints.values()), None)
            if checkpoint:
                last_full = next((scan for scan in recent_scans if scan.mode == Scan.Mode.FULL), None)
                per_page = max(1, (last_full.items_seen // last_full.pages_scanned)
                               if last_full and last_full.pages_scanned else 28)
                estimated_pages = ceil(checkpoint.expected_total / per_page) if checkpoint.expected_total else None
                full_cycle = {
                    "search_name": checkpoint.search_query.name, "started_at": checkpoint.cycle_started_at,
                    "next_page": checkpoint.next_page, "expected_total": checkpoint.expected_total,
                    "collected_count": len(checkpoint.external_ids), "total_changes": checkpoint.total_changes,
                    "estimated_pages": estimated_pages,
                    "progress": min(100, round(100 * len(checkpoint.external_ids) / checkpoint.expected_total))
                    if checkpoint.expected_total else 0,
                }
        sources.append({
            "value": value, "label": label, "modes": modes, "health": source_health,
            "icon": {"avito": "A", "cian": "⌖", "domclick": "⌂"}[value],
            "items_seen": sum(scan.items_seen or 0 for mode in modes for scan in mode["scans"]),
            "new_items": sum(scan.new_items or 0 for mode in modes for scan in mode["scans"]),
            "successful_runs": sum(scan.status == Scan.Status.SUCCESS for mode in modes for scan in mode["scans"]),
            "full_cycle": full_cycle, "candidate_count": cian_candidate_count if value == "cian" else 0,
            "detail_poll": ({
                "total": len(cian_active_ids),
                "checked": CianDetailPollState.objects.filter(listing__source=SearchQuery.Source.CIAN, last_checked_at__isnull=False).count(),
                "published": CianDetailPollState.objects.filter(listing__source=SearchQuery.Source.CIAN, status="published").count(),
                "errors": CianDetailPollState.objects.filter(listing__source=SearchQuery.Source.CIAN).exclude(last_error="").count(),
                "last": CianDetailPollState.objects.filter(listing__source=SearchQuery.Source.CIAN, last_checked_at__isnull=False).order_by("-last_checked_at").first(),
                "cycle": detail_cycle,
                "health": {"state": detail_health.state, "label": detail_health.label, "detail": detail_health.detail},
            } if value == "cian" else None),
        })
    manual_domclick_job = ManualDomclickJob.objects.select_related("scan").order_by("-id").first()
    return render(request, "listings/scan_statistics.html", {"sources": sources, "manual_domclick_job": manual_domclick_job})
