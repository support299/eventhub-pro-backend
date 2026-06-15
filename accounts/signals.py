from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender="accounts.User")
def handle_new_user(sender, instance, created, **kwargs):
    if not created:
        return
    from accounts.models import Profile, UserRole

    Profile.objects.get_or_create(user=instance)

    if not UserRole.objects.filter(user=instance).exists():
        # First user in the system becomes admin; all others become attendee.
        from accounts.models import User
        role = "admin" if User.objects.count() == 1 else "attendee"
        UserRole.objects.create(user=instance, role=role)
