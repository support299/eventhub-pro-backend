from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet

from accounts.models import User, UserRole
from accounts.permissions import IsAdmin, IsStaff

from .models import Attendance, Event, EventAttendee, EventHost, EventOccurrence
from .serializers import (
    AttendanceSerializer,
    EventDetailSerializer,
    EventListSerializer,
    OccurrenceSerializer,
)


def _is_staff(user):
    return user.roles.filter(role__in=["admin", "trainer"]).exists()


def _is_admin(user):
    return user.roles.filter(role="admin").exists()


class EventViewSet(ViewSet):
    permission_classes = [IsAuthenticated]

    def list(self, request):
        user = request.user
        if _is_staff(user):
            events = Event.objects.all()
        else:
            attendee_ids = EventAttendee.objects.filter(user=user).values_list("event_id", flat=True)
            host_ids = EventHost.objects.filter(user=user).values_list("event_id", flat=True)
            primary_ids = Event.objects.filter(host=user).values_list("id", flat=True)
            all_ids = set(list(attendee_ids) + list(host_ids) + list(primary_ids))
            events = Event.objects.filter(id__in=all_ids)

        events = events.select_related("host").prefetch_related("occurrences").order_by("-created_at")
        return Response(EventListSerializer(events, many=True).data)

    def create(self, request):
        if not _is_admin(request.user):
            return Response({"detail": "Admin only"}, status=status.HTTP_403_FORBIDDEN)

        data = request.data
        host_id = data.get("host_id") or str(request.user.id)
        try:
            host = User.objects.get(id=host_id)
        except User.DoesNotExist:
            return Response({"detail": "Host not found"}, status=status.HTTP_400_BAD_REQUEST)

        event = Event.objects.create(
            title=data["title"],
            description=data.get("description") or None,
            host=host,
            created_by=request.user,
            online_location=data.get("online_location") or None,
            physical_location=data.get("physical_location") or None,
            duration_minutes=data.get("duration_minutes", 60),
            timezone=data.get("timezone", "UTC"),
            recurrence=data.get("recurrence"),
        )

        host_ids = data.get("host_ids") or [host_id]
        for uid in host_ids:
            try:
                EventHost.objects.get_or_create(event=event, user_id=uid)
            except Exception:
                pass

        for occ_data in data.get("occurrences", []):
            EventOccurrence.objects.create(
                event=event,
                scheduled_at=occ_data["scheduled_at"],
                duration_minutes=occ_data.get("duration_minutes") or event.duration_minutes,
            )

        return Response({"id": str(event.id)}, status=status.HTTP_201_CREATED)

    def retrieve(self, request, pk=None):
        try:
            event = Event.objects.get(pk=pk)
        except Event.DoesNotExist:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        occurrences = event.occurrences.all()
        occ_ids = occurrences.values_list("id", flat=True)
        attendees = event.attendees.all()
        hosts = event.hosts.all()
        attendance = Attendance.objects.filter(occurrence_id__in=occ_ids)

        return Response({
            **EventDetailSerializer(event).data,
            "occurrences": OccurrenceSerializer(occurrences, many=True).data,
            "attendees": [{"user_id": str(a.user_id)} for a in attendees],
            "hosts": [{"user_id": str(h.user_id)} for h in hosts],
            "attendance": AttendanceSerializer(attendance, many=True).data,
        })

    def partial_update(self, request, pk=None):
        if not _is_admin(request.user):
            return Response({"detail": "Admin only"}, status=status.HTTP_403_FORBIDDEN)
        try:
            event = Event.objects.get(pk=pk)
        except Event.DoesNotExist:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        data = request.data
        for field in ["title", "description", "online_location", "physical_location", "duration_minutes", "timezone", "recurrence"]:
            if field in data:
                setattr(event, field, data[field] or None if field in ["description", "online_location", "physical_location"] else data[field])
        if "host_id" in data and data["host_id"]:
            event.host_id = data["host_id"]
        event.save()

        if data.get("regenerate_occurrences"):
            now = timezone.now()
            event.occurrences.filter(scheduled_at__gte=now).delete()
            for occ_data in data.get("new_occurrences", []):
                EventOccurrence.objects.create(
                    event=event,
                    scheduled_at=occ_data["scheduled_at"],
                    duration_minutes=data.get("duration_minutes") or event.duration_minutes,
                )

        return Response({"ok": True})

    def destroy(self, request, pk=None):
        if not _is_admin(request.user):
            return Response({"detail": "Admin only"}, status=status.HTTP_403_FORBIDDEN)
        try:
            event = Event.objects.get(pk=pk)
        except Event.DoesNotExist:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        event.delete()
        return Response({"ok": True})

    @action(detail=False, methods=["post"], url_path="bulk-delete")
    def bulk_delete(self, request):
        if not _is_admin(request.user):
            return Response({"detail": "Admin only"}, status=status.HTTP_403_FORBIDDEN)
        ids = request.data.get("ids", [])
        Event.objects.filter(id__in=ids).delete()
        return Response({"ok": True})

    @action(detail=False, methods=["patch"], url_path="bulk-update-locations")
    def bulk_update_locations(self, request):
        if not _is_admin(request.user):
            return Response({"detail": "Admin only"}, status=status.HTTP_403_FORBIDDEN)
        ids = request.data.get("ids", [])
        patch = {}
        if "physical_location" in request.data:
            patch["physical_location"] = request.data["physical_location"] or None
        if "online_location" in request.data:
            patch["online_location"] = request.data["online_location"] or None
        if patch:
            Event.objects.filter(id__in=ids).update(**patch)
        return Response({"ok": True})

    # ── Occurrences ───────────────────────────────────────────────────────────

    @action(detail=True, methods=["post"], url_path="occurrences")
    def add_occurrence(self, request, pk=None):
        if not _is_admin(request.user):
            return Response({"detail": "Admin only"}, status=status.HTTP_403_FORBIDDEN)
        try:
            event = Event.objects.get(pk=pk)
        except Event.DoesNotExist:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        occ = EventOccurrence.objects.create(
            event=event,
            scheduled_at=request.data["scheduled_at"],
            duration_minutes=request.data.get("duration_minutes") or event.duration_minutes,
        )
        return Response(OccurrenceSerializer(occ).data, status=status.HTTP_201_CREATED)

    # ── Attendees ─────────────────────────────────────────────────────────────

    @action(detail=True, methods=["post"], url_path="attendees")
    def add_attendees(self, request, pk=None):
        if not _is_staff(request.user):
            return Response({"detail": "Staff only"}, status=status.HTTP_403_FORBIDDEN)
        try:
            event = Event.objects.get(pk=pk)
        except Event.DoesNotExist:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        user_ids = request.data.get("user_ids") or ([request.data["user_id"]] if request.data.get("user_id") else [])
        added = []
        for uid in user_ids:
            try:
                obj, created = EventAttendee.objects.get_or_create(event=event, user_id=uid)
                if created:
                    added.append(str(uid))
            except Exception:
                pass
        return Response({"added": added})

    @action(detail=True, methods=["delete"], url_path=r"attendees/(?P<user_id>[^/.]+)")
    def remove_attendee(self, request, pk=None, user_id=None):
        if not _is_staff(request.user):
            return Response({"detail": "Staff only"}, status=status.HTTP_403_FORBIDDEN)
        EventAttendee.objects.filter(event_id=pk, user_id=user_id).delete()
        return Response({"ok": True})

    # ── Hosts ─────────────────────────────────────────────────────────────────

    @action(detail=True, methods=["post"], url_path="hosts")
    def add_host(self, request, pk=None):
        if not _is_admin(request.user):
            return Response({"detail": "Admin only"}, status=status.HTTP_403_FORBIDDEN)
        try:
            event = Event.objects.get(pk=pk)
        except Event.DoesNotExist:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        EventHost.objects.get_or_create(event=event, user_id=request.data["user_id"])
        return Response({"ok": True})

    @action(detail=True, methods=["delete"], url_path=r"hosts/(?P<user_id>[^/.]+)")
    def remove_host(self, request, pk=None, user_id=None):
        if not _is_admin(request.user):
            return Response({"detail": "Admin only"}, status=status.HTTP_403_FORBIDDEN)
        EventHost.objects.filter(event_id=pk, user_id=user_id).delete()
        return Response({"ok": True})

    @action(detail=True, methods=["patch"], url_path=r"hosts/(?P<user_id>[^/.]+)/make-primary")
    def make_primary_host(self, request, pk=None, user_id=None):
        if not _is_admin(request.user):
            return Response({"detail": "Admin only"}, status=status.HTTP_403_FORBIDDEN)
        try:
            event = Event.objects.get(pk=pk)
        except Event.DoesNotExist:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        event.host_id = user_id
        event.save(update_fields=["host_id"])
        return Response({"ok": True})


