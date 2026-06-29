from django.urls import path
from .views import GhlWebhookView, SyncEventView, SyncUsersView, UpsertContactView

urlpatterns = [
    path("ghl/upsert-contact/", UpsertContactView.as_view(), name="ghl_upsert_contact"),
    path("ghl/sync-users/", SyncUsersView.as_view(), name="ghl_sync_users"),
    path("ghl/sync-event/", SyncEventView.as_view(), name="ghl_sync_event"),
    path("ghl/webhook/", GhlWebhookView.as_view(), name="ghl_webhook"),
]
