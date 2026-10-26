"""Разрешение сетевых и polling-профилей во время запуска.

Профили базы данных пока необязательны. Если профиль не задан, модуль
возвращает совместимый вариант на основе переменных окружения источника.
"""

from dataclasses import dataclass, field
import os
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings


@dataclass(frozen=True)
class RouteSpec:
    code: str
    name: str
    kind: str
    proxy_url: str
    endpoint: str = ""
    secret_ref: str = ""
    healthcheck_url: str = ""
    settings: dict[str, Any] = field(default_factory=dict)

    @property
    def resolved_proxy_url(self) -> str:
        """Возвращает URL прокси без учётных данных."""
        return self.resolved_proxy_options.get("server", "")

    @property
    def resolved_proxy_options(self) -> dict[str, str]:
        """Возвращает параметры прокси Playwright с отдельными учётными данными.

        ``secret_ref=SERVER_PROXY`` reads ``SERVER_PROXY_URL``,
        ``SERVER_PROXY_USERNAME`` and ``SERVER_PROXY_PASSWORD``.  The old
        convention of putting a complete URL in the variable named by
        ``secret_ref`` is retained for existing routes.
        """
        url = self.proxy_url
        username = password = ""
        if self.secret_ref:
            prefix = self.secret_ref
            url = os.getenv(f"{prefix}_URL", "") or os.getenv(prefix, "") or url
            username = os.getenv(f"{prefix}_USERNAME", "")
            password = os.getenv(f"{prefix}_PASSWORD", "")
        return build_proxy_options(url, username=username, password=password)

    @property
    def resolved_healthcheck_url(self) -> str:
        """Возвращает URL проверки маршрута с поддержкой настройки через окружение."""
        if self.secret_ref:
            return os.getenv(f"{self.secret_ref}_HEALTHCHECK_URL", "") or self.healthcheck_url
        return self.healthcheck_url


def build_proxy_options(proxy_url: str, *, username: str = "", password: str = "") -> dict[str, str]:
    """Проверяет URL прокси и формирует параметры Playwright.

    Credentials may be supplied in dedicated arguments (the preferred form)
    or embedded in a legacy URL.  The returned ``server`` never contains
    credentials, which prevents accidental leakage through diagnostics.
    """
    if not proxy_url:
        return {}
    # Провайдеры часто выдают адрес как host:port; для HTTP-прокси явно
    # добавляем схему, иначе urlsplit ошибочно принимает порт за часть пути.
    if "://" not in proxy_url:
        proxy_url = f"http://{proxy_url}"
    proxy = urlsplit(proxy_url)
    if (proxy.scheme not in {"socks5", "http", "https"} or not proxy.hostname
            or not proxy.port or proxy.path not in {"", "/"}
            or proxy.query or proxy.fragment):
        raise ValueError("Proxy URL must include a supported scheme, host and port without a path")
    embedded_username = proxy.username or ""
    embedded_password = proxy.password or ""
    username = username or embedded_username
    password = password or embedded_password
    if (username and not password) or (password and not username):
        raise ValueError("Proxy username and password must be supplied together")
    host = proxy.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{proxy.port}"
    options = {"server": urlunsplit((proxy.scheme, netloc, "", "", ""))}
    if username:
        options["username"] = username
        options["password"] = password
    return options


@dataclass(frozen=True)
class PollingProfileSpec:
    code: str
    name: str
    source: str
    mode: str
    route: RouteSpec
    transport: str = "browser_html"
    cookie_strategy: str = "browser_profile"
    session_mode: str = "settings"
    block_policy: str = "settings"
    settings: dict[str, Any] = field(default_factory=dict)

    @property
    def persistent_profile(self) -> bool | None:
        if self.session_mode == "persistent":
            return True
        if self.session_mode == "temporary":
            return False
        return None


def _legacy_profile(source: str, mode: str) -> PollingProfileSpec:
    defaults = {
        "avito": (
            "avito_default", "Avito: общий маршрут", "ssh_socks5", settings.AVITO_PROXY_URL,
            "avito", "settings", "cooldown",
        ),
        "cian": (
            "cian_default", "CIAN: общий маршрут", "ssh_socks5", settings.CIAN_PROXY_URL,
            "cian", "persistent", "cooldown",
        ),
        "domclick": (
            "domclick_default", "Домклик: общий маршрут", "ssh_socks5", settings.DOMCLICK_PROXY_URL,
            "domclick", "settings", "cooldown",
        ),
    }
    try:
        code, name, kind, proxy_url, source_code, session_mode, block_policy = defaults[source]
    except KeyError as exc:
        raise ValueError(f"Unsupported polling source: {source}") from exc
    if not proxy_url:
        kind = "direct"
    return PollingProfileSpec(
        code=code, name=name, source=source_code, mode=mode,
        route=RouteSpec(
            code=f"legacy_{source}", name="Общий SSH/SOCKS-маршрут" if proxy_url else "Прямое подключение",
            kind=kind, proxy_url=proxy_url,
        ),
        session_mode=session_mode, block_policy=block_policy,
    )


def resolve_profile(source: str, mode: str, code: str | None = None) -> PollingProfileSpec:
    """Читает профиль из базы или безопасно использует старые настройки окружения."""
    from listings.models import PollingProfile, SourcePollingControl, SourcePollingProfile

    query = PollingProfile.objects.select_related("route").filter(
        source=source, mode__in=(mode, "all"), enabled=True, route__enabled=True,
    )
    profile = query.filter(code=code).first() if code else None
    if profile is None and not code:
        source_assignment = SourcePollingProfile.objects.select_related("profile", "profile__route").filter(
            source=source, enabled=True, profile__mode__in=(mode, "all"),
            profile__enabled=True, profile__route__enabled=True,
        ).first()
        profile = source_assignment.profile if source_assignment else None
        if profile is None:
            control = SourcePollingControl.objects.select_related("profile", "profile__route").filter(
                source=source, mode=mode, enabled=True, profile__enabled=True, profile__route__enabled=True,
            ).first()
            profile = control.profile if control else query.filter(is_default=True).first()
    if profile is None and code:
        raise ValueError(f"Enabled polling profile not found: {code}")
    if profile is None:
        profile = query.order_by("pk").first()
    if profile is None:
        return _legacy_profile(source, mode)
    route = profile.route
    return PollingProfileSpec(
        code=profile.code,
        name=profile.name,
        source=profile.source,
        mode=mode,
        route=RouteSpec(
            code=route.code,
            name=route.name,
            kind=route.kind,
            proxy_url=route.proxy_url,
            endpoint=route.endpoint,
            secret_ref=route.secret_ref,
            healthcheck_url=route.healthcheck_url,
            settings=route.settings,
        ),
        session_mode=profile.session_mode,
        block_policy=profile.block_policy,
        transport=profile.transport,
        cookie_strategy=profile.cookie_strategy,
        settings=profile.settings,
    )
