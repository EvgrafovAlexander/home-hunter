"""Учёт обращений к SPFA и сохранение полученных cookies для Avito."""

import hashlib
import json
from datetime import datetime, timezone

from django.db import transaction

from listings.models import AvitoSpfaCookie, AvitoSpfaRequest


class SpfaRateLimitError(RuntimeError):
    """Платный запрос SPFA ещё не разрешён настроенным интервалом."""


class SpfaAudit:
    """Fail-closed журнал: запрос не отправляется, пока запись не создана."""

    def __init__(self, *, profile_code: str = "", route_code: str = ""):
        self.profile_code = profile_code or ""
        self.route_code = route_code or ""

    def start(self, operation: str, endpoint: str, payload: dict, *, cookie_id: int | None = None):
        safe_payload = {key: value for key, value in payload.items() if key not in {"api_key", "proxy"}}
        request_hash = hashlib.sha256(json.dumps(safe_payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with transaction.atomic():
            return AvitoSpfaRequest.objects.create(
                profile_code=self.profile_code, route_code=self.route_code,
                operation=operation, endpoint=endpoint, request_hash=request_hash,
                cookie_id=cookie_id,
            )

    @staticmethod
    def finish(record, *, status: str, response_status: int | None = None,
               response_success: bool | None = None, error: str = ""):
        record.status = status
        record.response_status = response_status
        record.response_success = response_success
        record.error = error[:4000]
        record.finished_at = datetime.now(timezone.utc)
        record.save(update_fields=("status", "response_status", "response_success", "error", "finished_at"))

    def save_cookie(self, cookie, *, fingerprint=None, mobile=False):
        return AvitoSpfaCookie.objects.create(
            profile_code=self.profile_code, route_code=self.route_code,
            cookie_id=cookie.cookie_id, cookies=cookie.cookies,
            user_agent=cookie.user_agent, fingerprint=fingerprint,
            mobile=mobile,
        )

    def ensure_paid_allowed(self, min_interval_seconds: float) -> None:
        """Запрещает платный вызов до истечения интервала с последней отправки."""
        if min_interval_seconds <= 0:
            return
        latest = (AvitoSpfaRequest.objects
                  .filter(profile_code=self.profile_code, route_code=self.route_code,
                          operation__in=("cookies", "cookies/mobile"))
                  .order_by("-started_at")
                  .first())
        if latest is None:
            return
        from django.utils import timezone
        elapsed = (timezone.now() - latest.started_at).total_seconds()
        if elapsed < min_interval_seconds:
            remaining = int(min_interval_seconds - elapsed) + 1
            raise SpfaRateLimitError(
                f"SPFA paid request cooldown: retry in {remaining}s; last sent at {latest.started_at.isoformat()}"
            )
