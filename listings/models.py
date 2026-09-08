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


class Listing(models.Model):
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
    address = models.TextField(null=True, blank=True)
    district = models.CharField(max_length=255, null=True, blank=True)
    microdistrict = models.CharField(max_length=255, null=True, blank=True)
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

    class Meta:
        constraints = [models.UniqueConstraint(fields=["listing", "search_query"], name="unique_listing_search")]


class CianFullScanCheckpoint(models.Model):
    search_query = models.OneToOneField(SearchQuery, on_delete=models.CASCADE, related_name="cian_full_checkpoint")
    search_url = models.TextField()
    next_page = models.PositiveIntegerField(default=1)
    expected_total = models.PositiveIntegerField(null=True, blank=True)
    external_ids = models.JSONField(default=list)
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
