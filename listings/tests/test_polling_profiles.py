import pytest

from collectors.routing import resolve_profile
from listings.models import NetworkRoute, PollingDiagnosticJob, PollingProfile, SearchQuery, SourcePollingProfile


@pytest.mark.django_db
def test_source_profile_is_inherited_by_all_polling_modes():
    route = NetworkRoute.objects.create(
        code="mobile_test", name="Mobile test", kind="mobile_proxy", proxy_url="socks5://mobile:1080",
    )
    profile = PollingProfile.objects.create(
        code="avito_mobile", name="Avito mobile", source="avito", mode="all", route=route,
        session_mode="temporary", block_policy="cooldown",
    )
    SourcePollingProfile.objects.update_or_create(source="avito", defaults={"profile": profile, "enabled": True})

    for mode in ("fast", "full"):
        resolved = resolve_profile("avito", mode)
        assert resolved.code == "avito_mobile"
        assert resolved.mode == mode
        assert resolved.route.code == "mobile_test"


@pytest.mark.django_db
def test_avito_uses_mobile_proxy_from_environment(monkeypatch):
    route = NetworkRoute.objects.create(
        code="shared_test", name="Shared test", kind="ssh_socks5", proxy_url="socks5://shared:1080",
    )
    PollingProfile.objects.create(
        code="avito_env_test", name="Avito env", source="avito", mode="all", route=route,
        is_default=True,
    )
    monkeypatch.setattr("django.conf.settings.MOBILE_PROXY_URL", "http://proxy.example:8080")
    monkeypatch.setattr("django.conf.settings.MOBILE_PROXY_HEALTHCHECK_URL", "https://api.ipify.org")

    resolved = resolve_profile("avito", "fast")

    assert resolved.route.kind == "mobile_proxy"
    assert resolved.route.code == "env_mobile_proxy"
    assert resolved.route.secret_ref == "MOBILE_PROXY"


@pytest.mark.django_db
def test_explicit_profile_can_select_a_source_wide_profile():
    route = NetworkRoute.objects.create(
        code="server_test", name="Server test", kind="server_proxy", proxy_url="http://server:8080",
    )
    profile = PollingProfile.objects.create(
        code="cian_server", name="CIAN server", source="cian", mode="all", route=route,
    )

    resolved = resolve_profile("cian", "detail", "cian_server")
    assert resolved.code == "cian_server"
    assert resolved.mode == "detail"
    assert resolved.route.kind == "server_proxy"


@pytest.mark.django_db
def test_staff_ui_assigns_one_profile_to_the_source(admin_client):
    route = NetworkRoute.objects.create(
        code="ui_test", name="UI test", kind="server_proxy", proxy_url="http://ui:8080",
    )
    profile = PollingProfile.objects.create(
        code="domclick_ui", name="Domclick UI", source="domclick", mode="all", route=route,
    )

    response = admin_client.get("/polling-profiles/")
    assert response.status_code == 200
    assert "Chromium / HTML" in response.content.decode()
    assert "Профиль Chromium" in response.content.decode()
    response = admin_client.post("/polling-profiles/", {
        "action": "assign_source_profile", "source": "domclick", "profile_id": profile.pk,
    })
    assert response.status_code == 302
    assert SourcePollingProfile.objects.get(source="domclick").profile_id == profile.pk


@pytest.mark.django_db
def test_staff_ui_queues_one_off_diagnostic_without_changing_assignment(admin_client):
    route = NetworkRoute.objects.create(
        code="diag_test", name="Diagnostic route", kind="free_proxy_pool", endpoint="https://proxy.test/list",
    )
    profile = PollingProfile.objects.create(
        code="avito_diag", name="Avito diagnostic", source="avito", mode="all", route=route,
    )
    search = SearchQuery.objects.create(name="Avito test search", source="avito", url="https://avito.test/search")
    previous_assignment = SourcePollingProfile.objects.filter(source="avito").values_list("profile_id", flat=True).first()

    response = admin_client.post("/polling-profiles/", {
        "action": "test_profile", "source": "avito", "profile_id": profile.pk,
    })

    assert response.status_code == 302
    job = PollingDiagnosticJob.objects.get()
    assert job.profile_id == profile.pk
    assert job.search_query_id == search.pk
    assert job.status == PollingDiagnosticJob.Status.QUEUED
    assert SourcePollingProfile.objects.filter(source="avito").values_list("profile_id", flat=True).first() == previous_assignment
