"""HTTP-обработчики выгрузки пользовательских оценок и рыночных данных."""

import csv

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse

from .models import ListingReview


@login_required
def review_export(request, format):
    """Выгрузить оценки пользователя в JSON или CSV."""
    reviews = (
        ListingReview.objects.filter(author=request.user)
        .select_related("listing")
        .prefetch_related("tags")
    )
    rows = [
        {
            "listing_id": review.listing_id,
            "url": review.listing.url,
            "title": review.listing.title,
            "source": review.listing.source,
            "price": review.listing.price,
            "rooms": review.listing.rooms,
            "area": str(review.listing.area or ""),
            "rating": review.rating,
            "decision": review.decision,
            "tags": [tag.code for tag in review.tags.all()],
            "comment": review.comment,
            "interest_reason": review.interest_reason,
            "deal_breaker": review.deal_breaker,
            "created_at": review.created_at.isoformat(),
            "updated_at": review.updated_at.isoformat(),
        }
        for review in reviews
    ]
    if format == "json":
        return JsonResponse(rows, safe=False, json_dumps_params={"ensure_ascii": False, "indent": 2})
    if format != "csv":
        return HttpResponse(status=404)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="home-hunter-reviews.csv"'
    response.write("\ufeff")
    writer = csv.DictWriter(
        response,
        fieldnames=rows[0].keys() if rows else ("listing_id", "rating", "decision"),
    )
    writer.writeheader()
    for row in rows:
        row["tags"] = ", ".join(row["tags"])
        writer.writerow(row)
    return response


@login_required
def market_export_xlsx(request):
    """Сформировать XLSX-выгрузку рыночного сравнения пользователя."""
    from .services.market_export import build_market_xlsx

    response = HttpResponse(
        build_market_xlsx(request.user),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="home-hunter-market-export.xlsx"'
    return response
