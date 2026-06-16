import uuid
from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.utils.translation import gettext_lazy as _


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(_("email address"), unique=True)
    username = models.CharField(max_length=150, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    @property
    def full_name(self):
        name = f"{self.first_name} {self.last_name}".strip()
        return name or None

    def set_full_name(self, full_name: str):
        parts = (full_name or "").strip().split(" ", 1)
        self.first_name = parts[0]
        self.last_name = parts[1] if len(parts) > 1 else ""

    def __str__(self):
        return self.email


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    phone = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def full_name(self):
        return self.user.full_name

    @property
    def email(self):
        return self.user.email

    def __str__(self):
        return self.user.email


class UserRole(models.Model):
    ADMIN = "admin"
    TRAINER = "trainer"
    ATTENDEE = "attendee"
    ROLE_CHOICES = [
        (ADMIN, "Admin"),
        (TRAINER, "Trainer"),
        (ATTENDEE, "Attendee"),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="roles")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user", "role")]

    def __str__(self):
        return f"{self.user.email} – {self.role}"


class GhlSyncState(models.Model):
    """Singleton row (pk=1) tracking background GHL user import — shared across Gunicorn workers."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    running = models.BooleanField(default=False)
    result = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "GHL sync state"

    def __str__(self):
        return "running" if self.running else "idle"
