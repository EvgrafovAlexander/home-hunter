import time

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from listings.services.telegram_reviews import handle_update
from listings.models import TelegramPollingState


class Command(BaseCommand):
    help = "Poll Telegram Bot API and process interactive listing reviews."

    def handle(self, *args, **options):
        if not settings.TG_BOT_TOKEN:
            self.stderr.write("TG_BOT_TOKEN is not configured")
            return
        state, _ = TelegramPollingState.objects.get_or_create(key="default")
        offset = state.offset
        timeout = max(1, settings.TG_POLLING_TIMEOUT)
        proxies = {"http": settings.TELEGRAM_PROXY_URL, "https": settings.TELEGRAM_PROXY_URL} if settings.TELEGRAM_PROXY_URL else None
        while True:
            try:
                response = requests.get(f"https://api.telegram.org/bot{settings.TG_BOT_TOKEN}/getUpdates",
                                        params={"timeout": timeout, "offset": offset, "allowed_updates": '["message","callback_query"]'},
                                        proxies=proxies, timeout=timeout + 10)
                response.raise_for_status()
                for update in response.json().get("result", []):
                    offset = update["update_id"] + 1
                    state.offset = offset
                    state.save(update_fields=("offset", "updated_at"))
                    handle_update(update)
            except requests.RequestException as exc:
                self.stderr.write(f"Telegram polling error: {type(exc).__name__}")
                time.sleep(5)
