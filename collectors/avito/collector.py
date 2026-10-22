import asyncio
import logging
import math
import time
from tempfile import TemporaryDirectory
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from django.conf import settings
from playwright.async_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout, async_playwright

from collectors.base import BaseCollector, CollectionError, CollectionResult
from collectors.routing import RouteSpec, build_proxy_options
from collectors.proxy_pool import select_working_proxy
from . import selectors as sel
from .parser import parse_page
from .session import SessionState, profile_state_directory

if TYPE_CHECKING:
    from listings.models import SearchQuery

logger = logging.getLogger(__name__)
BLOCK_TEXT = (
    "доступ ограничен", "проверка безопасности", "проблема с ip", "проблемы с ip",
    "подтвердите, что вы не робот", "капча", "captcha",
)


def set_page(url: str, number: int) -> str:
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != "p"]
    query.append(("p", str(number)))
    return urlunsplit(parts._replace(query=urlencode(query)))


class AvitoBlocked(RuntimeError):
    pass


class AvitoCollector(BaseCollector):
    def __init__(self, *, headless: bool = True, manual_wait_seconds: float = 0,
                 route: RouteSpec | None = None, session_mode: str = "settings",
                 profile_code: str | None = None):
        if manual_wait_seconds < 0 or not math.isfinite(manual_wait_seconds):
            raise ValueError("Invalid manual captcha timeout")
        if headless and manual_wait_seconds:
            raise ValueError("Manual captcha waiting requires --headed")
        self.manual_wait_seconds = manual_wait_seconds
        self.route = route
        self.session_mode = session_mode
        self.profile_code = profile_code
        self.stop_requested = False
        self.state = SessionState(self._state_directory())
        # Диагностика может изолировать состояние запросов, не меняя
        # выбранный постоянный профиль браузера.
        self.browser_profile_dir = None
        self.headless = headless
        self.temporary_profile = None
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.start_error: Exception | None = None

    def _state_directory(self):
        """Возвращает изолированное состояние браузера и запросов профиля.

        Direct construction without a profile code keeps the legacy location
        used by tests and backwards-compatible manual runs.  Production
        polling passes the database profile code, so each proxy/session pair
        gets its own cookies and cooldown state.
        """
        return profile_state_directory(settings.AVITO_STATE_DIR, self.profile_code)

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

    async def _start(self) -> None:
        if self.start_error is not None:
            raise RuntimeError("Browser startup previously failed") from self.start_error
        if self.page is not None:
            return
        try:
            if self.route is not None and self.route.kind == "free_proxy_pool":
                proxy_options = {}
            else:
                selected_proxy = (
                    self.route.resolved_proxy_options
                    if self.route is not None
                    else build_proxy_options(settings.AVITO_PROXY_URL)
                )
                proxy_options = {"proxy": selected_proxy} if selected_proxy else {}
            self.state.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            profile = self.browser_profile_dir or self.state.directory / "chromium"
            persistent_profile = self._persistent_profile_enabled()
            if not persistent_profile:
                self.temporary_profile = TemporaryDirectory(prefix="chromium-run-", dir=self.state.directory)
                profile = self.temporary_profile.name
            self.playwright = await async_playwright().start()
            if self.route is not None and self.route.kind == "free_proxy_pool":
                proxy_options["proxy"] = await select_working_proxy(self.playwright, self.route)
            self.context = await self.playwright.chromium.launch_persistent_context(
                str(profile), headless=self.headless, channel="chromium",
                viewport={"width": 1440, "height": 1000}, locale="ru-RU", **proxy_options,
            )
            self.browser = self.context.browser
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        except Exception as exc:
            self.stop_requested = True
            self.start_error = exc
            raise

    def _persistent_profile_enabled(self):
        if self.session_mode == "persistent":
            return True
        if self.session_mode == "temporary":
            return False
        return settings.AVITO_PERSISTENT_PROFILE

    @staticmethod
    def _page_state(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(sel.ITEM)
        for card in cards:
            card.decompose()
        text = soup.get_text(" ", strip=True).lower()
        if any(word in text for word in BLOCK_TEXT):
            return "blocked"
        if cards or soup.select_one(sel.EMPTY) or "ничего не найдено" in text:
            return "ready"
        return "unknown"

    def _check_cooldown(self) -> None:
        until = self.state.read()["blocked_until"]
        if until > time.time():
            self.stop_requested = True
            stamp = datetime.fromtimestamp(until, timezone.utc).isoformat()
            raise AvitoBlocked(f"Avito cooldown until {stamp}; no request sent")

    async def _pace_request(self) -> None:
        self._check_cooldown()
        delay = self.state.read()["next_request_at"] - time.time()
        if delay > 0:
            logger.info("Waiting %.1fs before next Avito navigation", delay)
            await asyncio.sleep(delay)
        self._check_cooldown()
        self.state.update(next_request_at=time.time() + settings.AVITO_PAGE_DELAY_SECONDS)

    def _diagnose(self, url: str, html: str, status, headers: dict, phase: str) -> None:
        soup = BeautifulSoup(html, "html.parser")
        data = {
            "time": datetime.now(timezone.utc).isoformat(), "url": url,
            "status": status, "phase": phase,
            "title": soup.title.get_text(strip=True)[:200] if soup.title else "",
            "items": len(soup.select(sel.ITEM)),
            "headers": {key: headers[key] for key in ("retry-after", "content-type") if key in headers},
        }
        self.state.write_json("last-response.json", data)
        logger.info("Avito response: %s", data)

    async def _wait_for_manual_captcha(self, url: str) -> str:
        logger.warning("Complete the captcha in the open browser within %.0fs; no automatic reloads",
                       self.manual_wait_seconds)
        deadline = time.monotonic() + self.manual_wait_seconds
        while time.monotonic() < deadline:
            # Читаем только уже открытую вкладку: без storage_state(), goto() и reload().
            try:
                html = await self.page.content()
            except PlaywrightError:
                if self.page.is_closed():
                    raise
                await asyncio.sleep(1)
                continue  # Пользователь мог перейти на другую страницу после капчи.
            if self.page.url.split("#")[0] == url.split("#")[0] and self._page_state(html) == "ready":
                self.state.update(blocked_until=0, next_request_at=max(
                    self.state.read()["next_request_at"], time.time() + settings.AVITO_PAGE_DELAY_SECONDS))
                self.stop_requested = False
                self._diagnose(url, html, None, {}, "manual_recovery")
                return html
            await asyncio.sleep(1)
        raise AvitoBlocked("Avito access blocked / security check; manual captcha timeout")

    async def _load_page(self, url: str) -> str:
        await self._pace_request()
        try:
            response = await self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightTimeout as exc:
            # Avito может догружать вторичные ресурсы после готовности выдачи.
            # Сохраняем готовую страницу и не считаем таймаут ошибкой скана.
            try:
                await self.page.wait_for_timeout(5000)
                html = await self.page.content()
            except PlaywrightError:
                self.stop_requested = True
                raise exc
            state = self._page_state(html)
            if state == "ready":
                self._diagnose(url, html, None, {}, "navigation_timeout_recovered")
                return html
            if state == "blocked":
                self.stop_requested = True
                self.state.block(settings.AVITO_BLOCK_COOLDOWN_SECONDS, None)
            raise exc
        except PlaywrightError:
            self.stop_requested = True
            raise  # При сбое маршрута не делаем повтор и не выходим напрямую.
        status = response.status if response else None
        headers = await response.all_headers() if response else {}
        # HTTP-блокировки обрабатываем сразу; успешная страница ещё может догружаться.
        if status is not None and status < 400:
            try:
                await self.page.wait_for_function(
                    """([item, empty, blocked]) => {
                        const text = document.body?.innerText.toLowerCase() || '';
                        return document.querySelector(item) || document.querySelector(empty)
                            || blocked.some(s => text.includes(s))
                            || text.includes('ничего не найдено');
                    }""",
                    arg=[sel.ITEM, sel.EMPTY, list(BLOCK_TEXT)], timeout=30000,
                )
            except PlaywrightTimeout:
                pass  # Диагностику и классификацию страницы выполним ниже.
        html = await self.page.content()
        state = self._page_state(html)
        blocked = status in {403, 429, 439} or state == "blocked"
        if blocked:
            self.stop_requested = True
            self.state.block(settings.AVITO_BLOCK_COOLDOWN_SECONDS, headers.get("retry-after"))
        self._diagnose(url, html, status, headers, "navigation")
        if blocked:
            if self.manual_wait_seconds:
                return await self._wait_for_manual_captcha(url)
            raise AvitoBlocked(f"Avito access blocked / security check; Page HTTP error: {status}")
        if status is None or status >= 400:
            raise RuntimeError(f"Page HTTP error: {status if status is not None else 'no response'}")
        if state != "ready":
            raise RuntimeError("Unrecognized search page")
        return html

    async def collect(self, search: "SearchQuery", *, mode: str) -> CollectionResult:
        result = CollectionResult()
        try:
            if mode not in {"fast", "full"}:
                raise ValueError("Unknown scan mode")
            if mode == "full" and not settings.AVITO_FULL_SCAN_ENABLED:
                raise ValueError("Full scans disabled; set AVITO_FULL_SCAN_ENABLED=true explicitly")
            parts = urlsplit(search.url)
            if parts.scheme != "https" or parts.hostname not in {"avito.ru", "www.avito.ru"}:
                raise ValueError("Expected an https://www.avito.ru search URL")
            limit = settings.AVITO_FAST_SCAN_PAGES if mode == "fast" else settings.AVITO_MAX_PAGES
            if limit < 1 or settings.AVITO_NO_NEW_PAGES_LIMIT < 1 or settings.AVITO_PAGE_DELAY_SECONDS < 0:
                raise ValueError("Invalid Avito scan settings")
            if (not math.isfinite(settings.AVITO_PAGE_DELAY_SECONDS)
                    or not math.isfinite(settings.AVITO_BLOCK_COOLDOWN_SECONDS)
                    or settings.AVITO_BLOCK_COOLDOWN_SECONDS <= 0):
                raise ValueError("Invalid Avito timing settings")
            self._check_cooldown()
            await self._start()
            seen = set()
            stale_pages = 0
            damaged = False
            for number in range(1, limit + 1):
                html = await self._load_page(set_page(search.url, number))
                parsed = parse_page(html)
                result.pages_scanned += 1
                result.items_seen += parsed.card_count
                damaged = damaged or bool(parsed.errors)
                logger.info("search=%s page=%s items=%s", search.name, number, parsed.card_count)
                if not parsed.card_count:
                    result.complete = not damaged
                    result.stop_reason = "damaged cards" if damaged else "empty page"
                    return result
                fresh = 0
                for item in parsed.listings:
                    key = (item.source, item.external_id)
                    if key not in seen:
                        seen.add(key)
                        result.listings.append(item)
                        fresh += 1
                stale_pages = stale_pages + 1 if fresh == 0 else 0
                if mode == "full" and stale_pages >= settings.AVITO_NO_NEW_PAGES_LIMIT:
                    result.stop_reason = "consecutive pages without new IDs"
                    logger.warning("Partial full scan: %s", result.stop_reason)
                    return result
            result.complete = mode == "fast"
            result.stop_reason = "fast page budget" if mode == "fast" else "max pages reached"
            if mode == "full":
                logger.warning("Partial full scan: %s", result.stop_reason)
            return result
        except Exception as exc:
            raise CollectionError(f"{type(exc).__name__}: {str(exc)[:500]}", result) from exc
