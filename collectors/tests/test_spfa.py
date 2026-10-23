from types import SimpleNamespace

import pytest

from collectors.avito.spfa import SpfaClient, SpfaError


def test_spfa_balance_and_web_converter(monkeypatch):
    calls = []

    def post(url, json, timeout):
        calls.append((url, json, timeout))
        if url.endswith("/balance/"):
            return SimpleNamespace(status_code=200, json=lambda: {"success": True, "balance": 12.5})
        return SimpleNamespace(status_code=200, json=lambda: {"success": True, "api_url": "https://m.avito.ru/api/11/items"})

    monkeypatch.setattr("collectors.avito.spfa.requests.post", post)
    client = SpfaClient("secret", timeout=3)
    assert client.balance() == 12.5
    assert client.web_to_api("https://www.avito.ru/ufa/kvartiry") == "https://m.avito.ru/api/11/items"
    assert calls[0][1] == {"api_key": "secret"}
    assert calls[1][1] == {"url": "https://www.avito.ru/ufa/kvartiry"}


def test_mobile_cookies_require_authenticated_proxy():
    client = SpfaClient("secret")
    with pytest.raises(SpfaError, match="authenticated proxy"):
        client.cookies(mobile=True, proxy="socks5://host:1")
