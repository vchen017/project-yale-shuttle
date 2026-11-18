"""Fetch and read a rider's ICS calendar (#25).

The server fetches the address the rider pasted — no Google sign-in — and
reads it with icalendar, which handles the folding, escaping and time zones of
the iCalendar format, and recurring-ical-events, which expands the repeats
(class schedules repeat weekly). Floating times, the ones with no time zone in
the file, are read as Eastern: that's where our riders are.

Tests never reach the network: they patch `calendars.ical.urlopen`, the same
way transit patches the Downtowner feed, or pass the file's bytes straight to
`parse`.
"""

import datetime as dt
from urllib.request import urlopen
from uuid import uuid4
from zoneinfo import ZoneInfo

import icalendar
import recurring_ical_events

ET = ZoneInfo("America/New_York")
TIMEOUT = 5  # seconds, the same as the other outside calls
WINDOW_DAYS = 7  # imported ahead; the when-to-leave page looks at 24 hours


class CalendarUnavailable(Exception):
    """The calendar's server didn't answer, or what it sent isn't a calendar."""


def fetch(url):
    """The calendar file at the address, or CalendarUnavailable."""
    try:
        with urlopen(url, timeout=TIMEOUT) as response:
            return response.read()
    except Exception as error:
        raise CalendarUnavailable(f"We couldn't reach that address ({error}).") from error


def parse(data, window_start, window_end):
    """The events in the file between two aware datetimes, soonest first:
    [{"uid", "title", "start", "end", "location"}], datetimes aware."""
    try:
        calendar = icalendar.Calendar.from_ical(data)
        # One event without a start would break the expansion for all of them.
        calendar.subcomponents = [
            part for part in calendar.subcomponents
            if part.name != "VEVENT" or part.get("DTSTART") is not None
        ]
        occurrences = recurring_ical_events.of(calendar).between(window_start, window_end)
    except ValueError as error:
        # icalendar raises ValueError for a file it can't read; that covers
        # HTML error pages and other answers that aren't calendars.
        raise CalendarUnavailable("That address doesn't serve a calendar we can read.") from error

    events = []
    for occurrence in occurrences:
        try:
            start = _aware(occurrence["DTSTART"].dt)
        except (KeyError, ValueError, TypeError):
            continue  # one malformed event shouldn't lose the rest
        events.append({
            "uid": _uid(occurrence),
            "title": str(occurrence.get("SUMMARY") or "Untitled event")[:200],
            "start": start,
            "end": _aware(occurrence["DTEND"].dt) if occurrence.get("DTEND") else _end_from_duration(occurrence, start),
            "location": str(occurrence.get("LOCATION") or "")[:300],
        })
    events.sort(key=lambda event: event["start"])
    return events


def _aware(value):
    """Anything the file calls a time, as an aware datetime: a plain date is
    an all-day event and becomes midnight Eastern, a floating time is Eastern,
    and UTC and named zones stay as they are."""
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        value = dt.datetime.combine(value, dt.time())
    if value.tzinfo is None:
        value = value.replace(tzinfo=ET)
    return value


def _end_from_duration(occurrence, start):
    """An event may say how long it lasts instead of when it ends."""
    duration = occurrence.get("DURATION")
    if duration is None:
        return None
    try:
        return start + duration.dt
    except TypeError:
        return None


def _uid(occurrence):
    """The event's UID, or a fresh one when the file left it out, so two
    events without UIDs never claim the same row."""
    uid = str(occurrence.get("UID") or "").strip()
    return uid or f"generated-{uuid4().hex}"
