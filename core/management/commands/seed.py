"""Create the grader accounts (and, later, demo data). Safe to run more than once.

Passwords come from environment variables so they never land in git. Each run
creates missing accounts and resets existing ones to the current passwords.
"""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

ACCOUNTS = [
    # username (also the email), password variable, admin?
    ("admin@example.com", "SEED_ADMIN_PASSWORD", True),
    ("rider@example.com", "SEED_RIDER_PASSWORD", False),
]


class Command(BaseCommand):
    help = "Create the grader accounts and demo data. Safe to run more than once."

    def handle(self, *args, **options):
        missing = [var for _, var, _ in ACCOUNTS if not os.environ.get(var)]
        if missing:
            raise CommandError(f"Set {', '.join(missing)} first. Grader passwords never go in git.")

        User = get_user_model()
        for email, password_var, is_admin in ACCOUNTS:
            user, created = User.objects.get_or_create(username=email, defaults={"email": email})
            user.email = email
            user.is_staff = user.is_superuser = is_admin
            user.set_password(os.environ[password_var])
            user.save()
            self.stdout.write(f"{'Created' if created else 'Updated'} {email}")
