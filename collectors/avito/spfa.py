"""Клиент Smart Price For Avito для cookies и служебных методов.

Модуль отвечает только за HTTP-вызовы SPFA и проверку ответов. Он не знает о
`Listing`, профилях polling и файловом состоянии коллектора; это позволяет
использовать один контракт для браузерных и Mobile API запросов.
"""

from dataclasses import dataclass
from typing import Any

import requests


class SpfaError(RuntimeError):
    """SPFA вернул ошибку или ответ, который нельзя использовать."""


@dataclass(frozen=True)
class SpfaCookies:
    cookie_id: int
    cookies: dict[str, str]
    user_agent: str = ""
    fingerprint: dict[str, Any] | None = None


class SpfaClient:
    """Вызывает API SPFA с единым timeout и без утечки API-ключа в ошибки."""

    def __init__(self, api_key: str, *, base_url: str = "https://spfa.pro/api", timeout: float = 120):
        if not api_key:
            raise SpfaError("SPFA API key is not configured")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, payload: dict[str, Any], *, accepted: tuple[int, ...] = (200,)) -> dict[str, Any]:
        try:
            response = requests.post(f"{self.base_url}/{path.strip('/')}/", json=payload, timeout=self.timeout)
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise SpfaError(f"SPFA request failed: {type(exc).__name__}") from exc
        if response.status_code not in accepted or not isinstance(data, dict) or not data.get("success"):
            message = data.get("message", "unknown error") if isinstance(data, dict) else "invalid response"
            raise SpfaError(f"SPFA {path} failed: HTTP {response.status_code}: {message}")
        return data

    def balance(self) -> Any:
        """Возвращает текущий рублёвый баланс без списания средств."""
        return self._post("balance", {"api_key": self.api_key}).get("balance")

    def cookies(self, *, mobile: bool = False, proxy: str = "", full_format: bool = True) -> SpfaCookies:
        """Получает cookies; для mobile передаёт обязательный авторизованный proxy."""
        if mobile and "@" not in proxy:
            raise SpfaError("SPFA mobile cookies require an authenticated proxy")
        if mobile:
            payload = {"api_key": self.api_key, "proxy": proxy}
            path = "cookies/mobile"
        else:
            payload = {"api_key": self.api_key, "full_format": full_format}
            path = "cookies"
        data = self._post(path, payload).get("results") or {}
        cookies = data.get("cookies")
        if not isinstance(cookies, dict) or not cookies:
            raise SpfaError("SPFA returned no cookies")
        try:
            cookie_id = int(data["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SpfaError("SPFA returned invalid cookie id") from exc
        return SpfaCookies(cookie_id, {str(k): str(v) for k, v in cookies.items()},
                           str(data.get("user_agent") or ""), data.get("fingerprint"))

    def unblock(self, cookie_id: int, *, proxy: str = "") -> SpfaCookies | None:
        """Ставит cookies в очередь разблокировки; 202 не означает завершение."""
        payload: dict[str, Any] = {"id": int(cookie_id), "api_key": self.api_key}
        if proxy:
            payload["proxy"] = proxy
        data = self._post("unblock", payload, accepted=(200, 202)).get("results") or {}
        cookies = data.get("cookies")
        if not isinstance(cookies, dict):
            return None
        return SpfaCookies(int(data.get("id", cookie_id)), {str(k): str(v) for k, v in cookies.items()})

    def web_to_api(self, url: str) -> str:
        """Преобразует публичный URL поиска Avito в Mobile API URL."""
        data = self._post("avito-url", {"url": url}, accepted=(200,))
        api_url = data.get("api_url")
        if not isinstance(api_url, str) or not api_url:
            raise SpfaError("SPFA returned no api_url")
        return api_url
