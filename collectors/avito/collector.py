import asyncio
import logging
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from django.conf import settings
from playwright.async_api import async_playwright

from collectors.base import BaseCollector, CollectionError, CollectionResult
from . import selectors as sel
from .parser import parse_page

if TYPE_CHECKING:
    from listings.models import SearchQuery

logger = logging.getLogger(__name__)
BLOCK_TEXT = ("доступ ограничен", "проверка безопасности", "проблема с ip", "проблемы с ip")


def set_page(url: str, number: int) -> str:
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != "p"]
    query.append(("p", str(number)))
    return urlunsplit(parts._replace(query=urlencode(query)))


class AvitoCollector(BaseCollector):
    def __init__(self, *, headless: bool = True):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.start_error: Exception | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        try:
            if self.browser:
                await self.browser.close()
        finally:
            if self.playwright:
                await self.playwright.stop()

    async def _start(self) -> None:
        if self.start_error is not None:
            raise RuntimeError("Browser startup previously failed") from self.start_error
        if self.page is not None:
            return
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(headless=self.headless)
            self.context = await self.browser.new_context(viewport={"width": 1440, "height": 1000}, locale="ru-RU")
            self.page = await self.context.new_page()
        except Exception as exc:
            self.start_error = exc
            raise

    async def _load_page(self, url: str) -> str:
        response = await self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        if response is None or response.status >= 400:
            raise RuntimeError(f"Page HTTP error: {response.status if response else 'no response'}")
        # A blank/challenge page is not evidence of an empty search.
        await self.page.wait_for_function(
            """([item, empty, blocked]) => {
                const text = document.body?.innerText.toLowerCase() || '';
                return document.querySelector(item) || document.querySelector(empty)
                    || blocked.some(s => text.includes(s))
                    || text.includes('ничего не найдено');
            }""",
            arg=[sel.ITEM, sel.EMPTY, list(BLOCK_TEXT)], timeout=30000,
        )
        html = await self.page.content()
        soup = BeautifulSoup(html, "html.parser")
        # Ignore listing descriptions when looking for page-level block messages.
        for card in soup.select(sel.ITEM):
            card.decompose()
        text = soup.get_text(" ", strip=True).lower()
        if any(word in text for word in BLOCK_TEXT):
            raise RuntimeError("Avito access blocked / security check")
        if not BeautifulSoup(html, "html.parser").select(sel.ITEM):
            if not soup.select_one(sel.EMPTY) and "ничего не найдено" not in text:
                raise RuntimeError("Unrecognized search page")
        return html

    async def collect(self, search: "SearchQuery", *, mode: str) -> CollectionResult:
        result = CollectionResult()
        try:
            if mode not in {"fast", "full"}:
                raise ValueError("Unknown scan mode")
            parts = urlsplit(search.url)
            if parts.scheme != "https" or parts.hostname not in {"avito.ru", "www.avito.ru"}:
                raise ValueError("Expected an https://www.avito.ru search URL")
            limit = settings.AVITO_FAST_SCAN_PAGES if mode == "fast" else settings.AVITO_MAX_PAGES
            if limit < 1 or settings.AVITO_NO_NEW_PAGES_LIMIT < 1 or settings.AVITO_PAGE_DELAY_SECONDS < 0:
                raise ValueError("Invalid Avito scan settings")
            await self._start()
            seen = set()
            stale_pages = 0
            damaged = False
            for number in range(1, limit + 1):
                if number > 1:
                    await asyncio.sleep(settings.AVITO_PAGE_DELAY_SECONDS)
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
