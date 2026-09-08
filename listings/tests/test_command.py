from io import StringIO
from unittest.mock import AsyncMock, patch
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from collectors.base import CollectionResult
from listings.models import Scan, SearchQuery, SourcePollingControl
from .test_persistence import search, item

pytestmark = pytest.mark.django_db(transaction=True)


def test_command_one_collector_sequential_searches(search, item):
    SearchQuery.objects.create(name="Other", source="avito", url=search.url)
    SearchQuery.objects.create(name="Disabled", source="avito", url=search.url, enabled=False)
    collector = AsyncMock()
    collector.stop_requested = False
    collector.__aenter__.return_value = collector
    collector.collect.return_value = CollectionResult([item], 1, 1, True)
    with patch("listings.management.commands.collect_listings.get_collector", return_value=collector) as factory:
        call_command("collect_listings", source="avito", mode="fast", headless=False, stdout=StringIO())
    factory.assert_called_once_with("avito", headless=False, manual_wait_seconds=0)
    assert collector.collect.await_count == 2
    assert collector.__aexit__.await_count == 1
    assert Scan.objects.filter(status="success").count() == 2


def test_command_failed_scan_exit(search, settings):
    settings.AVITO_FULL_SCAN_ENABLED = True
    SourcePollingControl.objects.create(source="avito", mode="full", enabled=True)
    collector = AsyncMock()
    collector.stop_requested = False
    collector.__aenter__.return_value = collector
    collector.collect.side_effect = RuntimeError("browser unavailable")
    with patch("listings.management.commands.collect_listings.get_collector", return_value=collector):
        with pytest.raises(CommandError):
            call_command("collect_listings", source="avito", mode="full", stdout=StringIO())
    assert Scan.objects.get().status == "failed"


def test_search_id_must_be_enabled(search):
    search.enabled = False
    search.save()
    with pytest.raises(CommandError):
        call_command("collect_listings", source="avito", mode="fast", search_id=search.pk)


def test_source_wide_switch_skips_collector(search):
    SourcePollingControl.objects.create(source="avito", mode="fast", enabled=False)
    output = StringIO()
    with patch("listings.management.commands.collect_listings.get_collector") as factory:
        call_command("collect_listings", source="avito", mode="fast", stdout=output)
    factory.assert_not_called()
    assert "Fast polling disabled for avito" in output.getvalue()
    assert not Scan.objects.exists()


def test_admin_auth_and_search_creation(client, admin_client):
    assert client.get("/").status_code == 302
    assert "/admin/login/" in client.get("/admin/listings/listing/").url
    response = admin_client.post("/admin/listings/searchquery/add/", {
        "name": "Admin search", "source": "avito", "url": "https://www.avito.ru/ufa/kvartiry",
        "enabled": "on", "_save": "Save",
    })
    assert response.status_code == 302
    assert SearchQuery.objects.filter(name="Admin search", enabled=True).exists()
    for model in ("listing", "searchquery", "listingsearchquery", "pricehistory", "listingsnapshot", "scan"):
        assert admin_client.get(f"/admin/listings/{model}/").status_code == 200


def test_lock_prevents_second_collector(search):
    import psycopg
    from django.db import connection
    from listings.management.commands.collect_listings import LOCK_ID
    params = connection.get_connection_params()
    with psycopg.connect(**params) as other:
        other.execute("SELECT pg_advisory_lock(%s)", [LOCK_ID])
        with patch("listings.management.commands.collect_listings.get_collector") as factory:
            with pytest.raises(CommandError, match="already running"):
                call_command("collect_listings", source="avito", mode="fast")
        factory.assert_not_called()
        other.execute("SELECT pg_advisory_unlock(%s)", [LOCK_ID])
    assert not Scan.objects.exists()


def test_block_stops_remaining_searches_and_keeps_partial_items(search, item):
    from collectors.base import CollectionError
    SearchQuery.objects.create(name="Other", source="avito", url=search.url)
    collector = AsyncMock()
    collector.__aenter__.return_value = collector
    collector.stop_requested = False

    async def blocked(*args, **kwargs):
        collector.stop_requested = True
        raise CollectionError("Avito blocked", CollectionResult([item], 1, 1))

    collector.collect.side_effect = blocked
    with patch("listings.management.commands.collect_listings.get_collector", return_value=collector):
        with pytest.raises(CommandError):
            call_command("collect_listings", source="avito", mode="fast", stdout=StringIO(), stderr=StringIO())
    collector.collect.assert_awaited_once()
    scan = Scan.objects.get()
    assert scan.status == "failed" and scan.new_items == 1
    collector.__aexit__.assert_awaited_once()


def test_manual_captcha_requires_visible_browser(search):
    with pytest.raises(CommandError, match="requires --headed"):
        call_command("collect_listings", source="avito", mode="fast", wait_for_captcha=300)
    assert not Scan.objects.exists()
