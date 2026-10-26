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
    mobile_api_url = models.TextField(
        blank=True,
        default="",
        help_text="URL выдачи Mobile API Avito для того же набора фильтров.",
    )
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name


class NetworkRoute(models.Model):
    """Переиспользуемый сетевой маршрут для одного или нескольких профилей.

    Credentials stay outside the database.  ``secret_ref`` identifies the
    server-side secret or mounted key that supplies them at runtime.
    """

    class Kind(models.TextChoices):
        DIRECT = "direct", "Прямое подключение"
        SSH_SOCKS5 = "ssh_socks5", "SSH/SOCKS5"
        SERVER_PROXY = "server_proxy", "Серверный прокси"
        MOBILE_PROXY = "mobile_proxy", "Мобильный прокси"
        FREE_PROXY_POOL = "free_proxy_pool", "Бесплатный пул прокси"

    code = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    proxy_url = models.CharField(
        max_length=255, blank=True,
        help_text="Proxy URL without credentials, for example socks5://shared-socks-tunnel:1080",
    )
    endpoint = models.CharField(max_length=255, blank=True, help_text="Remote endpoint or provider name")
    secret_ref = models.CharField(max_length=120, blank=True, help_text="Reference to a server-side secret")
    healthcheck_url = models.URLField(blank=True)
    settings = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name", "pk")

    def __str__(self) -> str:
        return self.name


class AvitoSpfaCookie(models.Model):
    """Снимок cookies SPFA и его жизненный цикл для профиля Avito."""

    profile_code = models.CharField(max_length=80)
    route_code = models.CharField(max_length=80, blank=True)
    cookie_id = models.BigIntegerField()
    cookies = models.JSONField()
    user_agent = models.TextField(blank=True)
    fingerprint = models.JSONField(null=True, blank=True)
    mobile = models.BooleanField(default=False)
    status = models.CharField(max_length=16, default="active")
    received_at = models.DateTimeField(auto_now_add=True)
    refreshed_at = models.DateTimeField(auto_now=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=("profile_code", "status")), models.Index(fields=("cookie_id",))]


class AvitoSpfaRequest(models.Model):
    """Неизменяемый учёт каждого вызова платного SPFA API."""

    profile_code = models.CharField(max_length=80, blank=True)
    route_code = models.CharField(max_length=80, blank=True)
    operation = models.CharField(max_length=40)
    endpoint = models.CharField(max_length=120)
    request_hash = models.CharField(max_length=64)
    cookie_id = models.BigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=16, default="pending")
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_success = models.BooleanField(null=True, blank=True)
    error = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-started_at", "-pk")


class PollingProfile(models.Model):
    """Настройки опроса источника и режима, включая сетевой маршрут."""

    class Transport(models.TextChoices):
        BROWSER_HTML = "browser_html", "Chromium / HTML"
        MOBILE_API = "mobile_api", "Mobile API"

    class CookieStrategy(models.TextChoices):
        BROWSER_PROFILE = "browser_profile", "Профиль Chromium"
        OWN_COOKIES = "own_cookies", "Собственные cookies"
        SPFA = "spfa", "SPFA"

    class SessionMode(models.TextChoices):
        SETTINGS = "settings", "Из настроек площадки"
        TEMPORARY = "temporary", "Временный профиль браузера"
        PERSISTENT = "persistent", "Постоянный профиль браузера"

    class BlockPolicy(models.TextChoices):
        SETTINGS = "settings", "Из настроек площадки"
        COOLDOWN = "cooldown", "Остановить и включить cooldown"
        ROTATE_IP = "rotate_ip", "Сменить IP и повторить"
        STOP = "stop", "Остановить запуск"

    code = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=120)
    source = models.CharField(max_length=20, choices=SearchQuery.Source.choices)
    mode = models.CharField(max_length=10, choices=(
        ("all", "Все режимы"), ("fast", "Fast"), ("full", "Full"), ("detail", "Detail"),
    ), default="all")
    route = models.ForeignKey(NetworkRoute, on_delete=models.PROTECT, related_name="polling_profiles")
    transport = models.CharField(max_length=20, choices=Transport.choices, default=Transport.BROWSER_HTML)
    cookie_strategy = models.CharField(
        max_length=20, choices=CookieStrategy.choices, default=CookieStrategy.BROWSER_PROFILE,
    )
    session_mode = models.CharField(max_length=20, choices=SessionMode.choices, default=SessionMode.SETTINGS)
    block_policy = models.CharField(max_length=20, choices=BlockPolicy.choices, default=BlockPolicy.SETTINGS)
    enabled = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    settings = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("source", "mode", "name", "pk")
        indexes = [models.Index(fields=("source", "mode", "enabled"))]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_source_display()} / {self.mode})"


