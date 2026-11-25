"""The notifications app's scheduled jobs (#29)."""

import datetime as dt

from jobs.registry import job

from . import trips


@job(schedule=trips.SCHEDULE, timeout=trips.WATCH + dt.timedelta(minutes=5), takes_over=True)
def notify_trips():
    """Watch riders' trips in the next 2 hours and send the notifications the
    agent decides on (#29): hourly, 4 minutes after the calendar refresh, so
    the database it wakes is usually still awake. Each run watches until the
    next one takes over, for up to 2 hours."""
    return trips.watch()
