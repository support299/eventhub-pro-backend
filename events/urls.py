from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import DashboardView, EventViewSet, OccurrenceViewSet

router = DefaultRouter()
router.register(r"events", EventViewSet, basename="events")
router.register(r"occurrences", OccurrenceViewSet, basename="occurrences")

urlpatterns = [
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("", include(router.urls)),
]