class OccurrenceViewSet(ViewSet):
    permission_classes = [IsAuthenticated]

    def partial_update(self, request, pk=None):
        if not _is_admin(request.user):
            return Response({"detail": "Admin only"}, status=status.HTTP_403_FORBIDDEN)
        try:
            occ = EventOccurrence.objects.get(pk=pk)
        except EventOccurrence.DoesNotExist:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        if "scheduled_at" in request.data:
            occ.scheduled_at = request.data["scheduled_at"]
        if "host_id" in request.data:
            occ.host_id = request.data["host_id"] or None
        occ.save()
        return Response({"ok": True})

    def destroy(self, request, pk=None):
        if not _is_admin(request.user):
            return Response({"detail": "Admin only"}, status=status.HTTP_403_FORBIDDEN)
        try:
            occ = EventOccurrence.objects.get(pk=pk)
        except EventOccurrence.DoesNotExist:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        occ.delete()
        return Response({"ok": True})

    @action(detail=True, methods=["post"], url_path="attendance")
    def upsert_attendance(self, request, pk=None):
        if not _is_staff(request.user):
            return Response({"detail": "Staff only"}, status=status.HTTP_403_FORBIDDEN)
        try:
            occ = EventOccurrence.objects.get(pk=pk)
        except EventOccurrence.DoesNotExist:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        user_id = request.data.get("user_id")
        mode = request.data.get("mode", Attendance.MODE_PHYSICAL)
        checked_in_by_id = request.data.get("checked_in_by")
        obj, _ = Attendance.objects.update_or_create(
            occurrence=occ,
            user_id=user_id,
            defaults={"mode": mode, "checked_in_by_id": checked_in_by_id},
        )
        return Response(AttendanceSerializer(obj).data)

    @action(detail=True, methods=["post"], url_path="attendance/bulk")
    def bulk_attendance(self, request, pk=None):
        if not _is_staff(request.user):
            return Response({"detail": "Staff only"}, status=status.HTTP_403_FORBIDDEN)
        try:
            occ = EventOccurrence.objects.get(pk=pk)
        except EventOccurrence.DoesNotExist:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        rows = request.data.get("rows", [])
        for row in rows:
            Attendance.objects.update_or_create(
                occurrence=occ,
                user_id=row["user_id"],
                defaults={
                    "mode": row.get("mode", Attendance.MODE_PHYSICAL),
                    "checked_in_by_id": row.get("checked_in_by"),
                },
            )
        return Response({"ok": True})

    @action(detail=True, methods=["delete"], url_path=r"attendance/(?P<user_id>[^/.]+)")
    def remove_attendance(self, request, pk=None, user_id=None):
        if not _is_staff(request.user):
            return Response({"detail": "Staff only"}, status=status.HTTP_403_FORBIDDEN)
        Attendance.objects.filter(occurrence_id=pk, user_id=user_id).delete()
        return Response({"ok": True})

    @action(detail=True, methods=["patch"], url_path=r"attendance/(?P<user_id>[^/.]+)/update")
    def update_attendance(self, request, pk=None, user_id=None):
        if not _is_staff(request.user):
            return Response({"detail": "Staff only"}, status=status.HTTP_403_FORBIDDEN)
        try:
            att = Attendance.objects.get(occurrence_id=pk, user_id=user_id)
        except Attendance.DoesNotExist:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        att.mode = request.data.get("mode", att.mode)
        att.save(update_fields=["mode"])
        return Response({"ok": True})


class DashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from accounts.models import UserRole

        events = list(
            Event.objects.values("id", "title", "host_id", "physical_location", "online_location")
        )
        occurrences = list(
            EventOccurrence.objects.values("id", "event_id", "scheduled_at", "status", "duration_minutes", "host_id")
        )
        attendees = list(EventAttendee.objects.values("event_id", "user_id"))
        attendance = list(Attendance.objects.values("occurrence_id", "user_id", "mode", "checked_in_at"))
        roles = list(UserRole.objects.values("user_id", "role"))

        from accounts.models import User
        raw_profiles = list(User.objects.values("id", "first_name", "last_name", "email"))

        def str_or_none(v):
            return str(v) if v else None

        return Response({
            "events": [
                {
                    "id": str(e["id"]),
                    "title": e["title"],
                    "host_id": str_or_none(e["host_id"]),
                    "physical_location": e["physical_location"],
                    "online_location": e["online_location"],
                }
                for e in events
            ],
            "occurrences": [
                {
                    "id": str(o["id"]),
                    "event_id": str(o["event_id"]),
                    "scheduled_at": o["scheduled_at"].isoformat() if o["scheduled_at"] else None,
                    "status": o["status"],
                    "duration_minutes": o["duration_minutes"],
                    "host_id": str_or_none(o["host_id"]),
                }
                for o in occurrences
            ],
            "attendees": [{"event_id": str(a["event_id"]), "user_id": str(a["user_id"])} for a in attendees],
            "attendance": [
                {
                    "occurrence_id": str(a["occurrence_id"]),
                    "user_id": str(a["user_id"]),
                    "mode": a["mode"],
                    "checked_in_at": a["checked_in_at"].isoformat() if a["checked_in_at"] else None,
                }
                for a in attendance
            ],
            "profiles": [
                {
                    "id": str(p["id"]),
                    "full_name": f"{p['first_name']} {p['last_name']}".strip() or None,
                    "email": p["email"],
                }
                for p in raw_profiles
            ],
            "roles": [{"user_id": str(r["user_id"]), "role": r["role"]} for r in roles],
        })
