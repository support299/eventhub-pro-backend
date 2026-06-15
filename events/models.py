import uuid
from django.db import models
from django.conf import settings


class Event(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.TextField()
    description = models.TextField(blank=True, null=True)
    host = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name="hosted_events",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_events",
    )
    online_location = models.TextField(blank=True, null=True)
    physical_location = models.TextField(blank=True, null=True)
    duration_minutes = models.IntegerField(default=60)
    timezone = models.TextField(default="UTC")
    recurrence = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class EventOccurrence(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="occurrences")
    scheduled_at = models.DateTimeField()
    status = models.TextField(default="upcoming")
    duration_minutes = models.IntegerField(null=True, blank=True)
    host = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="occurrence_hosts",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["event", "scheduled_at"])]
        ordering = ["scheduled_at"]

    def __str__(self):
        return f"{self.event.title} @ {self.scheduled_at}"


class EventAttendee(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="attendees")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="event_attendee_records",
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("event", "user")]


class EventHost(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="hosts")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="event_host_records",
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("event", "user")]


class Attendance(models.Model):
    MODE_PHYSICAL = "physical"
    MODE_ONLINE = "online"
    MODE_ABSENT = "absent"
    MODE_CHOICES = [
        (MODE_PHYSICAL, "Physical"),
        (MODE_ONLINE, "Online"),
        (MODE_ABSENT, "Absent"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    occurrence = models.ForeignKey(
        EventOccurrence, on_delete=models.CASCADE, related_name="attendance_records",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="attendance_records",
    )
    mode = models.CharField(max_length=20, choices=MODE_CHOICES, default=MODE_PHYSICAL)
    checked_in_at = models.DateTimeField(auto_now_add=True)
    checked_in_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="checked_in_records",
    )

    class Meta:
        unique_together = [("occurrence", "user")]
