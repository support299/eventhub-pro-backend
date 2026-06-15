"""Background GHL user import with incremental sync."""
from __future__ import annotations

import threading
from dataclasses import dataclass

import requests
from django.conf import settings
from django.db import transaction

from accounts.models import Profile, User, UserRole

DEFAULT_GHL_PASSWORD = "EV3Nt5@1234"
GHL_USERS_URL = "https://services.leadconnectorhq.com/users/"


@dataclass
class _SyncJob:
    running: bool = False
    result: dict | None = None
    error: str | None = None


_job = _SyncJob()
_lock = threading.Lock()


def get_ghl_sync_status() -> dict:
    with _lock:
        return {
            "running": _job.running,
            "result": _job.result,
            "error": _job.error,
        }


def start_ghl_user_sync() -> bool:
    """Start sync in a background thread. Returns False if already running."""
    with _lock:
        if _job.running:
            return False
        _job.running = True
        _job.result = None
        _job.error = None

    def worker():
        try:
            result = _perform_sync()
            with _lock:
                _job.result = result
        except Exception as exc:
            with _lock:
                _job.error = str(exc)
        finally:
            with _lock:
                _job.running = False

    threading.Thread(target=worker, daemon=True).start()
    return True


def _fetch_ghl_users(token: str, location_id: str) -> list[dict]:
    resp = requests.get(
        f"{GHL_USERS_URL}?locationId={location_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Version": "2021-07-28",
            "Accept": "application/json",
        },
        timeout=60,
    )
    if not resp.ok:
        raise RuntimeError(f"GHL users list [{resp.status_code}]: {resp.text}")
    return resp.json().get("users", [])


def _ghl_user_fields(u: dict) -> tuple[str, str, str | None]:
    email = (u.get("email") or "").strip().lower()
    full_name = (
        u.get("name") or f"{u.get('firstName', '')} {u.get('lastName', '')}".strip() or email
    )
    phone = u.get("phone") or None
    return email, full_name, phone


def _perform_sync() -> dict:
    token = settings.GHL_PRIVATE_TOKEN
    location_id = settings.GHL_LOCATION_ID
    if not token:
        raise RuntimeError("GHL_PRIVATE_TOKEN not configured")
    if not location_id:
        raise RuntimeError("GHL_LOCATION_ID not configured")

    admin_ids = set(UserRole.objects.filter(role="admin").values_list("user_id", flat=True))
    preserved_emails = set(
        User.objects.filter(id__in=admin_ids).values_list("email", flat=True)
    )

    ghl_users = _fetch_ghl_users(token, location_id)
    ghl_by_email: dict[str, dict] = {}
    for u in ghl_users:
        email = (u.get("email") or "").strip().lower()
        if email:
            ghl_by_email[email] = u

    existing_users = {
        u.email.lower(): u
        for u in User.objects.exclude(id__in=admin_ids).select_related("profile")
    }

    removed = created = updated = skipped = 0
    failures: list[dict] = []

    with transaction.atomic():
        for email, user in list(existing_users.items()):
            if email not in ghl_by_email:
                user.delete()
                removed += 1
                del existing_users[email]

        for email, ghl_user in ghl_by_email.items():
            if email in preserved_emails:
                skipped += 1
                continue

            _, full_name, phone = _ghl_user_fields(ghl_user)

            if email in existing_users:
                user = existing_users[email]
                changed = False
                if full_name:
                    user.set_full_name(full_name)
                    changed = True
                if changed:
                    user.save()
                if phone is not None:
                    profile, _ = Profile.objects.get_or_create(user=user)
                    if profile.phone != phone:
                        profile.phone = phone
                        profile.save()
                updated += 1
                continue

            try:
                user = User.objects.create_user(email=email, password=DEFAULT_GHL_PASSWORD)
                if full_name:
                    user.set_full_name(full_name)
                    user.save()
                user.roles.all().delete()
                UserRole.objects.get_or_create(user=user, role="attendee")
                if phone:
                    profile, _ = Profile.objects.get_or_create(user=user)
                    profile.phone = phone
                    profile.save()
                created += 1
            except Exception as exc:
                failures.append({"email": email, "error": str(exc)})

    if failures:
        print("[admin] GHL sync failures:", failures)

    return {
        "ok": True,
        "total": len(ghl_users),
        "wiped": removed,
        "removed": removed,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "failed": len(failures),
    }
