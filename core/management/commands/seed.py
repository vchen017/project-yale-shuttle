"""Create the grader accounts and demo data (#3, #46). Safe to run more than once.

Render runs it on every deploy (build.sh), so staging and production both get
it, and it should be run again after any database reset.

Passwords come from environment variables so they never land in git. Each run
creates missing accounts and resets existing ones to the current passwords.

Demo data lives with its feature: any installed app can have a `demo.py` with
`seed(accounts, stdout)`, where `accounts` is {"admin": User, "rider": User}.
Each one must be safe to run twice, must leave alone anything an admin has
since changed, and must never add A/B test visits: the graded A/B run counts
every visit.
"""

import os
from importlib import import_module
from importlib.util import find_spec

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

ACCOUNTS = [
    # role, username (also the email), password variable, admin?
    ("admin", "admin@example.com", "SEED_ADMIN_PASSWORD", True),
    ("rider", "rider@example.com", "SEED_RIDER_PASSWORD", False),
]


def demo_modules():
    """Each installed app's demo module, in INSTALLED_APPS order."""
    names = [f"{app.name}.demo" for app in apps.get_app_configs()]
    return [import_module(name) for name in names if find_spec(name)]


class Command(BaseCommand):
    help = "Create the grader accounts and demo data. Safe to run more than once."

    def handle(self, *args, **options):
        missing = [var for _, _, var, _ in ACCOUNTS if not os.environ.get(var)]
        if missing:
            raise CommandError(f"Set {', '.join(missing)} first. Grader passwords never go in git.")

        User = get_user_model()
        accounts = {}
        for role, email, password_var, is_admin in ACCOUNTS:
            user, created = User.objects.get_or_create(username=email, defaults={"email": email})
            user.email = email
            user.is_staff = user.is_superuser = is_admin
            user.set_password(os.environ[password_var])
            user.save()
            accounts[role] = user
            self.stdout.write(f"{'Created' if created else 'Updated'} {email}")

        for module in demo_modules():
            # One app's demo data either all lands or none of it does.
            with transaction.atomic():
                module.seed(accounts, self.stdout)
