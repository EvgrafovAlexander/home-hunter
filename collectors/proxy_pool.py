"""Small, bounded support for public proxy lists.

This is intentionally an experimental route.  It selects one working proxy
for a collector run and never falls back to a direct connection.  A public
proxy is not reused across requests after an Avito block; the collector's
normal cooldown remains the source of truth for 403/429/439 responses.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from collectors.routing import RouteSpec, build_proxy_options

logger = logging.getLogger(__name__)

PROXY_RE = re.compile(r"(?P<host>(?:\d{1,3}\.){3}\d{1,3}|[a-zA-Z0-9.-]+):(?P<port>\d{2,5})")
SCHEME_RE = re.compile(r"\b(?P<scheme>HTTPS?|SOCKS5)\b", re.IGNORECASE)
DEFAULT_SOURCE_URL = "https://spys.one/proxys/RU/"
DEFAULT_HEALTHCHECK_URL = "https://api.ipify.org?format=json"


@dataclass(frozen=True)
class ProxyCandidate:
    scheme: str
    host: str
    port: int

    @property
    def url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    @property
    def label(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"


def _proxy_candidate(value: str, *, default_scheme: str = "http", scheme_by_port: dict[str, str] | None = None,
                     explicit_scheme: str = "") -> ProxyCandidate | None:
    match = PROXY_RE.search(value)
    if not match:
        return None
    scheme = explicit_scheme.lower().replace("http/https", "http")
    if "socks" in scheme:
        scheme = "socks5"
    elif scheme not in {"http", "https"}:
        scheme = (scheme_by_port or {}).get(match.group("port"), default_scheme)
    if scheme not in {"http", "https", "socks5"}:
        return None
    return ProxyCandidate(scheme, match.group("host"), int(match.group("port")))


def parse_proxy_list(html: str, *, require_high_anonymity: bool = True,
                     default_scheme: str = "http", scheme_by_port: dict[str, str] | None = None) -> list[ProxyCandidate]:
    """Parse address rows from a public HTML or JSON proxy list."""
    try:
        payload = json.loads(html)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, list):
        candidates: list[ProxyCandidate] = []
        seen: set[str] = set()
        for item in payload:
            if not isinstance(item, dict):
                continue
            candidate = _proxy_candidate(
                str(item.get("ip") or item.get("proxy") or ""),
                default_scheme=default_scheme,
                scheme_by_port=scheme_by_port,
                explicit_scheme=str(item.get("proxyType") or item.get("type") or ""),
            )
            if candidate and candidate.url not in seen:
                seen.add(candidate.url)
                candidates.append(candidate)
        return candidates

    soup = BeautifulSoup(html, "html.parser")
    candidates: list[ProxyCandidate] = []
    seen: set[str] = set()
    for row in soup.select("tr"):
        text = " ".join(row.stripped_strings)
        if require_high_anonymity and "HIA" not in text.upper():
            continue
        scheme_match = SCHEME_RE.search(text)
        candidate = _proxy_candidate(
            text,
            default_scheme=default_scheme,
            scheme_by_port=scheme_by_port,
            explicit_scheme=scheme_match.group("scheme") if scheme_match else "",
        )
        if not candidate:
            continue
        if candidate.url not in seen:
            seen.add(candidate.url)
            candidates.append(candidate)
    return candidates


async def fetch_proxy_list(url: str, timeout: float, *, method: str = "GET", payload: dict | None = None) -> str:
    """Fetch a public list without exposing it to an Avito browser session."""
    def read() -> str:
        data = json.dumps(payload).encode("utf-8") if method.upper() == "POST" else None
        headers = {"User-Agent": "home-hunter/1.0", "Accept": "application/json, text/html"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method.upper())
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - configured public source
            return response.read().decode("utf-8", errors="replace")

    return await asyncio.to_thread(read)


async def select_working_proxy(playwright, route: RouteSpec) -> dict[str, str]:
    """Return one validated Playwright proxy mapping for a pool route."""
    settings = route.settings or {}
    source_url = str(settings.get("source_url") or route.endpoint or DEFAULT_SOURCE_URL)
    source_method = str(settings.get("source_method") or "GET").upper()
    source_payload = settings.get("source_payload")
    if source_payload is not None and not isinstance(source_payload, dict):
        raise ValueError("Public proxy pool source_payload must be an object")
    healthcheck_url = str(
        settings.get("healthcheck_url") or route.resolved_healthcheck_url or DEFAULT_HEALTHCHECK_URL
    )
    list_timeout = float(settings.get("list_timeout_seconds", 10))
    probe_timeout = float(settings.get("probe_timeout_seconds", 8))
    max_candidates = int(settings.get("max_candidates", 8))
    max_attempts = int(settings.get("max_attempts", 3))
    require_high_anonymity = bool(settings.get("require_high_anonymity", True))
    default_scheme = str(settings.get("default_scheme") or "http")
    scheme_by_port = {str(key): str(value) for key, value in (settings.get("scheme_by_port") or {}).items()}
    if not (0 < list_timeout <= 60 and 0 < probe_timeout <= 60):
        raise ValueError("Public proxy pool timeouts must be between 0 and 60 seconds")
    if not (1 <= max_candidates <= 50 and 1 <= max_attempts <= max_candidates):
        raise ValueError("Public proxy pool candidate limits are invalid")

    html = await fetch_proxy_list(source_url, list_timeout, method=source_method, payload=source_payload)
    candidates = parse_proxy_list(
        html,
        require_high_anonymity=require_high_anonymity,
        default_scheme=default_scheme,
        scheme_by_port=scheme_by_port,
    )[:max_candidates]
    if not candidates:
        raise RuntimeError("Public proxy list returned no usable candidates")

    attempts = 0
    for candidate in candidates:
        if attempts >= max_attempts:
            break
        attempts += 1
        proxy_options = build_proxy_options(candidate.url)
        browser = None
        try:
            browser = await playwright.chromium.launch(headless=True, proxy=proxy_options)
            page = await browser.new_page()
            response = await page.goto(healthcheck_url, wait_until="domcontentloaded", timeout=int(probe_timeout * 1000))
            if response and response.status < 400:
                logger.info("Public proxy candidate selected: %s", candidate.label)
                return proxy_options
            logger.info("Public proxy candidate failed healthcheck: %s status=%s", candidate.label,
                        response.status if response else None)
        except Exception as exc:  # noqa: BLE001 - one bad public proxy must not abort the pool
            logger.info("Public proxy candidate failed: %s (%s)", candidate.label, type(exc).__name__)
        finally:
            if browser:
                await browser.close()
    raise RuntimeError(f"No working public proxy found after {attempts} attempt(s)")
