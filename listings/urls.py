from django.urls import path

from . import agent_views, views

urlpatterns = [
    path("", views.listing_feed, name="listing_feed"),
    path("hidden/", views.hidden_listing_feed, name="hidden_listing_feed"),
    path("shortlist/", views.shortlist, name="shortlist"),
    path("listings/<int:listing_id>/", views.listing_detail, name="listing_detail"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("scans/", views.scan_statistics, name="scan_statistics"),
    path("data-quality/", views.data_quality, name="data_quality"),
    path("directory/", views.location_directory, name="location_directory"),
    path("map/", views.listing_map, name="listing_map"),
    path("api/domclick-agent/jobs/next/", agent_views.next_domclick_job),
    path("api/domclick-agent/jobs/<int:job_id>/complete/", agent_views.complete_domclick_job),
]
