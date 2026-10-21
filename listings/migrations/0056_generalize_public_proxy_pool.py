from django.db import migrations


ROUTE_OLD = "avito_free_proxy_pool"
ROUTE_NEW = "public_proxy_pool"
PROFILE_OLD = "avito_free_proxy_pool"
PROFILE_NEW = "public_proxy_pool_avito"
PROFILE_SOURCES = ("cian", "domclick")


def generalize_pool(apps, schema_editor):
    NetworkRoute = apps.get_model("listings", "NetworkRoute")
    PollingProfile = apps.get_model("listings", "PollingProfile")
    route = NetworkRoute.objects.filter(code=ROUTE_OLD).first()
    if route is None:
        return
    route.code = ROUTE_NEW
    route.name = "Публичный пул прокси"
    route.save(update_fields=("code", "name", "updated_at"))

    profile = PollingProfile.objects.filter(code=PROFILE_OLD).first()
    if profile is not None:
        profile.code = PROFILE_NEW
        profile.name = "Публичный пул прокси"
        profile.save(update_fields=("code", "name", "updated_at"))

    for source in PROFILE_SOURCES:
        PollingProfile.objects.get_or_create(
            code=f"public_proxy_pool_{source}",
            defaults={
                "name": "Публичный пул прокси",
                "source": source,
                "mode": "all",
                "route": route,
                "session_mode": "temporary",
                "block_policy": "cooldown",
                "enabled": True,
                "is_default": False,
            },
        )


def restore_avito_pool(apps, schema_editor):
    NetworkRoute = apps.get_model("listings", "NetworkRoute")
    PollingProfile = apps.get_model("listings", "PollingProfile")
    PollingProfile.objects.filter(code__in=("public_proxy_pool_cian", "public_proxy_pool_domclick")).delete()
    profile = PollingProfile.objects.filter(code=PROFILE_NEW).first()
    if profile is not None:
        profile.code = PROFILE_OLD
        profile.name = "АВИТО: бесплатный пул прокси"
        profile.save(update_fields=("code", "name", "updated_at"))
    route = NetworkRoute.objects.filter(code=ROUTE_NEW).first()
    if route is not None:
        route.code = ROUTE_OLD
        route.name = "АВИТО: бесплатный пул прокси"
        route.save(update_fields=("code", "name", "updated_at"))


class Migration(migrations.Migration):
    dependencies = [("listings", "0055_alter_networkroute_kind")]

    operations = [migrations.RunPython(generalize_pool, restore_avito_pool)]
