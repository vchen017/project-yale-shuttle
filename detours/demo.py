"""A sample detour alert for graders (#46, #55), naming the stops it skips
the way an admin's detour alert does, so the home page shows the whole
feature without a real detour happening. Created only if no alert has its
title, so a rerun creates nothing twice and never undoes an admin's edit."""

import datetime as dt

from django.utils import timezone

from alerts.models import Alert

TITLE = "Sample: a route is detoured"
MESSAGE = (
    "This sample shows what a detour alert looks like: the stops it skips, "
    "so a rider can ask the driver for the right drop-off. Real ones appear "
    "here when admins post them."
)
SKIPPED = "Chapel St at York St, Broadway at Tower Pkwy, College St at Crown St"
SHOWING_FOR = dt.timedelta(days=60)  # the same window as the alerts demo


def seed(accounts, stdout):
    _, created = Alert.objects.get_or_create(
        title=TITLE,
        defaults={
            "message": MESSAGE,
            "skipped_stops": SKIPPED,
            "starts_at": timezone.now(),
            "ends_at": timezone.now() + SHOWING_FOR,
        },
    )
    stdout.write(f"{'Created' if created else 'Kept'} the demo detour alert {TITLE!r}")
