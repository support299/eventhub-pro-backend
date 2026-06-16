"""
Server-side GoHighLevel integration service.
All GHL API calls originate from here so the private token never leaves the backend.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from django.conf import settings

GHL_BASE = "https://services.leadconnectorhq.com"


def _format_time_ampm(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    period = "AM" if dt.hour < 12 else "PM"
    return f"{hour}:{dt.minute:02d} {period}"


def _to_local(dt: datetime, time_zone: str) -> datetime:
    try:
        return dt.astimezone(ZoneInfo(time_zone))
    except Exception:
        return dt


def _ghl_headers():
    token = settings.GHL_PRIVATE_TOKEN
    if not token:
        raise ValueError("GHL_PRIVATE_TOKEN is not configured")
    return {
        "Authorization": f"Bearer {token}",
        "Version": "2021-07-28",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _retrigger_tags(contact_id: str, tags: list[str]):
    if not tags:
        return
    headers = _ghl_headers()
    try:
        requests.delete(f"{GHL_BASE}/contacts/{contact_id}/tags", json={"tags": tags}, headers=headers, timeout=15)
    except Exception as exc:
        print(f"[GHL] tag remove threw: {exc}")
    try:
        requests.post(f"{GHL_BASE}/contacts/{contact_id}/tags", json={"tags": tags}, headers=headers, timeout=15)
    except Exception as exc:
        print(f"[GHL] tag add threw: {exc}")


def upsert_ghl_contact(*, email: str, full_name: str | None = None, phone: str | None = None,
                        custom_fields: list | None = None, tags: list[str] | None = None):
    location_id = settings.GHL_LOCATION_ID
    if not location_id:
        raise ValueError("GHL_LOCATION_ID is not configured")

    parts = (full_name or "").strip().split(" ", 1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else ""

    payload: dict = {"locationId": location_id, "email": email, "source": "Training Hub signup"}
    if first_name:
        payload["firstName"] = first_name
    if last_name:
        payload["lastName"] = last_name
    if phone:
        payload["phone"] = phone
    if custom_fields:
        payload["customFields"] = custom_fields

    resp = requests.post(f"{GHL_BASE}/contacts/upsert", json=payload, headers=_ghl_headers(), timeout=15)
    if not resp.ok:
        raise RuntimeError(f"GHL upsert failed [{resp.status_code}]: {resp.text}")

    contact = resp.json().get("contact") or resp.json()
    if contact.get("id") and tags:
        _retrigger_tags(contact["id"], tags)

    return contact


def sync_user_schedule(user_id: str, tz: str | None = None, event_id: str | None = None):
    from accounts.models import User, Profile
    from events.models import Event, EventOccurrence, EventAttendee, EventHost, Attendance

    try:
        user = User.objects.select_related("profile").get(id=user_id)
    except User.DoesNotExist:
        return

    try:
        email = user.email
        full_name = user.full_name
        phone = user.profile.phone if hasattr(user, "profile") else None
    except Exception:
        return

    if not email:
        return

    if event_id:
        event_ids = [event_id]
    else:
        attendee_ids = list(EventAttendee.objects.filter(user_id=user_id).values_list("event_id", flat=True))
        host_ids = list(EventHost.objects.filter(user_id=user_id).values_list("event_id", flat=True))
        primary_ids = list(Event.objects.filter(host_id=user_id).values_list("id", flat=True))
        event_ids = list(set(str(i) for i in attendee_ids + host_ids + primary_ids))

    occ = None
    if event_ids:
        occ = (
            EventOccurrence.objects
            .filter(event_id__in=event_ids, scheduled_at__gte=datetime.now(tz=timezone.utc))
            .order_by("scheduled_at")
            .values("scheduled_at", "host_id", "event_id", "id")
            .first()
        )

    is_host = False
    if occ:
        ev = Event.objects.filter(id=occ["event_id"]).values("host_id").first()
        eh = EventHost.objects.filter(event_id=occ["event_id"], user_id=user_id).exists()
        is_host = str(ev["host_id"]) == str(user_id) if ev and ev["host_id"] else False
        is_host = is_host or eh

    role = "Host" if is_host else "Attendee"
    custom_fields = [{"key": "schedule_role", "field_value": role}]

    if occ:
        ev_detail = Event.objects.filter(id=occ["event_id"]).values(
            "title", "host_id", "online_location", "physical_location", "timezone"
        ).first()

        host_user_id = occ["host_id"] or (ev_detail["host_id"] if ev_detail else None)
        host_name = ""
        if host_user_id:
            try:
                hu = User.objects.get(id=host_user_id)
                host_name = hu.full_name or hu.email
            except User.DoesNotExist:
                pass

        att = Attendance.objects.filter(occurrence_id=occ["id"], user_id=user_id).values("mode").first()
        if att:
            m = att["mode"]
            attendance_label = "Present (Physical)" if m == "physical" else "Present (Online)" if m == "online" else "Absent"
        else:
            attendance_label = "Not marked"

        d = occ["scheduled_at"]
        time_zone = (ev_detail["timezone"] if ev_detail else None) or tz or "UTC"
        local_d = _to_local(d, time_zone)
        date_str = local_d.strftime("%d-%b-%Y")
        time_str = _format_time_ampm(local_d)

        custom_fields += [
            {"key": "schedule_title", "field_value": ev_detail["title"] if ev_detail else ""},
            {"key": "schedule_time", "field_value": time_str},
            {"key": "schedule_host", "field_value": host_name},
            {"key": "schedule_date", "field_value": date_str},
            {"key": "schedule_online_location", "field_value": ev_detail["online_location"] or "" if ev_detail else ""},
            {"key": "schedule_physical_location", "field_value": ev_detail["physical_location"] or "" if ev_detail else ""},
            {"key": "schedule_attendance", "field_value": attendance_label},
        ]

    upsert_ghl_contact(
        email=email,
        full_name=full_name,
        phone=phone,
        custom_fields=custom_fields,
        tags=["scheduledevent"],
    )


def sync_event_participants(event_id: str, tz: str | None = None):
    from events.models import Event, EventAttendee, EventHost

    ev = Event.objects.filter(id=event_id).values("host_id").first()
    hosts = list(EventHost.objects.filter(event_id=event_id).values_list("user_id", flat=True))
    attendees = list(EventAttendee.objects.filter(event_id=event_id).values_list("user_id", flat=True))

    ids = list(set(
        ([str(ev["host_id"])] if ev and ev["host_id"] else [])
        + [str(h) for h in hosts]
        + [str(a) for a in attendees]
    ))

    synced = failed = 0
    for uid in ids:
        try:
            sync_user_schedule(uid, tz, event_id)
            synced += 1
        except Exception as exc:
            print(f"[GHL] sync_user_schedule failed for {uid}: {exc}")
            failed += 1

    return {"synced": synced, "failed": failed}
