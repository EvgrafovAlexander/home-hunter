"""Коллектор поисковой выдачи Avito через Mobile API.

Коллектор выбирается только профилем с ``transport=mobile_api`` и передаёт
нормализованные объявления в общий persistence-контракт приложения.
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
from .spfa import SpfaClient, SpfaCookies, SpfaError
from listings.services.spfa_audit import SpfaAudit

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
        self.audit = SpfaAudit(profile_code=self.profile_code, route_code=route.code if route else "")
        self._configure_session()

    async def __aenter__(self):
        if getattr(self.profile, "cookie_strategy", "own_cookies") == "spfa":
            await asyncio.to_thread(self._load_spfa_cookies)
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
            # Загрузка cookie выполняется в рабочем потоке: внутри неё есть
            # синхронная запись аудита в Django ORM.
            return
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
        proxy = ""
        if self.route is not None:
            options = self.route.resolved_proxy_options
            if options.get("username"):
                proxy = f"{options['username']}:{options['password']}@{options['server'].split('://', 1)[-1]}"
        base_url = self._settings.get("spfa_base_url", settings.AVITO_SPFA_BASE_URL)
        client = SpfaClient(api_key, base_url=base_url, timeout=float(self._settings.get("spfa_timeout", 120)), audit=self.audit)
        cache_path = self.state.directory / "spfa-cookies.json"
        cookie_data = None
        min_interval = float(self._settings.get(
            "spfa_min_interval_seconds", settings.AVITO_SPFA_MIN_INTERVAL_SECONDS,
        ))
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if (cached.get("mobile") and cached.get("invalidated")
                        and isinstance(cached.get("cookies"), dict) and cached["cookies"]):
                    # Восстановление старой cookie бесплатно; суточный лимит
                    # относится только к выдаче новой платной cookie.
                    refreshed = client.unblock(int(cached["id"]), proxy=proxy)
                    if refreshed:
                        cookie_data = SpfaCookies(refreshed.cookie_id, refreshed.cookies,
                                                  str(cached.get("user_agent") or ""), cached.get("fingerprint"))
                elif (cached.get("mobile") and not cached.get("invalidated")
                        and isinstance(cached.get("cookies"), dict) and cached["cookies"]):
                    # Пока Avito не заблокировал сессию, повторное обращение к
                    # платному SPFA не требуется: используем сохранённый набор.
                    cookie_data = SpfaCookies(int(cached["id"]), cached["cookies"],
                                              str(cached.get("user_agent") or ""), cached.get("fingerprint"))
            except (OSError, ValueError, KeyError, SpfaError):
                cookie_data = None
        if cookie_data is None:
            self.audit.ensure_paid_allowed(min_interval)
            cookie_data = client.cookies(mobile=True, proxy=proxy)
        self.session.cookies.update(cookie_data.cookies)
        if cookie_data.fingerprint and isinstance(cookie_data.fingerprint.get("headers"), dict):
            self.session.headers.update({str(k): str(v) for k, v in cookie_data.fingerprint["headers"].items()})
        if cookie_data.user_agent:
            self.session.headers["User-Agent"] = cookie_data.user_agent
        self.state.write_json("spfa-cookies.json", {
            "id": cookie_data.cookie_id, "cookies": cookie_data.cookies,
            "user_agent": cookie_data.user_agent, "fingerprint": cookie_data.fingerprint, "mobile": True,
        })
        self.audit.save_cookie(cookie_data, fingerprint=cookie_data.fingerprint, mobile=True)

    def _api_url(self, search) -> str:
        """Возвращает URL API для поиска, не смешивая два транспорта."""
        explicit_url = getattr(search, "mobile_api_url", "")
        if explicit_url:
            return explicit_url
        configured = self._settings.get("api_url")
        if configured:
            return configured
        search_url = search.url
        cache_path = self.state.directory / "api-urls.json"
        cache = {}
        if cache_path.exists():
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        if search_url in cache:
            return cache[search_url]
        endpoint = self._settings.get(
            "url_converter_endpoint", settings.AVITO_URL_CONVERTER_ENDPOINT,
        )
        api_key = os.getenv(self._settings.get("spfa_api_key_env", "AVITO_SPFA_API_KEY"), "")
        if not api_key and self._settings.get("spfa_api_key_env", "AVITO_SPFA_API_KEY") == "AVITO_SPFA_API_KEY":
            api_key = settings.AVITO_SPFA_API_KEY
        # Конвертер бесплатный, но общий клиент требует ключ только для
        # платных методов, поэтому вызываем его с произвольным маркером.
        client = SpfaClient(api_key or "url-converter", base_url=endpoint.rsplit("/avito-url", 1)[0], audit=self.audit)
        api_url = client.web_to_api(search_url)
        cache[search_url] = api_url
        self.state.write_json("api-urls.json", cache)
        return api_url

    def _request(self, url: str) -> dict:
        response = self.session.get(url, timeout=float(self._settings.get("timeout", 30)))
        if response.status_code in {403, 429, 439}:
            self.stop_requested = True
            self.state.block(settings.AVITO_BLOCK_COOLDOWN_SECONDS, response.headers.get("Retry-After"))
            # Следующий запуск запросит свежую cookie; текущий не делает
            # дополнительного платного вызова SPFA во время обработки ошибки.
            cached = self.state.directory / "spfa-cookies.json"
            try:
                if cached.exists():
                    data = json.loads(cached.read_text(encoding="utf-8"))
                    data["invalidated"] = True
                    self.state.write_json("spfa-cookies.json", data)
            except (OSError, ValueError):
                pass
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
            api_url = await asyncio.to_thread(self._api_url, search)
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
