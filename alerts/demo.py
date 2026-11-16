"""Demo service alerts for graders (#46), run by `manage.py seed`.

One sample alert riders see on the home page, worded so no rider mistakes it
for real service news, and one that has already ended, so the admin's status
column and filter have something to show. Each is created only if no alert
has its title, so a rerun creates nothing twice and never undoes an admin's
edit, End now, or new times.
"""

import datetime as dt

from django.utils import timezone

from .models import Alert

SHOWING_FOR = dt.timedelta(days=60)  # past the final submission, from the first deploy

SAMPLE_TITLE = "Welcome to Yale Shuttle"
SAMPLE_MESSAGE = (
    "This is a sample alert, to show how service alerts look. Real ones, like "
    "detours, cancelled runs or road closures for an event, appear here newest "
    "first, next to the Downtowner's own announcements."
)
ENDED_TITLE = "Sample: Chapel Street closed for a past event"
ENDED_MESSAGE = "A sample alert that has already ended. Riders never see it; admins see it as Ended."


def seed(accounts, stdout):
    now = timezone.now()
    demo = [
        (SAMPLE_TITLE, SAMPLE_MESSAGE, now, now + SHOWING_FOR),
        (ENDED_TITLE, ENDED_MESSAGE, now - dt.timedelta(days=2), now - dt.timedelta(days=1)),
    ]
    for title, message, starts_at, ends_at in demo:
        _, created = Alert.objects.get_or_create(
            title=title, defaults={"message": message, "starts_at": starts_at, "ends_at": ends_at}
        )
        stdout.write(f"{'Created' if created else 'Kept'} the demo alert {title!r}")