class SourcePollingProfile(models.Model):
    """Активный профиль опроса, общий для всех режимов источника."""

    source = models.CharField(max_length=20, choices=SearchQuery.Source.choices, unique=True)
    profile = models.ForeignKey(
        PollingProfile, on_delete=models.PROTECT, related_name="source_assignments",
    )
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.get_source_display()}: {self.profile.name}"


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
    districts = models.ManyToManyField(
        "District", through="MicrodistrictDistrict", related_name="all_microdistricts", blank=True,
    )
    aliases = models.JSONField(default=list, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("district__sort_order", "sort_order", "name")
        constraints = [models.UniqueConstraint(fields=("district", "name"), name="unique_district_microdistrict")]

    def __str__(self) -> str:
        return f"{self.district.name} · {self.name}"


class MicrodistrictDistrict(models.Model):
    microdistrict = models.ForeignKey(Microdistrict, on_delete=models.CASCADE, related_name="district_links")
    district = models.ForeignKey(District, on_delete=models.PROTECT, related_name="microdistrict_links")
    is_primary = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=("microdistrict", "district"), name="unique_microdistrict_district_link",
        )]


class MicrodistrictBoundary(models.Model):
    """Версия геометрии источника для классификации по координатам."""

    microdistrict = models.ForeignKey(
        Microdistrict, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="boundaries",
    )
    source = models.CharField(max_length=50)
    source_polygon_id = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    geometry = models.JSONField()
    bbox = models.JSONField(default=list, blank=True)
    geometry_hash = models.CharField(max_length=64)
    confidence = models.DecimalField(max_digits=4, decimal_places=3, default=0.980)
    priority = models.PositiveSmallIntegerField(default=0, help_text="Higher priority wins when active polygons overlap")
    is_active = models.BooleanField(default=True, db_index=True)
    retrieved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("source", "title", "source_polygon_id")
        constraints = [models.UniqueConstraint(
            fields=("source", "source_polygon_id"), name="unique_boundary_source_polygon",
        )]

    def __str__(self) -> str:
        return f"{self.source} · {self.title}"


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
    is_excluded = models.BooleanField(default=False, help_text="Исключить объявления этой улицы из выдачи и рыночной статистики")
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ("street", "house_from", "house_to")

    def __str__(self) -> str:
        return self.street


