from django.db import migrations


def dedupe_street_assignments(apps, schema_editor):
    StreetAssignment = apps.get_model("listings", "StreetAssignment")
    fields = ("street", "house_from", "house_to", "parity", "district_id", "microdistrict_id", "is_excluded")
    seen = set()
    for item in StreetAssignment.objects.order_by("id"):
        key = tuple(getattr(item, field) for field in fields)
        if key in seen:
            item.delete()
        else:
            seen.add(key)


class Migration(migrations.Migration):
    dependencies = [("listings", "0033_street_assignment_exclusions")]
    operations = [migrations.RunPython(dedupe_street_assignments, migrations.RunPython.noop)]
