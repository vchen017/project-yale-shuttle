from django.contrib.auth.models import AbstractUser

# The usernames the seed command turns into the grader accounts (see
# core/management/commands/seed.py's ACCOUNTS). Signup refuses them, so a
# squatter can't occupy one before the first seed runs and promotes
# whoever owns it to grader or superuser.
RESERVED_USERNAMES = frozenset({"admin@example.com", "rider@example.com"})


class User(AbstractUser):
    """A rider. Admins are users with is_staff set; they can use /admin."""
