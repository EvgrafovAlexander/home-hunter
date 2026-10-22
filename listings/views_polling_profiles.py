"""HTTP-обработчик назначения сетевых профилей и диагностики маршрутов."""

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect, render

from .models import NetworkRoute, PollingDiagnosticJob, PollingProfile, Scan, SearchQuery, SourcePollingProfile


@staff_member_required
def polling_profiles(request):
    """Назначить сетевой профиль и поставить диагностический запуск."""
    action = request.POST.get("action") if request.method == "POST" else ""
    if request.method == "POST" and action == "test_profile":
        source = request.POST.get("source", "")
        profile_id = request.POST.get("profile_id", "")
        profile = PollingProfile.objects.filter(
            pk=profile_id, source=source, mode="all", enabled=True, route__enabled=True,
        ).first()
        search = SearchQuery.objects.filter(source=source, enabled=True).order_by("pk").first()
        active_job = PollingDiagnosticJob.objects.filter(
            source=source, status__in=[PollingDiagnosticJob.Status.QUEUED, PollingDiagnosticJob.Status.RUNNING],
        ).first()
        if profile is None:
            messages.error(request, "Профиль не найден или отключён.")
        elif search is None:
            messages.error(request, "Для площадки нет включённого поискового запроса.")
        elif active_job:
            messages.warning(request, "Для этой площадки уже выполняется диагностический запуск.")
        else:
            PollingDiagnosticJob.objects.create(
                source=source, search_query=search, profile=profile, mode=Scan.Mode.FAST,
            )
            messages.success(request, f"Тест профиля «{profile.name}» поставлен в очередь.")
        return redirect("polling_profiles")

    if request.method == "POST" and action in {"assign_profile", "assign_source_profile"}:
        source = request.POST.get("source", "")
        profile_id = request.POST.get("profile_id", "")
        profile = PollingProfile.objects.filter(
            pk=profile_id, source=source, mode="all", enabled=True, route__enabled=True,
        ).first()
        if profile is None:
            messages.error(request, "Профиль не найден или отключён.")
        else:
            SourcePollingProfile.objects.update_or_create(
                source=source, defaults={"profile": profile, "enabled": True},
            )
            PollingProfile.objects.filter(source=source, mode="all").update(is_default=False)
            profile.is_default = True
            profile.save(update_fields=("is_default", "updated_at"))
            messages.success(request, f"Профиль «{profile.name}» назначен для площадки {source}.")
        return redirect("polling_profiles")

    profiles = list(PollingProfile.objects.select_related("route").filter(enabled=True).order_by("source", "mode", "name"))
    assignments = {
        assignment.source: assignment
        for assignment in SourcePollingProfile.objects.select_related("profile", "profile__route").filter(enabled=True)
    }
    diagnostics = {}
    for job in PollingDiagnosticJob.objects.select_related("profile", "scan").filter(
        source__in=[source for source, _ in SearchQuery.Source.choices],
    )[:100]:
        diagnostics.setdefault(job.source, job)
    grouped = []
    for source, source_label in SearchQuery.Source.choices:
        options = [item for item in profiles if item.source == source and item.mode == "all"]
        if options:
            assignment = assignments.get(source)
            selected = assignment.profile if assignment and assignment.profile in options else None
            grouped.append({
                "source": source, "source_label": source_label, "profiles": options,
                "selected": selected or next((item for item in options if item.is_default), options[0]),
                "diagnostic": diagnostics.get(source),
            })
    return render(request, "listings/polling_profiles.html", {
        "routes": NetworkRoute.objects.prefetch_related("polling_profiles").order_by("name"),
        "profile_groups": grouped,
        "has_active_diagnostic_jobs": any(
            job.status in {PollingDiagnosticJob.Status.QUEUED, PollingDiagnosticJob.Status.RUNNING}
            for job in diagnostics.values()
        ),
    })
