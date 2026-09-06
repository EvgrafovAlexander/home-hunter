import asyncio
import time
from email.utils import formatdate
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from collectors.avito.collector import AvitoBlocked, AvitoCollector
from collectors.avito.session import SessionState, retry_after_seconds
from collectors.base import CollectionError
from .test_collector import HTML, SEARCH


def page_for(status=200, html=HTML, headers=None):
    return SimpleNamespace(
        url=SEARCH.url,
        goto=AsyncMock(return_value=SimpleNamespace(status=status, all_headers=AsyncMock(return_value=headers or {}))),
        content=AsyncMock(return_value=html),
        wait_for_function=AsyncMock(),
    )


@pytest.mark.parametrize('value,expected', [('120', 120), ('-1', 0), ('nonsense', 0), ('NaN', 0), (None, 0),
                                           (formatdate(1120, usegmt=True), 120)])
def test_retry_after(value, expected):
    assert retry_after_seconds(value, 1000) == expected


def test_manual_captcha_reads_open_page_without_second_navigation():
    collector = AvitoCollector(headless=False, manual_wait_seconds=10)
    collector.page = page_for(429)
    collector.page.content.side_effect = ['<title>Доступ ограничен</title>', HTML]
    assert asyncio.run(collector._load_page(SEARCH.url)) == HTML
    collector.page.goto.assert_awaited_once()
    assert collector.state.read()['blocked_until'] == 0
    assert not collector.stop_requested


def test_manual_timeout_preserves_block_and_sends_no_retries():
    collector = AvitoCollector(headless=False, manual_wait_seconds=1)
    collector.page = page_for(403, '<title>Доступ ограничен</title>')
    with patch('collectors.avito.collector.time', SimpleNamespace(time=time.time, monotonic=Mock(side_effect=[0, 2]))):
        with pytest.raises(AvitoBlocked, match='timeout'):
            asyncio.run(collector._load_page(SEARCH.url))
    assert collector.stop_requested
    collector.page.goto.assert_awaited_once()
    assert collector.state.read()['blocked_until'] > 0


def test_wrong_page_does_not_complete_manual_recovery():
    collector = AvitoCollector(headless=False, manual_wait_seconds=1)
    collector.page = page_for(403)
    collector.page.url = 'https://www.avito.ru/different-search'
    with patch('collectors.avito.collector.time', SimpleNamespace(time=time.time, monotonic=Mock(side_effect=[0, .5, 2]))), patch('collectors.avito.collector.asyncio.sleep', new_callable=AsyncMock):
        with pytest.raises(AvitoBlocked, match='timeout'):
            asyncio.run(collector._load_page(SEARCH.url))
    assert collector.stop_requested


def test_block_survives_restart_and_honors_retry_after(settings):
    first = AvitoCollector()
    first.page = page_for(429, '<title>Доступ ограничен</title>', {'retry-after': '7200', 'set-cookie': 'secret'})
    with patch('time.time', return_value=1000):
        with pytest.raises(AvitoBlocked):
            asyncio.run(first._load_page(SEARCH.url))
        assert first.state.read()['blocked_until'] == 8200
        second = AvitoCollector()
        second._start = AsyncMock()
        with pytest.raises(CollectionError, match='cooldown'):
            asyncio.run(second.collect(SEARCH, mode='fast'))
        second._start.assert_not_awaited()
        assert second.stop_requested
    diagnostics = (settings.AVITO_STATE_DIR / 'last-response.json').read_text()
    assert '7200' in diagnostics and 'secret' not in diagnostics


def test_spacing_between_searches_and_restarts(settings):
    settings.AVITO_PAGE_DELAY_SECONDS = 120
    first = AvitoCollector()
    first.page = page_for()
    with patch('time.time', return_value=1000):
        asyncio.run(first._load_page(SEARCH.url))
    # A different instance and URL still share the same navigation budget.
    second = AvitoCollector()
    second.page = page_for()
    with patch('collectors.avito.collector.time', SimpleNamespace(time=Mock(side_effect=[1030, 1030, 1120, 1120]))), patch('collectors.avito.collector.asyncio.sleep', new_callable=AsyncMock) as sleep:
        asyncio.run(second._pace_request())
    sleep.assert_awaited_once_with(90)
    assert second.state.read()['next_request_at'] == 1240


def test_corrupt_state_fails_closed(settings):
    state = SessionState(settings.AVITO_STATE_DIR)
    state.write_json('request-state.json', {'next_request_at': 'invalid', 'blocked_until': 0})
    collector = AvitoCollector()
    collector.page = page_for()
    with pytest.raises(ValueError, match='Invalid Avito session state'):
        asyncio.run(collector._load_page(SEARCH.url))
    collector.page.goto.assert_not_awaited()


def test_full_scan_is_opt_in(settings):
    settings.AVITO_FULL_SCAN_ENABLED = False
    collector = AvitoCollector()
    collector._start = AsyncMock()
    with pytest.raises(CollectionError, match='Full scans disabled'):
        asyncio.run(collector.collect(SEARCH, mode='full'))
    collector._start.assert_not_awaited()
