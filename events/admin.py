from django.contrib import admin
from .models import Event, EventOccurrence, EventAttendee, EventHost, Attendance


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ["title", "host", "duration_minutes", "created_at"]
    search_fields = ["title"]


@admin.register(EventOccurrence)
class EventOccurrenceAdmin(admin.ModelAdmin):
    list_display = ["event", "scheduled_at", "status", "duration_minutes"]
    list_filter = ["status"]


@admin.register(EventAttendee)
class EventAttendeeAdmin(admin.ModelAdmin):
    list_display = ["event", "user", "added_at"]


@admin.register(EventHost)
class EventHostAdmin(admin.ModelAdmin):
    list_display = ["event", "user", "added_at"]


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ["occurrence", "user", "mode", "checked_in_at"]
    list_filter = ["mode"]
