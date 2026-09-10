import asyncio
import inspect
import logging
import math
import random
import time
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from django.conf import settings
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from collectors.avito.session import SessionState
from collectors.base import BaseCollector, CollectionError, CollectionResult
from .parser import cian_url, parse_detail_page, parse_page

logger = logging.getLogger(__name__)
BLOCK_TEXT = ("обнаружен подозрительный трафик", "вы не робот?", "подтвердите, что запросы отправляли вы")


def search_url(url, page=1, *, newest=False):
    parts = urlsplit(cian_url(url))
    if parts.path != "/cat.php":
        raise ValueError("Expected a CIAN /cat.php search URL")
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k != "p" and not (newest and k == "sort")]
    if newest:
        pairs.append(("sort", "creation_date_desc"))
    pairs.append(("p", str(page)))
    return urlunsplit(parts._replace(query=urlencode(pairs), fragment=""))


def blocked(html, final_url):
    if "captcha" in urlsplit(final_url).path.lower():
        return True
    soup = BeautifulSoup(html, "html.parser")
    for card in soup.select('[data-name="CardComponent"]'):
        card.decompose()
    text = soup.get_text(" ", strip=True).lower()
    return "cian_waf_block" in text or any(s in text for s in BLOCK_TEXT)


class CianCollector(BaseCollector):
    def __init__(self, *, headless=False, manual_wait_seconds=0):
        if manual_wait_seconds:
            raise ValueError("CIAN manual captcha waiting is not implemented")
        self.headless = headless
        self.state = SessionState(settings.CIAN_STATE_DIR)
        self.context = self.page = self.playwright = None
        self.stop_requested = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        try:
            if self.context:
                await self.context.close()
        finally:
            if self.playwright:
                await self.playwright.stop()

    def _validate(self):
        proxy = urlsplit(settings.CIAN_PROXY_URL)
        if proxy.scheme not in {"socks5", "http", "https"} or not proxy.hostname or proxy.username or proxy.password:
            raise ValueError("CIAN_PROXY_URL must be an explicit proxy URL without credentials; direct fallback is disabled")
        for value in (settings.CIAN_PAGE_DELAY_MIN_SECONDS, settings.CIAN_PAGE_DELAY_MAX_SECONDS,
                      settings.CIAN_BLOCK_COOLDOWN_SECONDS):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("CIAN timing settings must be finite and positive")
        if (min(settings.CIAN_FAST_SCAN_PAGES, settings.CIAN_MAX_PAGES,
                settings.CIAN_FULL_BATCH_PAGES) < 1
                or settings.CIAN_PAGE_DELAY_MIN_SECONDS > settings.CIAN_PAGE_DELAY_MAX_SECONDS):
            raise ValueError("CIAN page limits must be positive")

    def _check_cooldown(self):
        until = self.state.read()["blocked_until"]
        if until > time.time():
            self.stop_requested = True
            raise RuntimeError(f"CIAN cooldown until {datetime.fromtimestamp(until, timezone.utc).isoformat()}; no request sent")

    async def _start(self):
        if self.page is not None:
            return
        self.state.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.playwright = await async_playwright().start()
        self.context = await self.playwright.chromium.launch_persistent_context(
            str(self.state.directory / "chromium"), headless=self.headless, channel="chromium",
            viewport={"width": 1440, "height": 1000}, locale="ru-RU",
            proxy={"server": settings.CIAN_PROXY_URL},
        )
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()

    async def _restart_browser(self):
        """Start a fresh browser session before retrying an inconsistent SERP page."""
        try:
            if self.context:
                await self.context.close()
        finally:
            self.context = self.page = None
            if self.playwright:
                await self.playwright.stop()
            self.playwright = None
        await self._start()

    async def _load_parsed_page(self, url, search, number):
        parsed = parse_page(await self._load_page(url), url)
        if not parsed.errors:
            return parsed
        logger.warning(
            "CIAN search=%s page=%s has %s damaged/mismatched cards; retrying with a fresh session",
            search.pk, number, parsed.errors,
        )
        await self._restart_browser()
        retried = parse_page(await self._load_page(url), url)
        if retried.errors:
            logger.warning(
                "CIAN search=%s page=%s still has %s damaged/mismatched cards; "
                "saving valid cards and disabling disappearance reconciliation for this pass",
                search.pk, number, retried.errors,
            )
        return retried

    async def _load_page(self, url, *, expected_path="/cat.php", delay_min=None, delay_max=None):
        self._check_cooldown()
        delay = self.state.read()["next_request_at"] - time.time()
        if delay > 0:
            logger.info("CIAN waiting %.1fs before next navigation", delay)
            await asyncio.sleep(delay)
        self._check_cooldown()
        delay_min = settings.CIAN_PAGE_DELAY_MIN_SECONDS if delay_min is None else delay_min
        delay_max = settings.CIAN_PAGE_DELAY_MAX_SECONDS if delay_max is None else delay_max
        think_time = random.uniform(delay_min, delay_max)
        self.state.update(next_request_at=time.time() + think_time)
        try:
            response = await self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
            status = response.status if response else None
            headers = await response.all_headers() if response else {}
            if status is not None and status < 400:
                try:
                    await self.page.wait_for_function("""() =>
                        document.querySelector('[data-name="CardComponent"]') ||
                        document.querySelector('#frontend-offer-card') ||
                        (window._cianConfig?.['frontend-serp'] || []).some(x => x.key === 'initialState') ||
                        /подозрительный трафик|Вы не робот|Подтвердите, что запросы/i.test(document.body?.innerText || '')
                    """, timeout=15000)
                except PlaywrightTimeout:
                    pass
            html = await self.page.content()
            is_blocked = status in {403, 429, 439} or blocked(html, self.page.url)
            soup = BeautifulSoup(html, "html.parser")
            self.state.write_json("last-response.json", {
                "time": datetime.now(timezone.utc).isoformat(), "url": url, "status": status,
                "final_path": urlsplit(self.page.url).path, "blocked": is_blocked,
                "title": soup.title.get_text()[:200] if soup.title else "",
                "cards": len(soup.select('[data-name="CardComponent"]')),
                "retry_after": headers.get("retry-after"),
            })
            if is_blocked:
                self.stop_requested = True
                self.state.block(settings.CIAN_BLOCK_COOLDOWN_SECONDS, headers.get("retry-after"))
                raise RuntimeError(f"CIAN blocked: HTTP {status}; cooldown saved")
            if status is None or status >= 400:
                raise RuntimeError(f"CIAN HTTP error: {status}")
            if urlsplit(self.page.url).hostname != urlsplit(url).hostname or urlsplit(self.page.url).path != expected_path:
                raise RuntimeError("Unexpected CIAN redirect")
            return html
        except Exception:
            # Proxy/network errors must not trigger requests for remaining searches.
            self.stop_requested = True
            raise

    async def poll_detail(self, listing):
        """One cautious detail navigation. WAF/network failures propagate and stop the batch."""
        self._validate()
        url = cian_url(listing.url)
        expected = f"/sale/flat/{listing.external_id}/"
        if urlsplit(url).path != expected:
            raise ValueError("Invalid CIAN detail URL")
        self._check_cooldown()
        await self._start()
        if (not math.isfinite(settings.CIAN_DETAIL_POLL_DELAY_MIN_SECONDS)
                or not math.isfinite(settings.CIAN_DETAIL_POLL_DELAY_MAX_SECONDS)
                or settings.CIAN_DETAIL_POLL_DELAY_MIN_SECONDS <= 0
                or settings.CIAN_DETAIL_POLL_DELAY_MIN_SECONDS > settings.CIAN_DETAIL_POLL_DELAY_MAX_SECONDS):
            raise ValueError("CIAN detail timing settings must be finite and positive")
        html = await self._load_page(
            url, expected_path=expected,
            delay_min=settings.CIAN_DETAIL_POLL_DELAY_MIN_SECONDS,
            delay_max=settings.CIAN_DETAIL_POLL_DELAY_MAX_SECONDS,
        )
        return parse_detail_page(html, url)

    async def collect(self, search, *, mode, start_page=1, page_budget=None, progress_callback=None):
        result = CollectionResult()
        try:
            self._validate()
            if mode not in {"fast", "full"}:
                raise ValueError("Unknown mode")
            if mode == "full" and not settings.CIAN_FULL_SCAN_ENABLED:
                raise ValueError("CIAN full scans disabled")
            if start_page < 1:
                raise ValueError("Invalid CIAN start page")
            url = search_url(search.url, page=start_page, newest=mode == "fast")
            self._check_cooldown()
            await self._start()
            seen = set()
            expected_total = 0
            limit = settings.CIAN_FAST_SCAN_PAGES if mode == "fast" else (page_budget or settings.CIAN_FULL_BATCH_PAGES)
            for number in range(start_page, start_page + limit):
                parsed = await self._load_parsed_page(url, search, number)
                result.pages_scanned += 1
                result.items_seen += parsed.card_count
                if expected_total and parsed.total != expected_total:
                    result.total_changes += 1
                    logger.warning(
                        "CIAN search=%s total changed during batch: %s -> %s on page=%s",
                        search.pk, expected_total, parsed.total, number,
                    )
                expected_total = parsed.total
                result.expected_total = expected_total
                # Detect servers silently returning another page/filter (unsafe for deactivation).
                def normalized_query(query):
                    pairs = parse_qsl(query, keep_blank_values=True)
                    if not any(k == "p" for k, _ in pairs):
                        pairs.append(("p", "1"))
                    return sorted(pairs)
                if normalized_query(parsed.query_string) != normalized_query(urlsplit(url).query):
                    raise ValueError("CIAN response query differs from requested query")
                fresh = 0
                for item in parsed.listings:
                    if item.external_id not in seen:
                        seen.add(item.external_id)
                        result.listings.append(item)
                        fresh += 1
                logger.info("CIAN search=%s page=%s cards=%s total=%s", search.pk, number, parsed.card_count, parsed.total)
                if parsed.errors:
                    result.skipped_cards += parsed.errors
                    result.safe_for_deactivation = False
                if progress_callback:
                    progress = progress_callback(result.pages_scanned, result.items_seen, len(seen))
                    if inspect.isawaitable(progress):
                        await progress
                if not parsed.next_url:
                    # From page one we can immediately detect a truncated
                    # response.  Later batches rely on their persisted ID set
                    # for the same check in the scan service.
                    result.complete = mode == "fast" or start_page > 1 or len(seen) >= expected_total
                    result.stop_reason = "end of results" if result.complete else "missing next link before total reached"
                    return result
                if not fresh:
                    raise ValueError("CIAN repeated page without new IDs")
                next_url = search_url(url, number + 1)
                if sorted(parse_qsl(urlsplit(parsed.next_url).query)) != sorted(parse_qsl(urlsplit(next_url).query)):
                    raise ValueError("CIAN next link changed filters or page number")
                if urlsplit(parsed.next_url).hostname != urlsplit(url).hostname:
                    raise ValueError("CIAN next link changed host")
                url = next_url
            result.complete = mode == "fast"
            result.stop_reason = "fast page budget" if mode == "fast" else "batch page budget"
            result.next_page = start_page + limit
            return result
        except Exception as exc:
            raise CollectionError(f"{type(exc).__name__}: {str(exc)[:500]}", result) from exc
