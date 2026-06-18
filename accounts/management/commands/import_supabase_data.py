"""
Import Supabase-exported CSV data into Django models.

CSV files are expected in import_data/ (semicolon-delimited) with columns
matching the Supabase export schema.
"""
from __future__ import annotations

import csv
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounts.models import Profile, User, UserRole
from events.models import Attendance, Event, EventAttendee, EventHost, EventOccurrence

logger = logging.getLogger(__name__)


@dataclass
class ImportStats:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    error_details: list[str] = field(default_factory=list)

    def record(self, created: bool) -> None:
        if created:
            self.created += 1
        else:
            self.updated += 1

    def record_skip(self, reason: str) -> None:
        self.skipped += 1
        self.error_details.append(reason)

    def record_error(self, reason: str) -> None:
        self.errors += 1
        self.error_details.append(reason)


def parse_dt(value: str | None) -> datetime | None:
    """Parse Supabase timestamp strings into timezone-aware datetimes."""
    if not value or not str(value).strip():
        return None
    raw = str(value).strip()
    if raw.endswith("+00"):
        raw = raw[:-3] + "+00:00"
    if len(raw) > 6 and raw[-6] == " " and raw[-5] in "+-":
        raw = raw[:-6] + raw[-5:]
    dt = parse_datetime(raw)
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.utc)
    return dt


def parse_uuid(value: str | None) -> uuid.UUID | None:
    if not value or not str(value).strip():
        return None
    try:
        return uuid.UUID(str(value).strip())
    except (ValueError, AttributeError):
        return None


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_json(value: str | None) -> Any:
    text = clean_text(value)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def apply_timestamps(model, pk, **fields) -> None:
    """Set auto-managed timestamp fields after create/update."""
    updates = {k: v for k, v in fields.items() if v is not None}
    if updates:
        model.objects.filter(pk=pk).update(**updates)


