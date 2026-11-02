from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """A rider. Admins are users with is_staff set; they can use /admin."""
