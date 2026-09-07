import asyncio
import logging
import math
import time
from datetime import datetime, timezone
from tempfile import TemporaryDirectory
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from django.conf import settings
from playwright.async_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout, async_playwright

from collectors.avito.session import SessionState
from collectors.base import BaseCollector, CollectionError, CollectionResult
from .parser import parse_page

logger = logging.getLogger(__name__)
BLOCK_TEXT = ("доступ ограничен", "проверка безопасности", "капча", "captcha", "слишком много запросов")


def set_page(url: str, number: int) -> str:
    parts = urlsplit(url)
    if parts.path == "/search":
        key, value = "offset", str((number - 1) * 20)
    else:
        key, value = "page", str(number)
    query = [(item_key, item_value) for item_key, item_value in parse_qsl(
        parts.query, keep_blank_values=True,
    ) if item_key != key]
    if number > 1:
        query.append((key, value))
    return urlunsplit(parts._replace(query=urlencode(query)))


class DomclickBlocked(RuntimeError):
    pass


class DomclickCollector(BaseCollector):
    def __init__(self, *, headless=True, manual_wait_seconds=0):
        if manual_wait_seconds < 0 or not math.isfinite(manual_wait_seconds):
            raise ValueError("Invalid manual captcha timeout")
        if headless and manual_wait_seconds:
            raise ValueError("Manual captcha waiting requires --headed")
        self.headless = headless
        self.manual_wait_seconds = manual_wait_seconds
        self.state = SessionState(settings.DOMCLICK_STATE_DIR)
        self.playwright = self.context = self.page = None
        self.temporary_profile = None
        self.start_error: Exception | None = None
        self.stop_requested = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        try:
            if self.context:
                await self.context.close()
        finally:
            try:
                if self.playwright:
                    await self.playwright.stop()
            finally:
                if self.temporary_profile:
                    self.temporary_profile.cleanup()

    def _validate(self, url: str) -> None:
        parts = urlsplit(url)
        is_public_listing = parts.path.startswith("/pokupka/") or parts.path.startswith("/arenda/")
        if (parts.scheme != "https" or not (parts.hostname or "").endswith("domclick.ru")
                or not (parts.path == "/search" or is_public_listing)):
            raise ValueError("Expected an HTTPS Domclick search or public listing URL")
        proxy = urlsplit(settings.DOMCLICK_PROXY_URL)
        if (proxy.scheme not in {"socks5", "http", "https"} or not proxy.hostname
                or proxy.username or proxy.password or not proxy.port
                or proxy.path not in {"", "/"} or proxy.query or proxy.fragment):
            raise ValueError("DOMCLICK_PROXY_URL must be an explicit proxy URL without credentials; direct fallback is disabled")
        for value in (settings.DOMCLICK_PAGE_DELAY_SECONDS, settings.DOMCLICK_BLOCK_COOLDOWN_SECONDS):
            if not math.isfinite(value) or value < 0:
                raise ValueError("Invalid Domclick timing settings")
        if min(settings.DOMCLICK_FAST_SCAN_PAGES, settings.DOMCLICK_MAX_PAGES,
               settings.DOMCLICK_NO_NEW_PAGES_LIMIT) < 1:
            raise ValueError("Invalid Domclick page limits")

    def _cooldown(self):
        until = self.state.read()["blocked_until"]
        if until > time.time():
            self.stop_requested = True
            raise DomclickBlocked(f"Domclick cooldown until {datetime.fromtimestamp(until, timezone.utc).isoformat()}")

    async def _start(self):
        if self.start_error is not None:
            raise RuntimeError("Browser startup previously failed") from self.start_error
        if self.page:
            return
        try:
            self.state.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            profile = self.state.directory / "chromium"
            if not settings.DOMCLICK_PERSISTENT_PROFILE:
                self.temporary_profile = TemporaryDirectory(prefix="chromium-run-", dir=self.state.directory)
                profile = self.temporary_profile.name
            self.playwright = await async_playwright().start()
            self.context = await self.playwright.chromium.launch_persistent_context(
                str(profile), headless=self.headless, channel="chromium",
                viewport={"width": 1440, "height": 1000}, locale="ru-RU",
                proxy={"server": settings.DOMCLICK_PROXY_URL},
            )
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        except Exception as exc:
            self.stop_requested = True
            self.start_error = exc
            raise

    @staticmethod
    def _page_state(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True).lower()
        if any(word in text for word in BLOCK_TEXT):
            return "blocked"
        if soup.select_one('a[href*="/card/"]') or "ничего не найдено" in text:
            return "ready"
        return "unknown"

    async def _wait_for_manual_captcha(self, url: str) -> str:
        logger.warning("Wait for manual Domclick recovery in the open browser within %.0fs; no reloads",
                       self.manual_wait_seconds)
        deadline = time.monotonic() + self.manual_wait_seconds
        while time.monotonic() < deadline:
            try:
                html = await self.page.content()
            except PlaywrightError:
                if self.page.is_closed():
                    raise
                await asyncio.sleep(1)
                continue
            if self.page.url.split("#")[0] == url.split("#")[0] and self._page_state(html) == "ready":
                self.state.update(blocked_until=0, next_request_at=max(
                    self.state.read()["next_request_at"], time.time() + settings.DOMCLICK_PAGE_DELAY_SECONDS,
                ))
                self.stop_requested = False
                return html
            await asyncio.sleep(1)
        raise DomclickBlocked("Domclick access blocked / security check; manual wait timeout")

    async def _load_page(self, url: str) -> str:
        self._cooldown()
        delay = self.state.read()["next_request_at"] - time.time()
        if delay > 0:
            await asyncio.sleep(delay)
        self._cooldown()
        self.state.update(next_request_at=time.time() + settings.DOMCLICK_PAGE_DELAY_SECONDS)
        try:
            response = await self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightError:
            self.stop_requested = True
            raise
        status = response.status if response else None
        headers = await response.all_headers() if response else {}
        if status is not None and status < 400:
            try:
                await self.page.wait_for_function("""() =>
                    document.querySelector('a[href*="/card/"]') ||
                    /ничего не найдено|доступ ограничен|капча/i.test(document.body?.innerText || '')
                """, timeout=30000)
            except PlaywrightTimeout:
                pass
        html = await self.page.content()
        blocked = status in {401, 403, 429, 439} or self._page_state(html) == "blocked"
        self.state.write_json("last-response.json", {"time": datetime.now(timezone.utc).isoformat(),
            "url": url, "status": status, "blocked": blocked, "cards": html.count("/card/")})
        if blocked:
            self.stop_requested = True
            self.state.block(settings.DOMCLICK_BLOCK_COOLDOWN_SECONDS, headers.get("retry-after"))
            if self.manual_wait_seconds:
                return await self._wait_for_manual_captcha(url)
            raise DomclickBlocked(f"Domclick blocked: HTTP {status}")
        if status is None or status >= 400:
            raise RuntimeError(f"Domclick HTTP error: {status}")
        final = urlsplit(self.page.url)
        expected = urlsplit(url)
        if final.hostname != expected.hostname or final.path != expected.path:
            raise RuntimeError("Unexpected Domclick redirect")
        return html

    async def collect(self, search, *, mode: str) -> CollectionResult:
        result = CollectionResult()
        try:
            if mode not in {"fast", "full"}:
                raise ValueError("Unknown scan mode")
            if mode == "full" and not settings.DOMCLICK_FULL_SCAN_ENABLED:
                raise ValueError("Domclick full scans disabled")
            self._validate(search.url)
            self._cooldown()
            await self._start()
            limit = settings.DOMCLICK_FAST_SCAN_PAGES if mode == "fast" else settings.DOMCLICK_MAX_PAGES
            seen, stale = set(), 0
            for number in range(1, limit + 1):
                page_url = set_page(search.url, number)
                parsed = parse_page(await self._load_page(page_url), page_url)
                result.pages_scanned += 1
                result.items_seen += parsed.card_count
                if parsed.errors:
                    result.stop_reason = "damaged cards"
                    return result
                if not parsed.card_count:
                    result.complete = True
                    result.stop_reason = "end of results"
                    return result
                fresh = 0
                for item in parsed.listings:
                    if item.external_id not in seen:
                        seen.add(item.external_id)
                        result.listings.append(item)
                        fresh += 1
                stale = stale + 1 if not fresh else 0
                if mode == "full" and stale >= settings.DOMCLICK_NO_NEW_PAGES_LIMIT:
                    result.stop_reason = "consecutive pages without new IDs"
                    return result
            result.complete = mode == "fast"
            result.stop_reason = "fast page budget" if mode == "fast" else "max pages reached"
            return result
        except Exception as exc:
            raise CollectionError(f"{type(exc).__name__}: {str(exc)[:500]}", result) from exc
