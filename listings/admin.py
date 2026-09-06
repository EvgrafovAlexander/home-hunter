from django.contrib import admin
from .models import Listing, ListingSearchQuery, ListingSnapshot, PriceHistory, Scan, SearchQuery


class PriceHistoryInline(admin.TabularInline):
    model = PriceHistory
    extra = 0
    readonly_fields = ("price", "observed_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    list_display = ("title", "source", "price", "rooms", "is_active", "first_seen_at", "last_seen_at")
    search_fields = ("title", "address", "external_id")
    list_filter = ("source", "rooms", "district", "is_active")
    inlines = [PriceHistoryInline]


@admin.register(SearchQuery)
class SearchQueryAdmin(admin.ModelAdmin):
    list_display = ("name", "source", "enabled", "url")
    list_filter = ("source", "enabled")


@admin.register(Scan)
class ScanAdmin(admin.ModelAdmin):
    list_display = ("search_query", "status", "mode", "source", "started_at", "finished_at",
                    "pages_scanned", "items_seen", "unique_items_seen", "new_items",
                    "updated_items", "price_changes")
    list_filter = ("status", "mode", "source")
    readonly_fields = tuple(field.name for field in Scan._meta.fields)


admin.site.register(ListingSearchQuery)
admin.site.register(PriceHistory)
admin.site.register(ListingSnapshot)
