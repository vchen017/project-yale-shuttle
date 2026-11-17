"""Score prediction accuracy and save it for the dashboard (#17).

The nightly job runs this after the history import; the dashboard only ever
reads what it saved, because scoring takes minutes and no request may wait
for that. Run it by hand with --from and --to to score a range the nightly
windows don't cover.
"""

import datetime as dt

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from history import accuracy, snapshots


def windows_for(today):
    """The ranges a nightly run scores: the dashboard's default week every
    day, and on Mondays also the longest range (a whole term), since weeks
    change nightly but the term's picture barely moves in a day."""
    week = (today - dt.timedelta(days=6), today)
    if today.weekday() == 0:  # Monday
        return [week, (today - dt.timedelta(days=30), today)]
    return [week]


class Command(BaseCommand):
    help = "Score prediction accuracy for the dashboard and save the result (#17)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--from", dest="start", type=dt.date.fromisoformat, default=None,
            help="Range start, YYYY-MM-DD Eastern (with --to)",
        )
        parser.add_argument(
            "--to", dest="end", type=dt.date.fromisoformat, default=None,
            help="Range end, YYYY-MM-DD Eastern (with --from)",
        )

    def handle(self, *args, **options):
        start, end = options["start"], options["end"]
        if (start is None) != (end is None):
            raise CommandError("Give both --from and --to, or neither.")
        if start is None:
            ranges = windows_for(timezone.localdate())
        else:
            ranges = [(start, end)]
        for start, end in ranges:
            if start > end:
                raise CommandError(f"{start} is after {end}.")
            if (end - start).days >= accuracy.MAX_RANGE_DAYS:
                raise CommandError(
                    f"{start} to {end} is longer than {accuracy.MAX_RANGE_DAYS} days; split it."
                )
            self.stdout.write(snapshots.describe(snapshots.score(start, end)))
