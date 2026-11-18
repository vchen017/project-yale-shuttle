"""Riders' connected calendars and the events imported from them (#25).

A rider pastes one ICS address; the server fetches it and keeps the upcoming
events, refreshed at least hourly. Events are stored as separate occurrences,
because class schedules repeat: a weekly lecture lands once per week it's
imported, each with the same UID and its own start.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Calendar(models.Model):
    """A rider's connected calendar: the ICS address the server fetches (#25)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calendars"
    )
    ics_url = models.URLField("ICS address", max_length=500)
    # Demo data for graders (seed): the refresh job leaves demo calendars alone.
    is_demo = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    # When the last refresh read events, and what went wrong on the latest one.
    last_imported_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        constraints = [
            # One connected address per rider; demo calendars don't count.
            models.UniqueConstraint(
                fields=["user"], condition=Q(is_demo=False), name="calendars_one_per_rider"
            ),
        ]

    def __str__(self):
        return f"{self.user} · {self.ics_url}"


class Event(models.Model):
    """One occurrence of an event, as the calendar's last refresh saw it.

    A repeating event (a weekly class) gives one row per occurrence, all with
    the same UID. A refresh replaces a calendar's events wholesale, so these
    rows are a mirror of the file, not a history.
    """

    calendar = models.ForeignKey(Calendar, on_delete=models.CASCADE, related_name="events")
    uid = models.CharField(max_length=255)  # the event's UID in the file
    title = models.CharField(max_length=200)
    start = models.DateTimeField()  # UTC, like every datetime we store
    end = models.DateTimeField(null=True, blank=True)
    location = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        ordering = ["start"]
        constraints = [
            models.UniqueConstraint(
                fields=["calendar", "uid", "start"], name="calendars_unique_occurrence"
            ),
        ]
        indexes = [models.Index(fields=["calendar", "start"])]

    def __str__(self):
        return f"{self.title} at {self.start:%Y-%m-%d %H:%M} UTC"
