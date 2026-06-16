from django.conf import settings
from django.contrib.auth import authenticate
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet
from rest_framework_simplejwt.tokens import RefreshToken

from .models import User, Profile, UserRole
from .permissions import IsAdmin, IsStaff
from .serializers import MeSerializer, UserSerializer

DEFAULT_GHL_PASSWORD = "EV3Nt5@1234"


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        password = request.data.get("password", "")
        user = authenticate(request, email=email, password=password)
        if not user:
            return Response({"detail": "Invalid credentials"}, status=status.HTTP_401_UNAUTHORIZED)
        refresh = RefreshToken.for_user(user)
        return Response({
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "user": MeSerializer(user).data,
        })


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            token = RefreshToken(request.data.get("refresh"))
            token.blacklist()
        except Exception:
            pass
        return Response({"ok": True})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(MeSerializer(request.user).data)


class AutoLoginView(APIView):
    """
    Generate JWT tokens for a user by email.
    Requires a shared AUTO_LOGIN_SECRET in the request body (for GHL ?user= deep links).
    """
    permission_classes = [AllowAny]

    def post(self, request):
        secret = request.data.get("secret", "")
        configured = settings.AUTO_LOGIN_SECRET
        if not configured or secret != configured:
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        email = (request.data.get("email") or "").strip().lower()
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return Response({"detail": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        refresh = RefreshToken.for_user(user)
        return Response({"access": str(refresh.access_token), "refresh": str(refresh)})


class UserViewSet(ViewSet):
    permission_classes = [IsAdmin]

    def get_permissions(self):
        if self.action == "create":
            return [AllowAny()]
        if self.action in ("staff", "profiles"):
            return [IsStaff()]
        return [IsAdmin()]

    def list(self, request):
        users = (
            User.objects.prefetch_related("roles", "profile")
            .order_by("-date_joined")
        )
        return Response(UserSerializer(users, many=True).data)

    def create(self, request):
        email = (request.data.get("email") or "").strip().lower()
        password = request.data.get("password") or DEFAULT_GHL_PASSWORD
        full_name = request.data.get("full_name") or ""
        phone = request.data.get("phone") or ""

        if not email:
            return Response({"detail": "Email required"}, status=status.HTTP_400_BAD_REQUEST)
        if User.objects.filter(email=email).exists():
            return Response({"detail": "Email already exists"}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.create_user(email=email, password=password)
        if full_name:
            user.set_full_name(full_name)
            user.save()

        user.roles.all().delete()
        if request.user.is_authenticated and UserRole.objects.filter(user=request.user, role="admin").exists():
            role = request.data.get("role", "attendee")
        elif User.objects.count() == 1:
            role = "admin"
        else:
            role = "attendee"
        if role not in ["admin", "trainer", "attendee"]:
            role = "attendee"
        UserRole.objects.create(user=user, role=role)

        profile, _ = Profile.objects.get_or_create(user=user)
        if phone:
            profile.phone = phone
            profile.save()

        try:
            from ghl.services import upsert_ghl_contact
            upsert_ghl_contact(email=email, full_name=full_name or None, phone=phone or None)
        except Exception as exc:
            print(f"[admin] GHL sync failed: {exc}")

        return Response({"id": str(user.id)}, status=status.HTTP_201_CREATED)

    def retrieve(self, request, pk=None):
        try:
            user = User.objects.prefetch_related("roles", "profile").get(pk=pk)
        except User.DoesNotExist:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(UserSerializer(user).data)

    def partial_update(self, request, pk=None):
        try:
            user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        if "full_name" in request.data and request.data["full_name"] is not None:
            user.set_full_name(request.data["full_name"])
        if "email" in request.data:
            user.email = request.data["email"].strip().lower()
        if request.data.get("password"):
            user.set_password(request.data["password"])
        user.save()

        if "phone" in request.data:
            profile, _ = Profile.objects.get_or_create(user=user)
            profile.phone = request.data["phone"] or None
            profile.save()

        try:
            from ghl.services import upsert_ghl_contact
            profile = user.profile
            upsert_ghl_contact(email=user.email, full_name=user.full_name, phone=profile.phone)
        except Exception as exc:
            print(f"[admin] GHL sync failed: {exc}")

        return Response({"ok": True})

    def destroy(self, request, pk=None):
        try:
            user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        if str(user.id) == str(request.user.id):
            return Response({"detail": "Cannot delete yourself"}, status=status.HTTP_400_BAD_REQUEST)
        user.delete()
        return Response({"ok": True})

    @action(detail=True, methods=["post"], url_path="set-role")
    def set_role(self, request, pk=None):
        try:
            user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        role = request.data.get("role")
        if role not in ["admin", "trainer", "attendee"]:
            return Response({"detail": "Invalid role"}, status=status.HTTP_400_BAD_REQUEST)
        user.roles.all().delete()
        UserRole.objects.create(user=user, role=role)
        return Response({"ok": True})

    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password(self, request, pk=None):
        try:
            user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        password = request.data.get("password")
        if not password:
            return Response({"detail": "Password required"}, status=status.HTTP_400_BAD_REQUEST)
        user.set_password(password)
        user.save()
        return Response({"ok": True})

    @action(detail=False, methods=["post"], url_path="sync-from-ghl")
    def sync_from_ghl(self, request):
        from accounts.ghl_sync import get_ghl_sync_status, start_ghl_user_sync

        if not settings.GHL_PRIVATE_TOKEN:
            return Response({"detail": "GHL_PRIVATE_TOKEN not configured"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        if not settings.GHL_LOCATION_ID:
            return Response({"detail": "GHL_LOCATION_ID not configured"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if not start_ghl_user_sync():
            status_payload = get_ghl_sync_status()
            return Response(
                {"detail": "GHL sync already in progress", **status_payload},
                status=status.HTTP_409_CONFLICT,
            )

        return Response({"status": "started", "running": True}, status=status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=["get"], url_path="sync-from-ghl/status")
    def sync_from_ghl_status(self, request):
        from accounts.ghl_sync import get_ghl_sync_status

        payload = get_ghl_sync_status()
        if payload["error"]:
            return Response({"status": "failed", **payload}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        if payload["running"]:
            return Response({"status": "running", **payload})
        if payload["result"]:
            return Response({"status": "completed", **payload})
        return Response({"status": "idle", **payload})

    @action(detail=False, methods=["get"], url_path="staff", permission_classes=[IsStaff])
    def staff(self, request):
        from accounts.models import UserRole as UR
        staff_ids = UR.objects.filter(role__in=["admin", "trainer"]).values_list("user_id", flat=True)
        users = User.objects.filter(id__in=staff_ids).order_by("first_name", "last_name")
        return Response([{"id": str(u.id), "full_name": u.full_name, "email": u.email} for u in users])

    @action(detail=False, methods=["get"], url_path="profiles", permission_classes=[IsStaff])
    def profiles(self, request):
        users = User.objects.order_by("first_name", "last_name")
        return Response([{"id": str(u.id), "full_name": u.full_name, "email": u.email} for u in users])
