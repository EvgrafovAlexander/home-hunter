"""Общие операции сохранения detail-ответов источников."""

import hashlib
import json

from listings.models import ListingSnapshot
from listings.services.persistence import snapshot_data_from_listing


def save_detail_payload(payload_model, listing, payload, observed):
    """Сохраняет последний полный payload и обновляет его контрольную сумму."""
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    checksum = hashlib.sha256(encoded).hexdigest()
    row, created = payload_model.objects.select_for_update().get_or_create(
        listing=listing,
        defaults={
            "payload": payload,
            "sha256": checksum,
            "first_received_at": observed,
            "last_received_at": observed,
        },
    )
    if created:
        return
    fields = ["last_received_at"]
    row.last_received_at = observed
    if row.sha256 != checksum:
        row.payload, row.sha256 = payload, checksum
        fields += ["payload", "sha256"]
    row.save(update_fields=fields)


def mark_detail_unavailable(listing, state, detail_data, observed, snapshot_key):
    """Помечает detail недоступным и создаёт snapshot перехода состояния."""
    state.status = state.Status.UNAVAILABLE
    state.first_unavailable_at = state.first_unavailable_at or observed
    state.detail_data = detail_data
    state.save()
    if listing.is_active or listing.publication_status != listing.PublicationStatus.UNAVAILABLE:
        listing.is_active = False
        listing.publication_status = listing.PublicationStatus.UNAVAILABLE
        ListingSnapshot.objects.create(
            listing=listing,
            observed_at=observed,
            data={**snapshot_data_from_listing(listing), snapshot_key: detail_data},
        )
        listing.save(update_fields=("is_active", "publication_status", "updated_at"))
    return False