def read_csv_rows(path: Path) -> Iterator[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for row in reader:
            if any(str(v).strip() for v in row.values()):
                yield row


class Command(BaseCommand):
    help = (
        "Import profiles, roles, events, and related data from Supabase CSV exports. "
        "Users are skipped by default (assumes GHL sync already populated them)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--data-dir",
            type=str,
            default=None,
            help="Directory containing CSV files (default: <project>/import_data).",
        )
        parser.add_argument(
            "--import-users",
            action="store_true",
            help="Also import users.csv (not needed when users were created by GHL sync).",
        )

    def handle(self, *args, **options):
        data_dir = self._resolve_data_dir(options["data_dir"])
        import_users = options["import_users"]
        self.stdout.write(self.style.NOTICE(f"Importing from {data_dir}"))

        self.supabase_id_to_email = self._load_supabase_user_map(data_dir)
        self.django_users_by_email = {
            u.email.lower(): u for u in User.objects.all()
        }
        self.stdout.write(
            f"  Supabase user map: {len(self.supabase_id_to_email)} entries, "
            f"Django users in DB: {len(self.django_users_by_email)}"
        )

        if not import_users:
            self.stdout.write(
                self.style.NOTICE(
                    "Skipping users.csv — matching existing users by email for foreign keys."
                )
            )

        summary: dict[str, ImportStats] = {}

        importers: list[tuple[str, str, Callable[[Path], ImportStats], bool]] = [
            ("users", "users.csv", self.import_users, import_users),
            ("profiles", "profiles.csv", self.import_profiles, True),
            ("user_roles", "user_roles.csv", self.import_user_roles, True),
            ("events", "events.csv", self.import_events, True),
            ("event_occurrences", "event_occurrences.csv", self.import_event_occurrences, True),
            ("event_hosts", "event_hosts.csv", self.import_event_hosts, True),
            ("event_attendees", "event_attendees.csv", self.import_event_attendees, True),
            ("attendance", "attendance.csv", self.import_attendance, True),
        ]

        for label, filename, importer, enabled in importers:
            path = data_dir / filename
            if not enabled:
                self.stdout.write(
                    self.style.WARNING(f"\n=== {label} ({filename}) — skipped ===")
                )
                summary[label] = ImportStats()
                continue
            if not path.exists():
                raise CommandError(f"Missing required file: {path}")
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n=== {label} ({filename}) ==="))
            stats = importer(path)
            summary[label] = stats
            self._log_stats(label, stats)

        self.stdout.write(self.style.SUCCESS("\n=== Import summary ==="))
        for label, stats in summary.items():
            self._log_stats(label, stats, indent="  ")

        total_errors = sum(s.errors for s in summary.values())
        total_skipped = sum(s.skipped for s in summary.values())
        if total_errors:
            self.stdout.write(
                self.style.WARNING(f"\nCompleted with {total_errors} error(s), {total_skipped} skipped.")
            )
        else:
            self.stdout.write(self.style.SUCCESS("\nImport completed successfully."))

    def _resolve_data_dir(self, data_dir: str | None) -> Path:
        if data_dir:
            path = Path(data_dir)
        else:
            path = Path(settings.BASE_DIR) / "import_data"
        if not path.is_dir():
            raise CommandError(f"Data directory not found: {path}")
        return path

    def _log_stats(self, label: str, stats: ImportStats, indent: str = "") -> None:
        self.stdout.write(
            f"{indent}{label}: created={stats.created}, updated={stats.updated}, "
            f"skipped={stats.skipped}, errors={stats.errors}"
        )
        for detail in stats.error_details[:10]:
            self.stdout.write(f"{indent}  - {detail}")
        if len(stats.error_details) > 10:
            self.stdout.write(f"{indent}  ... and {len(stats.error_details) - 10} more")

    def _load_supabase_user_map(self, data_dir: Path) -> dict[uuid.UUID, str]:
        """Map Supabase auth user UUIDs to emails (from users.csv and profiles.csv)."""
        mapping: dict[uuid.UUID, str] = {}
        for filename in ("users.csv", "profiles.csv"):
            path = data_dir / filename
            if not path.exists():
                continue
            for row in read_csv_rows(path):
                user_id = parse_uuid(row.get("id"))
                email = clean_text(row.get("email"))
                if user_id and email:
                    mapping[user_id] = email.lower()
        return mapping

    def _resolve_user(self, supabase_user_id: uuid.UUID | None) -> User | None:
        """Look up a Django user by Supabase UUID via email (GHL assigns new UUIDs)."""
        if not supabase_user_id:
            return None
        email = self.supabase_id_to_email.get(supabase_user_id)
        if not email:
            return None
        return self.django_users_by_email.get(email)

    def _resolve_user_by_email(self, email: str | None) -> User | None:
        if not email:
            return None
        return self.django_users_by_email.get(email.lower())

    def import_users(self, path: Path) -> ImportStats:
        stats = ImportStats()
        rows = list(read_csv_rows(path))
        total = len(rows)

        for index, row in enumerate(rows, start=1):
            user_id = parse_uuid(row.get("id"))
            email = clean_text(row.get("email"))
            if not user_id or not email:
                stats.record_skip(f"Row {index}: missing id or email")
                continue

            created_at = parse_dt(row.get("created_at"))
            try:
                with transaction.atomic():
                    user, created = User.objects.update_or_create(
                        id=user_id,
                        defaults={
                            "email": email.lower(),
                            "username": "",
                            "is_active": True,
                        },
                    )
                    if created:
                        user.set_unusable_password()
                        user.save(update_fields=["password"])
                    apply_timestamps(User, user.pk, date_joined=created_at)
                    stats.record(created)
            except Exception as exc:
                stats.record_error(f"Row {index} ({email}): {exc}")
                logger.exception("Failed to import user %s", email)

            if index % 50 == 0 or index == total:
                self.stdout.write(f"  users: {index}/{total}")

        return stats

    def import_profiles(self, path: Path) -> ImportStats:
        stats = ImportStats()
        rows = list(read_csv_rows(path))
        total = len(rows)

        for index, row in enumerate(rows, start=1):
            email = clean_text(row.get("email"))
            user = self._resolve_user_by_email(email)
            if not user:
                supabase_id = parse_uuid(row.get("id"))
                if supabase_id:
                    user = self._resolve_user(supabase_id)
            if not user:
                stats.record_skip(f"Row {index}: no Django user for {email or row.get('id')}")
                continue

            phone = clean_text(row.get("phone"))
            full_name = clean_text(row.get("full_name"))
            created_at = parse_dt(row.get("created_at"))

            try:
                with transaction.atomic():
                    profile, created = Profile.objects.update_or_create(
                        user_id=user.pk,
                        defaults={"phone": phone},
                    )
                    if full_name:
                        user.set_full_name(full_name)
                        user.save(update_fields=["first_name", "last_name"])
                    apply_timestamps(Profile, profile.pk, created_at=created_at)
                    stats.record(created)
            except Exception as exc:
                stats.record_error(f"Row {index} ({email}): {exc}")
                logger.exception("Failed to import profile for %s", email)

            if index % 50 == 0 or index == total:
                self.stdout.write(f"  profiles: {index}/{total}")

        return stats

    def import_user_roles(self, path: Path) -> ImportStats:
        stats = ImportStats()
        rows = list(read_csv_rows(path))
        total = len(rows)

        for index, row in enumerate(rows, start=1):
            supabase_user_id = parse_uuid(row.get("user_id"))
            role = clean_text(row.get("role"))
            if not supabase_user_id or not role:
                stats.record_skip(f"Row {index}: missing user_id or role")
                continue

            if role not in dict(UserRole.ROLE_CHOICES):
                stats.record_skip(f"Row {index}: invalid role '{role}'")
                continue

            user = self._resolve_user(supabase_user_id)
            if not user:
                stats.record_skip(f"Row {index}: no Django user for Supabase id {supabase_user_id}")
                continue

            created_at = parse_dt(row.get("created_at"))

            try:
                with transaction.atomic():
                    role_obj, created = UserRole.objects.update_or_create(
                        user_id=user.pk,
                        role=role,
                        defaults={},
                    )
                    apply_timestamps(UserRole, role_obj.pk, created_at=created_at)
                    stats.record(created)
            except Exception as exc:
                stats.record_error(f"Row {index} ({user.email}, role {role}): {exc}")
                logger.exception("Failed to import user role")

            if index % 50 == 0 or index == total:
                self.stdout.write(f"  user_roles: {index}/{total}")

        return stats

    def import_events(self, path: Path) -> ImportStats:
        stats = ImportStats()
        rows = list(read_csv_rows(path))
        total = len(rows)

        for index, row in enumerate(rows, start=1):
            event_id = parse_uuid(row.get("id"))
            title = clean_text(row.get("title"))
            if not event_id or not title:
                stats.record_skip(f"Row {index}: missing id or title")
                continue

            host = self._resolve_user(parse_uuid(row.get("host_id")))
            created_by = self._resolve_user(parse_uuid(row.get("created_by")))
            if parse_uuid(row.get("host_id")) and not host:
                stats.record_skip(f"Row {index}: host {row.get('host_id')} not found in DB")
                continue
            if parse_uuid(row.get("created_by")) and not created_by:
                stats.record_skip(f"Row {index}: created_by {row.get('created_by')} not found in DB")
                continue

            duration = row.get("duration_minutes")
            try:
                duration_minutes = int(duration) if duration and str(duration).strip() else 60
            except ValueError:
                duration_minutes = 60

            created_at = parse_dt(row.get("created_at"))

            try:
                with transaction.atomic():
                    event, created = Event.objects.update_or_create(
                        id=event_id,
                        defaults={
                            "title": title,
                            "description": clean_text(row.get("description")),
                            "host_id": host.pk if host else None,
                            "created_by_id": created_by.pk if created_by else None,
                            "online_location": clean_text(row.get("online_location")),
                            "physical_location": clean_text(row.get("physical_location")),
                            "duration_minutes": duration_minutes,
                            "timezone": clean_text(row.get("timezone")) or "UTC",
                            "recurrence": parse_json(row.get("recurrence")),
                        },
                    )
                    apply_timestamps(Event, event.pk, created_at=created_at)
                    stats.record(created)
            except Exception as exc:
                stats.record_error(f"Row {index} ({title}): {exc}")
                logger.exception("Failed to import event %s", event_id)

            if index % 10 == 0 or index == total:
                self.stdout.write(f"  events: {index}/{total}")

        return stats

    def import_event_occurrences(self, path: Path) -> ImportStats:
        stats = ImportStats()
        rows = list(read_csv_rows(path))
        total = len(rows)

        for index, row in enumerate(rows, start=1):
            occurrence_id = parse_uuid(row.get("id"))
            event_id = parse_uuid(row.get("event_id"))
            scheduled_at = parse_dt(row.get("scheduled_at"))
            if not occurrence_id or not event_id or not scheduled_at:
                stats.record_skip(f"Row {index}: missing id, event_id, or scheduled_at")
                continue

            if not Event.objects.filter(pk=event_id).exists():
                stats.record_skip(f"Row {index}: event {event_id} not found")
                continue

            host = self._resolve_user(parse_uuid(row.get("host_id")))
            if parse_uuid(row.get("host_id")) and not host:
                stats.record_skip(f"Row {index}: host {row.get('host_id')} not found in DB")
                continue

            duration = row.get("duration_minutes")
            duration_minutes = None
            if duration and str(duration).strip():
                try:
                    duration_minutes = int(duration)
                except ValueError:
                    pass

            created_at = parse_dt(row.get("created_at"))
            status = clean_text(row.get("status")) or "upcoming"

            try:
                with transaction.atomic():
                    occurrence, created = EventOccurrence.objects.update_or_create(
                        id=occurrence_id,
                        defaults={
                            "event_id": event_id,
                            "scheduled_at": scheduled_at,
                            "status": status,
                            "duration_minutes": duration_minutes,
                            "host_id": host.pk if host else None,
                        },
                    )
                    apply_timestamps(EventOccurrence, occurrence.pk, created_at=created_at)
                    stats.record(created)
            except Exception as exc:
                stats.record_error(f"Row {index} (occurrence {occurrence_id}): {exc}")
                logger.exception("Failed to import event occurrence")

            if index % 50 == 0 or index == total:
                self.stdout.write(f"  event_occurrences: {index}/{total}")

        return stats

    def import_event_hosts(self, path: Path) -> ImportStats:
        stats = ImportStats()
        rows = list(read_csv_rows(path))
        total = len(rows)

        for index, row in enumerate(rows, start=1):
            event_id = parse_uuid(row.get("event_id"))
            supabase_user_id = parse_uuid(row.get("user_id"))
            if not event_id or not supabase_user_id:
                stats.record_skip(f"Row {index}: missing event_id or user_id")
                continue

            if not Event.objects.filter(pk=event_id).exists():
                stats.record_skip(f"Row {index}: event {event_id} not found")
                continue

            user = self._resolve_user(supabase_user_id)
            if not user:
                stats.record_skip(f"Row {index}: user {supabase_user_id} not found in DB")
                continue

            added_at = parse_dt(row.get("added_at"))

            try:
                with transaction.atomic():
                    host_record, created = EventHost.objects.update_or_create(
                        event_id=event_id,
                        user_id=user.pk,
                        defaults={},
                    )
                    apply_timestamps(EventHost, host_record.pk, added_at=added_at)
                    stats.record(created)
            except Exception as exc:
                stats.record_error(f"Row {index} (event {event_id}, {user.email}): {exc}")
                logger.exception("Failed to import event host")

            if index % 10 == 0 or index == total:
                self.stdout.write(f"  event_hosts: {index}/{total}")

        return stats

    def import_event_attendees(self, path: Path) -> ImportStats:
        stats = ImportStats()
        rows = list(read_csv_rows(path))
        total = len(rows)

        for index, row in enumerate(rows, start=1):
            event_id = parse_uuid(row.get("event_id"))
            supabase_user_id = parse_uuid(row.get("user_id"))
            if not event_id or not supabase_user_id:
                stats.record_skip(f"Row {index}: missing event_id or user_id")
                continue

            if not Event.objects.filter(pk=event_id).exists():
                stats.record_skip(f"Row {index}: event {event_id} not found")
                continue

            user = self._resolve_user(supabase_user_id)
            if not user:
                stats.record_skip(f"Row {index}: user {supabase_user_id} not found in DB")
                continue

            added_at = parse_dt(row.get("added_at"))

            try:
                with transaction.atomic():
                    attendee, created = EventAttendee.objects.update_or_create(
                        event_id=event_id,
                        user_id=user.pk,
                        defaults={},
                    )
                    apply_timestamps(EventAttendee, attendee.pk, added_at=added_at)
                    stats.record(created)
            except Exception as exc:
                stats.record_error(f"Row {index} (event {event_id}, {user.email}): {exc}")
                logger.exception("Failed to import event attendee")

            if index % 50 == 0 or index == total:
                self.stdout.write(f"  event_attendees: {index}/{total}")

        return stats

    def import_attendance(self, path: Path) -> ImportStats:
        stats = ImportStats()
        rows = list(read_csv_rows(path))
        total = len(rows)

        if total == 0:
            self.stdout.write("  attendance: no rows (file empty)")
            return stats

        for index, row in enumerate(rows, start=1):
            attendance_id = parse_uuid(row.get("id"))
            occurrence_id = parse_uuid(row.get("occurrence_id"))
            supabase_user_id = parse_uuid(row.get("user_id"))
            if not attendance_id or not occurrence_id or not supabase_user_id:
                stats.record_skip(f"Row {index}: missing id, occurrence_id, or user_id")
                continue

            if not EventOccurrence.objects.filter(pk=occurrence_id).exists():
                stats.record_skip(f"Row {index}: occurrence {occurrence_id} not found")
                continue

            user = self._resolve_user(supabase_user_id)
            if not user:
                stats.record_skip(f"Row {index}: user {supabase_user_id} not found in DB")
                continue

            checked_in_by = self._resolve_user(parse_uuid(row.get("checked_in_by")))
            if parse_uuid(row.get("checked_in_by")) and not checked_in_by:
                stats.record_skip(f"Row {index}: checked_in_by {row.get('checked_in_by')} not found in DB")
                continue

            mode = clean_text(row.get("mode")) or Attendance.MODE_PHYSICAL
            if mode not in dict(Attendance.MODE_CHOICES):
                stats.record_skip(f"Row {index}: invalid mode '{mode}'")
                continue

            checked_in_at = parse_dt(row.get("checked_in_at"))

            try:
                with transaction.atomic():
                    record, created = Attendance.objects.update_or_create(
                        id=attendance_id,
                        defaults={
                            "occurrence_id": occurrence_id,
                            "user_id": user.pk,
                            "mode": mode,
                            "checked_in_by_id": checked_in_by.pk if checked_in_by else None,
                        },
                    )
                    apply_timestamps(Attendance, record.pk, checked_in_at=checked_in_at)
                    stats.record(created)
            except Exception as exc:
                stats.record_error(f"Row {index} (attendance {attendance_id}): {exc}")
                logger.exception("Failed to import attendance record")

            if index % 50 == 0 or index == total:
                self.stdout.write(f"  attendance: {index}/{total}")

        return stats
