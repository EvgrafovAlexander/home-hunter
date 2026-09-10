from django.conf import settings
from django.db import models
from django.utils import timezone


class SearchQuery(models.Model):
    class Source(models.TextChoices):
        AVITO = "avito", "Avito"
        CIAN = "cian", "CIAN"
        DOMCLICK = "domclick", "Домклик"

    name = models.CharField(max_length=255)
    source = models.CharField(max_length=20, choices=Source.choices)
    url = models.TextField()
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name


class District(models.Model):
    name = models.CharField(max_length=100, unique=True)
    aliases = models.JSONField(default=list, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "name")

    def __str__(self) -> str:
        return self.name


class Microdistrict(models.Model):
    name = models.CharField(max_length=100)
    district = models.ForeignKey(District, on_delete=models.PROTECT, related_name="microdistricts")
    aliases = models.JSONField(default=list, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("district__sort_order", "sort_order", "name")
        constraints = [models.UniqueConstraint(fields=("district", "name"), name="unique_district_microdistrict")]

    def __str__(self) -> str:
        return f"{self.district.name} · {self.name}"


class StreetAssignment(models.Model):
    class Parity(models.TextChoices):
        ANY = "any", "Любая"
        ODD = "odd", "Нечётные"
        EVEN = "even", "Чётные"

    street = models.CharField(max_length=255, db_index=True)
    house_from = models.PositiveIntegerField(null=True, blank=True)
    house_to = models.PositiveIntegerField(null=True, blank=True)
    parity = models.CharField(max_length=10, choices=Parity.choices, default=Parity.ANY)
    district = models.ForeignKey(District, on_delete=models.PROTECT, related_name="street_assignments")
    microdistrict = models.ForeignKey(Microdistrict, on_delete=models.PROTECT, related_name="street_assignments",
                                      null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ("street", "house_from", "house_to")

    def __str__(self) -> str:
        return self.street


class ScoringPreference(models.Model):
    """Personal scoring settings, deliberately separate from shared market data."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="scoring_preference")
    min_area = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    floor_min = models.PositiveSmallIntegerField(null=True, blank=True)
    floor_max = models.PositiveSmallIntegerField(null=True, blank=True)
    preferred_districts = models.JSONField(default=list, blank=True)
    prefer_photo = models.BooleanField(default=False)
    market_weight = models.PositiveSmallIntegerField(default=45)
    freshness_weight = models.PositiveSmallIntegerField(default=20)
    data_weight = models.PositiveSmallIntegerField(default=15)
    floor_weight = models.PositiveSmallIntegerField(default=10)
    price_history_weight = models.PositiveSmallIntegerField(default=10)
    preference_weight = models.PositiveSmallIntegerField(default=20)
    location_weight = models.PositiveSmallIntegerField(default=35)
    condition_weight = models.PositiveSmallIntegerField(default=25)
    use_ufa_target_zones = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)


class Listing(models.Model):
    class PublicationStatus(models.TextChoices):
        PUBLISHED = "published", "Опубликовано"
        UNAVAILABLE = "unavailable", "Снято / недоступно"

    source = models.CharField(max_length=20, choices=SearchQuery.Source.choices)
    external_id = models.CharField(max_length=100)
    url = models.TextField()
    title = models.CharField(max_length=1000)
    description = models.TextField(blank=True, null=True)
    price = models.BigIntegerField(null=True, blank=True, db_index=True)
    price_per_sqm = models.BigIntegerField(null=True, blank=True)
    rooms = models.IntegerField(null=True, blank=True, db_index=True)
    area = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, db_index=True)
    floor = models.IntegerField(null=True, blank=True)
    floors_total = models.IntegerField(null=True, blank=True)
    built_year = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)
    address = models.TextField(null=True, blank=True)
    district = models.CharField(max_length=255, null=True, blank=True)
    microdistrict = models.CharField(max_length=255, null=True, blank=True)
    address_override = models.TextField(null=True, blank=True)
    district_override = models.CharField(max_length=255, null=True, blank=True)
    microdistrict_override = models.CharField(max_length=255, null=True, blank=True)
    district_ref = models.ForeignKey(District, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="listings")
    microdistrict_ref = models.ForeignKey(Microdistrict, null=True, blank=True, on_delete=models.SET_NULL,
                                          related_name="listings")
    location_source = models.CharField(max_length=20, default="parser", db_default="parser")
    is_visible = models.BooleanField(default=True, db_default=True, db_index=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    geocode_status = models.CharField(max_length=20, null=True, blank=True)
    geocoded_at = models.DateTimeField(null=True, blank=True)
    published_text = models.CharField(max_length=255, null=True, blank=True)
    image_url = models.TextField(null=True, blank=True)
    first_seen_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_seen_at = models.DateTimeField(default=timezone.now, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    publication_status = models.CharField(
        max_length=20, choices=PublicationStatus.choices,
        default=PublicationStatus.PUBLISHED, db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    search_queries = models.ManyToManyField(SearchQuery, through="ListingSearchQuery")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["source", "external_id"], name="unique_source_listing")]

    def __str__(self) -> str:
        return self.title


class ListingSearchQuery(models.Model):
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="search_relations")
    search_query = models.ForeignKey(SearchQuery, on_delete=models.CASCADE, related_name="listing_relations")
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    is_active = models.BooleanField(default=True)
    missed_full_scans = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["listing", "search_query"], name="unique_listing_search")]


class CianFullScanCheckpoint(models.Model):
    search_query = models.OneToOneField(SearchQuery, on_delete=models.CASCADE, related_name="cian_full_checkpoint")
    search_url = models.TextField()
    next_page = models.PositiveIntegerField(default=1)
    expected_total = models.PositiveIntegerField(null=True, blank=True)
    external_ids = models.JSONField(default=list)
    cycle_started_at = models.DateTimeField(default=timezone.now)
    total_changes = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class CianDetailPollState(models.Model):
    class Status(models.TextChoices):
        UNKNOWN = "unknown", "Не проверено"
        PUBLISHED = "published", "Активно"
        UNAVAILABLE = "unavailable", "Недоступно"

    listing = models.OneToOneField(Listing, on_delete=models.CASCADE, related_name="cian_detail_state")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UNKNOWN)
    last_checked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_available_at = models.DateTimeField(null=True, blank=True)
    first_unavailable_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    detail_data = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class CianDetailPollProgress(models.Model):
    """Persistent state of one fair, full pass through active CIAN listings."""

    source = models.CharField(max_length=20, unique=True, default=SearchQuery.Source.CIAN)
    cycle_started_at = models.DateTimeField(default=timezone.now)
    cycle_total = models.PositiveIntegerField(default=0)
    completed_listing_ids = models.JSONField(default=list, blank=True)
    current_listing = models.ForeignKey(
        Listing, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    current_started_at = models.DateTimeField(null=True, blank=True)
    last_completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class PriceHistory(models.Model):
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="price_history")
    price = models.BigIntegerField(null=True, blank=True)
    observed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-observed_at"]


class ListingSnapshot(models.Model):
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="snapshots")
    observed_at = models.DateTimeField(default=timezone.now)
    data = models.JSONField()


class Scan(models.Model):
    class Mode(models.TextChoices):
        FAST = "fast", "Fast"
        FULL = "full", "Full"

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    search_query = models.ForeignKey(SearchQuery, on_delete=models.CASCADE, related_name="scans")
    source = models.CharField(max_length=20, choices=SearchQuery.Source.choices)
    mode = models.CharField(max_length=10, choices=Mode.choices)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    pages_scanned = models.PositiveIntegerField(default=0)
    items_seen = models.PositiveIntegerField(default=0)
    unique_items_seen = models.PositiveIntegerField(default=0)
    new_items = models.PositiveIntegerField(default=0)
    updated_items = models.PositiveIntegerField(default=0)
    price_changes = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RUNNING)
    error = models.TextField(blank=True)


class SourceHealthAlert(models.Model):
    source = models.CharField(max_length=20, choices=SearchQuery.Source.choices, unique=True)
    state = models.CharField(max_length=30)
    updated_at = models.DateTimeField(auto_now=True)


class SourcePollingControl(models.Model):
    """Switch for a particular source and scan mode."""

    source = models.CharField(max_length=20, choices=SearchQuery.Source.choices)
    mode = models.CharField(max_length=10, choices=Scan.Mode.choices, default=Scan.Mode.FAST)
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["source", "mode"], name="unique_source_polling_mode")]


class DealAlert(models.Model):
    listing = models.OneToOneField(Listing, on_delete=models.CASCADE, related_name="deal_alert")
    discount_percent = models.IntegerField()
    market_reference = models.CharField(max_length=100)
    sample_size = models.PositiveIntegerField()
    sent_at = models.DateTimeField(auto_now_add=True)


class ManualDomclickJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Ожидает ноутбук"
        RUNNING = "running", "Выполняется на ноутбуке"
        SUCCESS = "success", "Готово"
        FAILED = "failed", "Ошибка"

    search_query = models.ForeignKey(SearchQuery, on_delete=models.PROTECT, related_name="manual_domclick_jobs")
    scan = models.OneToOneField(Scan, on_delete=models.SET_NULL, null=True, blank=True, related_name="manual_domclick_job")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
