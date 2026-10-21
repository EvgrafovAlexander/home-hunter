from django.db import migrations


def use_geonix(apps, schema_editor):
    NetworkRoute = apps.get_model("listings", "NetworkRoute")
    route = NetworkRoute.objects.filter(code="public_proxy_pool").first()
    if route is None:
        return
    route.endpoint = "https://free.geonix.com/api/front/main/proxy/list"
    route.settings = {
        **(route.settings or {}),
        "source_url": "https://free.geonix.com/api/front/main/proxy/list",
        "source_method": "POST",
        "source_payload": {"country": "Russia", "count": 50},
        "default_scheme": "http",
        "scheme_by_port": {"1080": "socks5", "1081": "socks5", "1085": "socks5", "9050": "socks5"},
        "max_candidates": 20,
        "max_attempts": 8,
        "require_high_anonymity": False,
    }
    route.save(update_fields=("endpoint", "settings", "updated_at"))


def restore_spys(apps, schema_editor):
    NetworkRoute = apps.get_model("listings", "NetworkRoute")
    route = NetworkRoute.objects.filter(code="public_proxy_pool").first()
    if route is None:
        return
    route.endpoint = "https://spys.one/proxys/RU/"
    route.settings = {
        **(route.settings or {}),
        "source_url": "https://spys.one/proxys/RU/",
        "source_method": "GET",
        "source_payload": None,
        "max_candidates": 8,
        "max_attempts": 3,
        "require_high_anonymity": True,
    }
    route.save(update_fields=("endpoint", "settings", "updated_at"))


class Migration(migrations.Migration):
    dependencies = [("listings", "0056_generalize_public_proxy_pool")]
    operations = [migrations.RunPython(use_geonix, restore_spys)]
