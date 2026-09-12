from django.contrib import admin
from .models import (Consideration, District, GlobalListingHide, Listing, ListingReview, ListingReviewRevision,
                     ListingSearchQuery, ListingSnapshot, Microdistrict, PriceHistory, ReviewTag, Scan, SearchQuery,
                     StreetAssignment, UserListingHide)


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
    list_filter = ("source", "rooms", "district", "is_visible", "is_active")
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


@admin.register(ListingSearchQuery)
class ListingSearchQueryAdmin(admin.ModelAdmin):
    list_display = ("listing", "search_query", "is_active", "missed_full_scans", "last_seen_at")
    list_filter = ("is_active", "search_query__source")
    search_fields = ("listing__title", "listing__external_id", "search_query__name")


admin.site.register(PriceHistory)
admin.site.register(ListingSnapshot)
admin.site.register(District)
admin.site.register(Microdistrict)
admin.site.register(StreetAssignment)
admin.site.register(ReviewTag)
admin.site.register(UserListingHide)
admin.site.register(GlobalListingHide)
admin.site.register(Consideration)


@admin.register(ListingReview)
class ListingReviewAdmin(admin.ModelAdmin):
    list_display = ("listing", "author", "rating", "decision", "updated_at")
    list_filter = ("decision", "rating")
    search_fields = ("listing__title", "listing__external_id", "author__username", "comment")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ListingReviewRevision)
class ListingReviewRevisionAdmin(admin.ModelAdmin):
    list_display = ("review", "rating", "decision", "created_at")
    readonly_fields = tuple(field.name for field in ListingReviewRevision._meta.fields)
