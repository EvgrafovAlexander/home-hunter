from django.urls import path

from . import views

urlpatterns = [
    path("", views.listing_feed, name="listing_feed"),
    path("hidden/", views.hidden_listing_feed, name="hidden_listing_feed"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("scans/", views.scan_statistics, name="scan_statistics"),
    path("map/", views.listing_map, name="listing_map"),
]
