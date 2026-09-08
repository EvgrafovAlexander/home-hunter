import hmac
import json
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from collectors.base import NormalizedListing
from listings.models import ManualDomclickJob, Scan, SearchQuery
from listings.services.persistence import process_listing


def _authorized(request) -> bool:
    token = request.headers.get("X-Home-Hunter-Agent-Token", "")
    return bool(settings.DOMCLICK_AGENT_TOKEN and hmac.compare_digest(token, settings.DOMCLICK_AGENT_TOKEN))


def _forbidden():
    return JsonResponse({"detail": "Invalid agent token"}, status=403)


@csrf_exempt
@require_POST
def next_domclick_job(request):
    if not _authorized(request):
        return _forbidden()
    with transaction.atomic():
        job = (ManualDomclickJob.objects.select_for_update(skip_locked=True)
               .filter(status=ManualDomclickJob.Status.QUEUED).select_related("search_query").first())
        if not job:
            return JsonResponse({"job": None})
        now = timezone.now()
        scan = Scan.objects.create(search_query=job.search_query, source=SearchQuery.Source.DOMCLICK,
                                   mode=Scan.Mode.FAST, started_at=now)
        job.status = ManualDomclickJob.Status.RUNNING
        job.scan, job.started_at = scan, now
        job.save(update_fields=["status", "scan", "started_at"])
    return JsonResponse({"job": {"id": job.pk, "url": job.search_query.url, "name": job.search_query.name}})


def _optional_int(value):
    return int(value) if value not in (None, "") else None


def _listing_from_payload(payload):
    try:
        area = Decimal(str(payload["area"])) if payload.get("area") not in (None, "") else None
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Invalid area") from exc
    required = ("external_id", "url", "title")
    if any(not isinstance(payload.get(field), str) or not payload[field].strip() for field in required):
        raise ValueError("Missing listing identity")
    return NormalizedListing(
        source=SearchQuery.Source.DOMCLICK, external_id=payload["external_id"][:100], url=payload["url"],
        title=payload["title"][:1000], price=_optional_int(payload.get("price")),
        price_per_sqm=_optional_int(payload.get("price_per_sqm")), rooms=_optional_int(payload.get("rooms")),
        area=area, floor=_optional_int(payload.get("floor")), floors_total=_optional_int(payload.get("floors_total")),
        address=payload.get("address"), district=payload.get("district"), description=payload.get("description"),
        published_text=payload.get("published_text"), image_url=payload.get("image_url"), raw_data={},
    )


@csrf_exempt
@require_POST
def complete_domclick_job(request, job_id: int):
    if not _authorized(request):
        return _forbidden()
    try:
        payload = json.loads(request.body)
    except (TypeError, json.JSONDecodeError):
        return JsonResponse({"detail": "Invalid JSON"}, status=400)
    job = ManualDomclickJob.objects.select_related("search_query", "scan").filter(pk=job_id).first()
    if not job or job.status != ManualDomclickJob.Status.RUNNING or not job.scan:
        return JsonResponse({"detail": "Unknown or finished job"}, status=409)
    now = timezone.now()
    if payload.get("status") == "failed":
        error = str(payload.get("error", "Agent failed"))[:800]
        job.status, job.error, job.finished_at = ManualDomclickJob.Status.FAILED, error, now
        job.save(update_fields=["status", "error", "finished_at"])
        job.scan.status, job.scan.error, job.scan.finished_at = Scan.Status.FAILED, error, now
        job.scan.save(update_fields=["status", "error", "finished_at"])
        return JsonResponse({"ok": True})
    listings = payload.get("listings")
    if payload.get("status") != "success" or not isinstance(listings, list) or len(listings) > 200:
        return JsonResponse({"detail": "Invalid result"}, status=400)
    try:
        saved = [process_listing(job.search_query, _listing_from_payload(item), now) for item in listings]
    except (TypeError, ValueError, KeyError) as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
    job.scan.pages_scanned = max(0, _optional_int(payload.get("pages_scanned")) or 0)
    job.scan.items_seen = max(len(listings), _optional_int(payload.get("items_seen")) or 0)
    job.scan.unique_items_seen = len(saved)
    job.scan.new_items = sum(result.created for result in saved)
    job.scan.updated_items = sum(result.updated for result in saved)
    job.scan.price_changes = sum(result.price_changed for result in saved)
    job.scan.status, job.scan.finished_at = Scan.Status.SUCCESS, now
    job.scan.save(update_fields=["pages_scanned", "items_seen", "unique_items_seen", "new_items", "updated_items", "price_changes", "status", "finished_at"])
    job.status, job.finished_at = ManualDomclickJob.Status.SUCCESS, now
    job.save(update_fields=["status", "finished_at"])
    return JsonResponse({"ok": True, "new_items": job.scan.new_items})
