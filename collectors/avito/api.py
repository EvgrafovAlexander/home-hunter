"""Необязательный коллектор Avito через мобильный API.

The browser/HTML collector remains the default.  This module is deliberately
selected only by an Avito polling profile with ``transport=mobile_api``.
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from django.conf import settings

from collectors.base import BaseCollector, CollectionError, CollectionResult, NormalizedListing
from collectors.routing import RouteSpec
from .collector import AvitoBlocked
from .parser import parse_money, parse_title
from .session import SessionState, profile_state_directory

logger = logging.getLogger(__name__)


def api_page_url(url: str, page: int) -> str:
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if key not in {"p", "page"}]
    query.append(("page", str(page)))
    return urlunsplit(parts._replace(query=urlencode(query), fragment=""))


def _first_dict(*values):
    return next((value for value in values if isinstance(value, dict)), {})


def extract_catalog(payload: dict) -> dict:
    result = payload.get("result") if isinstance(payload, dict) else None
    candidates = [
        payload.get("catalog") if isinstance(payload, dict) else None,
        result.get("catalog") if isinstance(result, dict) else None,
        result,
        payload,
    ]
    return next((candidate for candidate in candidates
                 if isinstance(candidate, dict) and isinstance(candidate.get("items"), list)), {})


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        return parse_money(value)
    return None


def _image_url(item: dict) -> str | None:
    gallery = item.get("gallery") if isinstance(item.get("gallery"), dict) else {}
    candidates = [gallery.get("imageLargeUrl"), gallery.get("imageUrl")]
    for image in item.get("images") or []:
        if isinstance(image, dict):
            candidates.extend(image.values())
        elif isinstance(image, str):
            candidates.append(image)
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
            return candidate
    return None


def normalize_item(item: dict) -> NormalizedListing:
    external_id = item.get("id")
    if isinstance(external_id, dict):
        external_id = external_id.get("value") or external_id.get("id")
    if not external_id:
        raise ValueError("API item missing id")
    title = str(item.get("title") or "").strip()
    path = item.get("urlPath") or item.get("url")
    if not title or not path:
        raise ValueError("API item missing title or url")
    url = urljoin("https://www.avito.ru", path)
    price = _number(_first_dict(item.get("priceDetailed")).get("value"))
    if price is None:
        price = parse_money(item.get("normalizedPrice") or item.get("priceWithoutDiscount"))
    address_data = _first_dict(item.get("geo"), item.get("addressDetailed"))
    address = address_data.get("formattedAddress") or address_data.get("locationName")
    description = item.get("description")
    return NormalizedListing(
        source="avito", external_id=str(external_id), title=title, url=url, price=price,
        address=str(address).strip() if address else None,
        description=str(description).strip() if description else None,
        image_url=_image_url(item), raw_data={"api_item": item}, **parse_title(title),
    )


class AvitoApiCollector(BaseCollector):
    """Читает страницы поиска Avito через настроенный URL мобильного API."""

    def __init__(self, *, profile, route: RouteSpec | None = None):
        self.profile = profile
        self.route = route
        self.profile_code = profile.code
        self.state = SessionState(profile_state_directory(settings.AVITO_STATE_DIR, self.profile_code))
        self.session = requests.Session()
        self.stop_requested = False
        self._configure_session()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self._save_cookies()
        self.session.close()

    def _configure_session(self):
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Referer": "https://m.avito.ru/",
            "User-Agent": self.profile.settings.get(
                "user_agent", "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
            ),
        })
        if self.route is not None:
            proxy = self.route.resolved_proxy_options
            if proxy:
                server = proxy["server"]
                parsed = urlsplit(server)
                credentials = ""
                if proxy.get("username"):
                    credentials = f"{proxy['username']}:{proxy['password']}@"
                server = urlunsplit((parsed.scheme, credentials + parsed.netloc, "", "", ""))
                self.session.proxies.update({"http": server, "https": server})
        strategy = getattr(self.profile, "cookie_strategy", "own_cookies")
        if strategy == "own_cookies":
            self._load_cookies()
        elif strategy == "spfa":
            self._load_spfa_cookies()
        else:
            raise ValueError("Mobile API requires own_cookies or spfa cookie strategy")

    @property
    def _settings(self):
        return self.profile.settings if isinstance(self.profile.settings, dict) else {}

    def _cookies_path(self) -> Path:
        return self.state.directory / "cookies.json"

    def _load_cookies(self):
        path = self._cookies_path()
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        cookies = data.get("cookies", data) if isinstance(data, dict) else {}
        if not isinstance(cookies, dict):
            raise ValueError("Avito cookies file must contain an object")
        self.session.cookies.update(cookies)

    def _save_cookies(self):
        if not self.session.cookies:
            return
        self.state.write_json("cookies.json", {"cookies": self.session.cookies.get_dict()})

    def _load_spfa_cookies(self):
        api_key_env = self._settings.get("spfa_api_key_env", "AVITO_SPFA_API_KEY")
        api_key = os.getenv(api_key_env, "")
        if not api_key and api_key_env == "AVITO_SPFA_API_KEY":
            api_key = settings.AVITO_SPFA_API_KEY
        if not api_key:
            raise ValueError(f"SPFA API key is not configured: {api_key_env}")
        proxy = self.route.resolved_proxy_url if self.route is not None else ""
        endpoint = self._settings.get("spfa_cookies_endpoint", "https://spfa.pro/api/cookies/mobile/")
        response = requests.post(endpoint, json={"api_key": api_key, "mobile": True, "proxy": proxy}, timeout=120)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("results", {}) if payload.get("success") else {}
        cookies = data.get("cookies")
        if not isinstance(cookies, dict) or not cookies:
            raise ValueError("SPFA returned no cookies")
        self.session.cookies.update(cookies)
        if data.get("user_agent"):
            self.session.headers["User-Agent"] = data["user_agent"]

    def _api_url(self, search_url: str) -> str:
        configured = self._settings.get("api_url")
        if configured:
            return configured
        cache_path = self.state.directory / "api-urls.json"
        cache = {}
        if cache_path.exists():
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        if search_url in cache:
            return cache[search_url]
        endpoint = self._settings.get(
            "url_converter_endpoint", settings.AVITO_URL_CONVERTER_ENDPOINT,
        )
        response = requests.post(endpoint, json={"url": search_url}, timeout=30)
        response.raise_for_status()
        payload = response.json()
        api_url = payload.get("api_url") if payload.get("success") else None
        if not api_url:
            raise ValueError("URL converter did not return api_url")
        cache[search_url] = api_url
        self.state.write_json("api-urls.json", cache)
        return api_url

    def _request(self, url: str) -> dict:
        response = self.session.get(url, timeout=float(self._settings.get("timeout", 30)))
        if response.status_code in {403, 429, 439}:
            self.stop_requested = True
            self.state.block(settings.AVITO_BLOCK_COOLDOWN_SECONDS, response.headers.get("Retry-After"))
            raise AvitoBlocked(f"Avito API blocked: HTTP {response.status_code}")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Avito API response must be an object")
        return payload

    async def collect(self, search, *, mode: str) -> CollectionResult:
        result = CollectionResult()
        try:
            if mode not in {"fast", "full"}:
                raise ValueError("Unknown scan mode")
            if mode == "full" and not settings.AVITO_FULL_SCAN_ENABLED:
                raise ValueError("Full scans disabled; set AVITO_FULL_SCAN_ENABLED=true explicitly")
            limit = settings.AVITO_FAST_SCAN_PAGES if mode == "fast" else settings.AVITO_MAX_PAGES
            if limit < 1:
                raise ValueError("Invalid Avito scan settings")
            self._check_cooldown()
            api_url = self._api_url(search.url)
            seen = set()
            for page in range(1, limit + 1):
                await self._pace()
                payload = await asyncio.to_thread(self._request, api_page_url(api_url, page))
                items = extract_catalog(payload).get("items", [])
                result.pages_scanned += 1
                result.items_seen += len(items)
                if not items:
                    result.complete = result.skipped_cards == 0
                    result.stop_reason = "damaged cards" if result.skipped_cards else "empty page"
                    return result
                for item in items:
                    try:
                        listing = normalize_item(item)
                    except (TypeError, ValueError, OverflowError) as exc:
                        result.skipped_cards += 1
                        logger.warning("Skipping damaged Avito API item: %s", exc)
                        continue
                    key = (listing.source, listing.external_id)
                    if key not in seen:
                        seen.add(key)
                        result.listings.append(listing)
            result.complete = mode == "fast"
            result.stop_reason = "fast page budget" if mode == "fast" else "max pages reached"
            return result
        except Exception as exc:
            raise CollectionError(f"{type(exc).__name__}: {str(exc)[:500]}", result) from exc

    async def poll_detail(self, external_id: str) -> dict:
        """Запрашивает endpoint карточки для второго этапа обогащения detail."""
        self._check_cooldown()
        await self._pace()
        item_id = str(external_id).strip()
        if not item_id.isdigit():
            raise ValueError("Avito external id must be numeric for detail API")
        return await asyncio.to_thread(
            self._request, f"https://m.avito.ru/api/1/card/items/{item_id}"
        )

    def _check_cooldown(self):
        until = self.state.read()["blocked_until"]
        if until > time.time():
            self.stop_requested = True
            raise AvitoBlocked("Avito API cooldown is active; no request sent")

    async def _pace(self):
        delay = self.state.read()["next_request_at"] - time.time()
        if delay > 0:
            await asyncio.sleep(delay)
        self._check_cooldown()
        self.state.update(next_request_at=time.time() + settings.AVITO_PAGE_DELAY_SECONDS)
