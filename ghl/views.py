from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import sync_event_participants, sync_user_schedule, upsert_ghl_contact


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
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

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
