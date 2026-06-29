"""Handle GoHighLevel user lifecycle webhook events."""
from __future__ import annotations

from django.conf import settings

from accounts.ghl_sync import (
    delete_user_from_ghl,
    ghl_user_belongs_to_location,
    upsert_user_from_ghl,
)

CREATE_EVENTS = {"UserCreate", "user.created"}
UPDATE_EVENTS = {"UserUpdate", "user.updated"}
DELETE_EVENTS = {"UserDelete", "user.deleted"}
USER_EVENTS = CREATE_EVENTS | UPDATE_EVENTS | DELETE_EVENTS


def parse_webhook_payload(body: dict) -> tuple[str | None, dict]:
    event_type = (body.get("type") or body.get("event") or "").strip()
    user_data = body.get("data")
    if isinstance(user_data, dict) and user_data:
        return event_type, user_data
    skip_keys = {"type", "event", "timestamp", "webhookId", "webhook_id", "idempotencyKey"}
    return event_type, {k: v for k, v in body.items() if k not in skip_keys}


def handle_ghl_user_webhook(body: dict) -> dict:
    event_type, user_data = parse_webhook_payload(body)
    if event_type not in USER_EVENTS:
        return {"ok": True, "action": "ignored", "reason": "unsupported_event", "type": event_type}

    location_id = settings.GHL_LOCATION_ID
    if location_id and not ghl_user_belongs_to_location(user_data, location_id):
        return {"ok": True, "action": "ignored", "reason": "other_location", "type": event_type}

    if event_type in DELETE_EVENTS:
        result = delete_user_from_ghl(user_data)
    else:
        result = upsert_user_from_ghl(user_data)

    return {"ok": True, "type": event_type, **result}
