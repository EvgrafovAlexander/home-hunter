from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from django.core.management import call_command

from collectors.cian.collector import CianCollector
from collectors.tests.test_cian import URL, html
from listings.models import Listing, ListingSnapshot, PriceHistory, Scan, SearchQuery

pytestmark = pytest.mark.django_db(transaction=True)


def test_cian_command_persists_and_deduplicates(settings, tmp_path):
    settings.CIAN_STATE_DIR=tmp_path/'cian'
    settings.CIAN_PROXY_URL='socks5://localhost:1080'
    settings.CIAN_PAGE_DELAY_SECONDS=0.01
    SearchQuery.objects.create(source='cian',name='CIAN fixture',url=URL)
    def factory(source, **kwargs):
        assert source=='cian' and kwargs['headless'] is False
        c=CianCollector(**kwargs)
        c._start=AsyncMock()
        c._load_page=AsyncMock(return_value=html(newest=True,total=641,next_page=2))
        return c
    with patch('listings.management.commands.collect_listings.get_collector',side_effect=factory):
        for _ in range(2):
            call_command('collect_listings',source='cian',mode='fast',stdout=StringIO())
    assert Listing.objects.filter(source='cian').count()==1
    assert PriceHistory.objects.count()==1 and ListingSnapshot.objects.count()==1
    assert Scan.objects.filter(status='success').count()==2
    assert list(Scan.objects.order_by('pk').values_list('new_items',flat=True))==[1,0]
