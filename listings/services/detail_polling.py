"""Общая orchestration-логика циклов detail-опроса источников."""

from asgiref.sync import sync_to_async
from django.db.models import F

from listings.models import Listing


def select_detail_candidates(source, state_relation, completed_ids, limit):
    """Выбирает ещё не обработанные активные объявления источника."""
    return list(
        Listing.objects.filter(source=source, is_active=True)
        .exclude(pk__in=completed_ids)
        .select_related(state_relation)
        .order_by(F(f"{state_relation}__last_checked_at").asc(nulls_first=True), "pk")[:limit]
    )


async def run_detail_batch(
    candidates,
    progress_id,
    collector,
    fetch_detail,
    save_detail,
    state_model,
    mark_completed,
    set_current=None,
    clear_current=None,
    success_message=None,
    source_label="detail",
    stdout=None,
    stderr=None,
    stop_message=None,
    continue_polling=None,
    continue_stop_message=None,
):
    """Последовательно выполняет общий цикл detail с обработкой ошибок."""
    for listing in candidates:
        if continue_polling and not continue_polling():
            if stdout and continue_stop_message:
                stdout.write(continue_stop_message)
            break
        try:
            if set_current:
                await sync_to_async(set_current)(progress_id, listing.pk)
            detail = await fetch_detail(listing)
            available = await sync_to_async(save_detail)(listing.pk, detail)
            await sync_to_async(mark_completed)(progress_id, listing.pk)
            if stdout:
                message = success_message(listing, detail, available) if success_message else source_label
                stdout.write(message)
        except Exception as exc:
            if clear_current:
                await sync_to_async(clear_current)(progress_id)
            await sync_to_async(state_model.objects.update_or_create)(
                listing=listing,
                defaults={"last_error": f"{type(exc).__name__}: {exc}"[:1000]},
            )
            if stderr:
                stderr.write(f"{source_label} {listing.external_id}: {type(exc).__name__}")
            if getattr(collector, "stop_requested", False):
                if stderr and stop_message:
                    stderr.write(stop_message)
                break
