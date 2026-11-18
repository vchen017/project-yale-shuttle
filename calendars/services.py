"""Connecting, refreshing and reading calendars, between the views and the
models (#25). The refresh replaces a calendar's events wholesale, so it's safe
to run again, late, or after a skipped run."""

import datetime as dt

from django.db import transaction
from django.utils import timezone

from . import ical
from .models import Calendar, Event

LOOK_BACK = dt.timedelta(seconds=3600)  # behind now to keep importing: an
# event that started minutes ago still matters on the when-to-leave page.


def connection(user):
    """The rider's connected calendar, or None when they haven't connected one."""
    return user.calendars.filter(is_demo=False).first()


def save_url(user, url):
    """Connect the rider's calendar to this address, replacing any old one."""
    calendar = connection(user)
    if calendar:
        calendar.ics_url = url
        calendar.last_imported_at = None
        calendar.last_error = ""
        calendar.save(update_fields=["ics_url", "last_imported_at", "last_error"])
        return calendar
    return Calendar.objects.create(user=user, ics_url=url)


def remove(user):
    """Forget the rider's calendar and its events. Demo calendars stay."""
    Calendar.objects.filter(user=user, is_demo=False).delete()


def refresh(calendar, data=None):
    """Read the calendar's address and replace its events. Returns how many
    events were imported; raises CalendarUnavailable and touches nothing when
    the file can't be read."""
    if data is None:
        data = ical.fetch(calendar.ics_url)
    now = timezone.now()
    events = ical.parse(data, now - LOOK_BACK, now + dt.timedelta(days=ical.WINDOW_DAYS))
    with transaction.atomic():
        calendar.events.all().delete()
        Event.objects.bulk_create(Event(calendar=calendar, **event) for event in events)
        calendar.last_imported_at = now
        calendar.last_error = ""
        calendar.save(update_fields=["last_imported_at", "last_error"])
    return len(events)


def try_refresh(calendar, data=None):
    """refresh, recording a failure on the calendar instead of raising, so one
    unreadable calendar never breaks a page or the hourly job. Returns how
    many events were imported, 0 when it failed."""
    try:
        return refresh(calendar, data)
    except ical.CalendarUnavailable as error:
        calendar.last_error = str(error)[:200]
        calendar.save(update_fields=["last_error"])
        return 0


def refresh_all():
    """Every connected calendar, for the hourly job. Returns a summary for the
    job's run log."""
    calendars = Calendar.objects.filter(is_demo=False)
    imported = failed = 0
    for calendar in calendars:
        events = try_refresh(calendar)
        if calendar.last_error:
            failed += 1
        else:
            imported += events
    return f"Refreshed {calendars.count()} calendars: {imported} events imported, {failed} failed"


def upcoming(user, now=None, hours=24):
    """The rider's events in the next `hours` hours, soonest first — their
    calendar's, plus any demo events (seed) on their account."""
    now = now or timezone.now()
    return Event.objects.filter(
        calendar__user=user,
        start__range=(now, now + dt.timedelta(hours=hours)),
    ).select_related("calendar").order_by("start")
