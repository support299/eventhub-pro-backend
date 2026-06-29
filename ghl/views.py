from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.conf import settings

from accounts.permissions import IsStaff

from .services import sync_event_participants, sync_user_schedule, upsert_ghl_contact
from .webhook_handlers import handle_ghl_user_webhook
from .webhook_verify import verify_ghl_webhook


class UpsertContactView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            contact = upsert_ghl_contact(
                email=request.data["email"],
                full_name=request.data.get("fullName"),
                phone=request.data.get("phone"),
                custom_fields=request.data.get("customFields"),
                tags=request.data.get("tags"),
            )
            return Response({"ok": True, "contact": contact})
        except Exception as exc:
            return Response({"ok": False, "error": str(exc)}, status=500)


class SyncUsersView(APIView):
    permission_classes = [IsStaff]

    def post(self, request):
        user_ids = request.data.get("user_ids", [])
        tz = request.data.get("tz")
        event_id = request.data.get("event_id")
        synced = failed = 0
        for uid in user_ids:
            try:
                sync_user_schedule(uid, tz, event_id)
                synced += 1
            except Exception as exc:
                print(f"[GHL] sync failed for {uid}: {exc}")
                failed += 1
        return Response({"ok": True, "synced": synced, "failed": failed})


class SyncEventView(APIView):
    permission_classes = [IsStaff]

    def post(self, request):
        event_id = request.data.get("event_id")
        tz = request.data.get("tz")
        if not event_id:
            return Response({"detail": "event_id required"}, status=400)
        try:
            result = sync_event_participants(event_id, tz)
            return Response({"ok": True, **result})
        except Exception as exc:
            return Response({"ok": False, "error": str(exc)}, status=500)


class GhlWebhookView(APIView):
    """Receive GHL user lifecycle webhooks (UserCreate / UserUpdate / UserDelete)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        raw_body = request.body
        verify = getattr(settings, "GHL_WEBHOOK_VERIFY", not settings.DEBUG)
        if verify:
            ghl_sig = request.headers.get("X-GHL-Signature")
            legacy_sig = request.headers.get("X-WH-Signature")
            if not verify_ghl_webhook(raw_body, ghl_sig, legacy_sig):
                return Response({"detail": "Invalid webhook signature"}, status=401)

        try:
            result = handle_ghl_user_webhook(request.data)
            print(f"[GHL webhook] {result}")
            return Response(result)
        except Exception as exc:
            print(f"[GHL webhook] failed: {exc}")
            return Response({"ok": False, "error": str(exc)}, status=500)
