"""Background GHL user import with incremental sync."""
from __future__ import annotations

import threading

import requests
from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone

from accounts.models import GhlSyncState, Profile, User, UserRole

DEFAULT_GHL_PASSWORD = "EV3Nt5@1234"
GHL_USERS_URL = "https://services.leadconnectorhq.com/users/"

_lock = threading.Lock()


def _get_state() -> GhlSyncState:
    state, _ = GhlSyncState.objects.get_or_create(pk=1)
    return state


def get_ghl_sync_status() -> dict:
    state = _get_state()
    return {
        "running": state.running,
        "result": state.result,
        "error": state.error or None,
        "started_at": state.started_at.isoformat() if state.started_at else None,
        "finished_at": state.finished_at.isoformat() if state.finished_at else None,
    }


def start_ghl_user_sync() -> bool:
    """Start sync in a background thread. Returns False if already running."""
    with _lock:
        state = _get_state()
        if state.running:
            stale = (
                state.started_at
                and (timezone.now() - state.started_at).total_seconds() > 30 * 60
            )
            if not stale:
                return False
            state.running = False
            state.error = "Previous sync timed out"
            state.finished_at = timezone.now()
            state.save(update_fields=["running", "error", "finished_at", "updated_at"])
        state.running = True
        state.result = None
        state.error = ""
        state.started_at = timezone.now()
        state.finished_at = None
        state.save(
            update_fields=["running", "result", "error", "started_at", "finished_at", "updated_at"]
        )

    def worker():
        close_old_connections()
        try:
            result = _perform_sync()
            state = _get_state()
            state.result = result
            state.error = ""
        except Exception as exc:
            state = _get_state()
            state.error = str(exc)
            state.result = None
            print(f"[admin] GHL sync failed: {exc}")
        finally:
            state = _get_state()
            state.running = False
            state.finished_at = timezone.now()
            state.save(
                update_fields=["running", "result", "error", "finished_at", "updated_at"]
            )
            close_old_connections()

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
        timeout=120,
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
            with transaction.atomic():
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
