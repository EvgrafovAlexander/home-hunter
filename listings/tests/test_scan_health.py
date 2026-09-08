from unittest.mock import patch

import pytest
from django.utils import timezone

from listings.models import Scan, SearchQuery, SourcePollingControl
from listings.services.scan_health import check_source_health


pytestmark = pytest.mark.django_db


def scan(search, status):
    return Scan.objects.create(
        search_query=search, source=search.source, mode="fast", status=status,
        finished_at=timezone.now() if status != Scan.Status.RUNNING else None,
    )


def test_notifies_on_failure_and_recovery_only_once():
    search = SearchQuery.objects.create(name="Avito", source="avito", url="https://example.test")
    with patch("listings.services.scan_health.send_telegram_message") as notify:
        scan(search, Scan.Status.SUCCESS)
        assert check_source_health("avito").state == "healthy"
        notify.assert_not_called()

        scan(search, Scan.Status.FAILED)
        assert check_source_health("avito").state == "failed"
        assert "Проблема" in notify.call_args.args[0]
        check_source_health("avito")
        assert notify.call_count == 1

        scan(search, Scan.Status.SUCCESS)
        assert check_source_health("avito").state == "healthy"
        assert notify.call_count == 2
        assert "Восстановление" in notify.call_args.args[0]


def test_escalates_after_three_consecutive_failures():
    search = SearchQuery.objects.create(name="CIAN", source="cian", url="https://example.test")
    with patch("listings.services.scan_health.send_telegram_message") as notify:
        for _ in range(3):
            scan(search, Scan.Status.FAILED)
        health = check_source_health("cian")
    assert health.state == "failed_3"
    assert "3 ошибки подряд" in notify.call_args.args[0]


def test_disabled_source_does_not_send_stale_notification():
    search = SearchQuery.objects.create(name="Домклик", source="domclick", url="https://example.test")
    SourcePollingControl.objects.create(source=search.source, mode="fast", enabled=False)
    with patch("listings.services.scan_health.send_telegram_message") as notify:
        health = check_source_health(search.source)
    assert health.state == "disabled"
    notify.assert_not_called()
