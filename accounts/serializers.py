from rest_framework import serializers
from .models import User, Profile, UserRole


class UserSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()
    full_name = serializers.SerializerMethodField()
    phone = serializers.SerializerMethodField()
    created_at = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "email", "full_name", "phone", "created_at", "roles"]

    def get_roles(self, obj):
        return list(obj.roles.values_list("role", flat=True))

    def get_full_name(self, obj):
        return obj.full_name

    def get_phone(self, obj):
        try:
            return obj.profile.phone
        except Profile.DoesNotExist:
            return None

    def get_created_at(self, obj):
        return obj.date_joined.isoformat()


class MeSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()
    full_name = serializers.SerializerMethodField()
    phone = serializers.SerializerMethodField()
    isAdmin = serializers.SerializerMethodField()
    isStaff = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "email", "full_name", "phone", "roles", "isAdmin", "isStaff"]

    def get_roles(self, obj):
        return list(obj.roles.values_list("role", flat=True))

    def get_full_name(self, obj):
        return obj.full_name

    def get_phone(self, obj):
        try:
            return obj.profile.phone
        except Profile.DoesNotExist:
            return None

    def get_isAdmin(self, obj):
        return obj.roles.filter(role="admin").exists()

    def get_isStaff(self, obj):
        return obj.roles.filter(role__in=["admin", "trainer"]).exists()
