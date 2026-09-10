from django.db import migrations, models


def mark_known_unavailable(apps, schema_editor):
    Listing = apps.get_model("listings", "Listing")
    CianDetailPollState = apps.get_model("listings", "CianDetailPollState")
    unavailable_ids = CianDetailPollState.objects.filter(status="unavailable").values_list("listing_id", flat=True)
    Listing.objects.filter(pk__in=unavailable_ids).update(publication_status="unavailable", is_active=False)
    Listing.objects.filter(is_active=False).exclude(pk__in=unavailable_ids).update(publication_status="unavailable")


class Migration(migrations.Migration):
    dependencies = [
        ("listings", "0026_ciandetailpollprogress"),
    ]

    operations = [
        migrations.AddField(
            model_name="listing",
            name="publication_status",
            field=models.CharField(choices=[("published", "Опубликовано"), ("unavailable", "Снято / недоступно")], db_index=True, default="published", max_length=20),
        ),
        migrations.RunPython(mark_known_unavailable, migrations.RunPython.noop),
    ]
