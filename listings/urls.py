from django.urls import path

from . import views

urlpatterns = [
    path("", views.listing_feed, name="listing_feed"),
    path("dashboard/", views.dashboard, name="dashboard"),
]
