from rest_framework import serializers
from .models import Event, EventOccurrence, EventAttendee, EventHost, Attendance


class OccurrenceSerializer(serializers.ModelSerializer):
    event_id = serializers.SerializerMethodField()
    host_id = serializers.SerializerMethodField()

    class Meta:
        model = EventOccurrence
        fields = ["id", "event_id", "scheduled_at", "status", "duration_minutes", "host_id", "created_at"]

    def get_event_id(self, obj):
        return str(obj.event_id)

    def get_host_id(self, obj):
        return str(obj.host_id) if obj.host_id else None


class AttendanceSerializer(serializers.ModelSerializer):
    occurrence_id = serializers.SerializerMethodField()
    user_id = serializers.SerializerMethodField()
    checked_in_by = serializers.SerializerMethodField()

    class Meta:
        model = Attendance
        fields = ["id", "occurrence_id", "user_id", "mode", "checked_in_at", "checked_in_by"]

    def get_occurrence_id(self, obj):
        return str(obj.occurrence_id)

    def get_user_id(self, obj):
        return str(obj.user_id)

    def get_checked_in_by(self, obj):
        return str(obj.checked_in_by_id) if obj.checked_in_by_id else None


class EventListSerializer(serializers.ModelSerializer):
    host_id = serializers.SerializerMethodField()
    host = serializers.SerializerMethodField()
    occurrences = OccurrenceSerializer(many=True, read_only=True)

    class Meta:
        model = Event
        fields = [
            "id", "title", "description", "online_location", "physical_location",
            "host_id", "host", "duration_minutes", "timezone", "recurrence", "created_at",
            "occurrences",
        ]

    def get_host_id(self, obj):
        return str(obj.host_id) if obj.host_id else None

    def get_host(self, obj):
        if not obj.host:
            return None
        return {"id": str(obj.host.id), "full_name": obj.host.full_name, "email": obj.host.email}


class EventDetailSerializer(serializers.ModelSerializer):
    host_id = serializers.SerializerMethodField()
    created_by = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = [
            "id", "title", "description", "host_id", "created_by",
            "online_location", "physical_location", "duration_minutes",
            "timezone", "recurrence", "created_at",
        ]

    def get_host_id(self, obj):
        return str(obj.host_id) if obj.host_id else None

    def get_created_by(self, obj):
        return str(obj.created_by_id) if obj.created_by_id else None