class ScoringPreference(models.Model):
    """Пользовательские настройки оценки, отделённые от общих рыночных данных."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="scoring_preference")
    min_area = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    max_area = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    min_kitchen_area = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    max_price = models.BigIntegerField(null=True, blank=True)
    preferred_rooms = models.JSONField(default=list, blank=True)
    floor_min = models.PositiveSmallIntegerField(null=True, blank=True)
    floor_max = models.PositiveSmallIntegerField(null=True, blank=True)
    preferred_districts = models.JSONField(default=list, blank=True)
    preferred_repair_types = models.JSONField(default=list, blank=True)
    prefer_photo = models.BooleanField(default=False)
    require_lift = models.BooleanField(default=False)
    require_balcony = models.BooleanField(default=False)
    prefer_furniture = models.BooleanField(default=False)
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


class GeoZone(models.Model):
    class Tier(models.TextChoices):
        A = "A", "Идеальная зона"
        B = "B", "Хорошая зона"
        C = "C", "Компромиссная зона"
        D = "D", "Не рассматриваю"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="geo_zones")
    name = models.CharField(max_length=120)
    tier = models.CharField(max_length=1, choices=Tier.choices)
    geometry = models.JSONField(help_text="GeoJSON Polygon или MultiPolygon")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("tier", "name", "id")
        constraints = [models.UniqueConstraint(fields=("user", "name"), name="unique_user_geo_zone_name")]

    def __str__(self):
        return f"{self.name} ({self.tier})"


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
    microdistrict_source = models.CharField(max_length=20, null=True, blank=True)
    microdistrict_confidence = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    microdistrict_polygon_id = models.PositiveIntegerField(null=True, blank=True)
    microdistrict_boundary = models.ForeignKey(
        "MicrodistrictBoundary", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="listings",
    )
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
    kitchen_area = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    living_area = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    ceiling_height = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    bathrooms_combined = models.PositiveSmallIntegerField(null=True, blank=True)
    bathrooms_separate = models.PositiveSmallIntegerField(null=True, blank=True)
    balconies_count = models.PositiveSmallIntegerField(null=True, blank=True)
    loggias_count = models.PositiveSmallIntegerField(null=True, blank=True)
    repair_type = models.CharField(max_length=100, null=True, blank=True)
    windows_view_type = models.CharField(max_length=100, null=True, blank=True)
    has_furniture = models.BooleanField(null=True, blank=True)
    passenger_lifts_count = models.PositiveSmallIntegerField(null=True, blank=True)
    cargo_lifts_count = models.PositiveSmallIntegerField(null=True, blank=True)
    has_ramp = models.BooleanField(null=True, blank=True)
    building_material_type = models.CharField(max_length=100, null=True, blank=True)
    parking_type = models.CharField(max_length=100, null=True, blank=True)
    has_garbage_chute = models.BooleanField(null=True, blank=True)
    publication_status = models.CharField(
        max_length=20, choices=PublicationStatus.choices,
        default=PublicationStatus.PUBLISHED, db_default=PublicationStatus.PUBLISHED, db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    search_queries = models.ManyToManyField(SearchQuery, through="ListingSearchQuery")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["source", "external_id"], name="unique_source_listing")]

    def __str__(self) -> str:
        return self.title


class TelegramPollingState(models.Model):
    key = models.CharField(max_length=50, unique=True, default="default")
    offset = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


from .models_reviews import (
    Consideration,
    GlobalListingHide,
    ListingReview,
    ListingReviewRevision,
    ReviewTag,
    TelegramReviewSession,
    UserListingHide,
)


class ListingSearchQuery(models.Model):
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="search_relations")
    search_query = models.ForeignKey(SearchQuery, on_delete=models.CASCADE, related_name="listing_relations")
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    is_active = models.BooleanField(default=True)
    missed_full_scans = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["listing", "search_query"], name="unique_listing_search")]


from .models_polling import (
    AvitoDetailPayload,
    AvitoDetailPollProgress,
    AvitoDetailPollState,
    CianDetailPayload,
    CianDetailPollProgress,
    CianDetailPollState,
    CianFullScanCheckpoint,
)


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
    profile_code = models.CharField(max_length=80, blank=True)
    route_code = models.CharField(max_length=80, blank=True)
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


class PollingDiagnosticJob(models.Model):
    """Разовый запуск опроса по выбранному профилю."""

    class Status(models.TextChoices):
        QUEUED = "queued", "В очереди"
        RUNNING = "running", "Выполняется"
        SUCCESS = "success", "Успешно"
        FAILED = "failed", "Ошибка"

    source = models.CharField(max_length=20, choices=SearchQuery.Source.choices)
    search_query = models.ForeignKey(SearchQuery, on_delete=models.PROTECT, related_name="polling_diagnostic_jobs")
    profile = models.ForeignKey("PollingProfile", on_delete=models.PROTECT, related_name="diagnostic_jobs")
    mode = models.CharField(max_length=10, choices=Scan.Mode.choices, default=Scan.Mode.FAST)
    scan = models.OneToOneField(Scan, on_delete=models.SET_NULL, null=True, blank=True, related_name="diagnostic_job")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED, db_index=True)
    log = models.TextField(blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = [models.Index(fields=("source", "status", "created_at"))]


class SourceHealthAlert(models.Model):
    source = models.CharField(max_length=20, choices=SearchQuery.Source.choices, unique=True)
    state = models.CharField(max_length=30)
    updated_at = models.DateTimeField(auto_now=True)


class CianDetailHealthAlert(models.Model):
    state = models.CharField(max_length=20, default="unknown")
    updated_at = models.DateTimeField(auto_now=True)


class SourcePollingControl(models.Model):
    """Переключатель состояния конкретного источника и режима сканирования."""

    source = models.CharField(max_length=20, choices=SearchQuery.Source.choices)
    mode = models.CharField(max_length=10, choices=Scan.Mode.choices, default=Scan.Mode.FAST)
    profile = models.ForeignKey(
        "PollingProfile", on_delete=models.PROTECT, null=True, blank=True, related_name="polling_controls",
    )
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


class TelegramListingAlert(models.Model):
    """Одна публикация объявления в Telegram-ленте."""

    class DeliveryStatus(models.TextChoices):
        AWAITING_GEOCODE = "awaiting_geocode", "Ожидает геокодирование"
        PENDING = "pending", "Ожидает отправки"
        SENDING = "sending", "Отправляется"
        SENT = "sent", "Отправлено"
        FAILED = "failed", "Ошибка отправки"
        SKIPPED = "skipped", "Пропущено фильтром"

    listing = models.OneToOneField(Listing, on_delete=models.CASCADE, related_name="telegram_alert")
    score = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    is_deal = models.BooleanField(default=False)
    reasons = models.JSONField(default=list)
    chat_id = models.CharField(max_length=100, blank=True)
    message_id = models.BigIntegerField(null=True, blank=True)
    message_kind = models.CharField(max_length=10, choices=(("text", "Текст"), ("photo", "Фото")), blank=True)
    message_text = models.TextField(blank=True)
    score_ready = models.BooleanField(default=False)
    score_updated_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivery_status = models.CharField(
        max_length=20, choices=DeliveryStatus.choices, default=DeliveryStatus.SENT, db_index=True,
    )
    delivery_attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True, db_index=True)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_delivery_error = models.TextField(blank=True)
    filter_reason = models.CharField(max_length=255, blank=True)
    filter_checked_at = models.DateTimeField(null=True, blank=True)


class TelegramPriceAlert(models.Model):
    """Одно Telegram-уведомление о конкретном изменении цены."""

    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="telegram_price_alerts")
    old_price = models.BigIntegerField()
    new_price = models.BigIntegerField()
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=["listing", "old_price", "new_price"], name="unique_telegram_price_alert",
        )]


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
