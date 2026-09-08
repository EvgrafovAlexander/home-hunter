import json

import pytest
from django.urls import reverse

from listings.models import Listing, ManualDomclickJob, SearchQuery


pytestmark = pytest.mark.django_db


def test_agent_claims_and_completes_manual_job(client, settings):
    settings.DOMCLICK_AGENT_TOKEN = "agent-token"
    search = SearchQuery.objects.create(name="Домклик вручную", source="domclick", url="https://ufa.domclick.ru/search?rooms=2")
    job = ManualDomclickJob.objects.create(search_query=search)
    headers = {"HTTP_X_HOME_HUNTER_AGENT_TOKEN": "agent-token"}

    response = client.post("/api/domclick-agent/jobs/next/", **headers)
    assert response.status_code == 200
    assert response.json()["job"]["id"] == job.pk
    job.refresh_from_db()
    assert job.status == "running" and job.scan_id

    response = client.post(f"/api/domclick-agent/jobs/{job.pk}/complete/", data=json.dumps({
        "status": "success", "pages_scanned": 1, "items_seen": 1,
        "listings": [{"external_id": "domclick-1", "url": "https://ufa.domclick.ru/card/1",
                      "title": "Квартира", "price": 6_000_000, "price_per_sqm": 100_000,
                      "rooms": 2, "area": "60.00", "address": "Уфа, улица Тестовая, 1"}],
    }), content_type="application/json", **headers)
    assert response.status_code == 200
    job.refresh_from_db()
    assert job.status == "success" and job.scan.status == "success"
    assert Listing.objects.get(source="domclick", external_id="domclick-1").price == 6_000_000


def test_agent_rejects_invalid_token(client, settings):
    settings.DOMCLICK_AGENT_TOKEN = "agent-token"
    assert client.post("/api/domclick-agent/jobs/next/").status_code == 403
