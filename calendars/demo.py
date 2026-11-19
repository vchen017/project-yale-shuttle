"""Demo calendar events for graders (#8), run by `manage.py seed`.

The rider account gets a demo calendar — the kind the hourly refresh never
touches — with three upcoming events at fixed New York times, so the
when-to-leave page shows all three kinds of plan: a lecture at a building a
route links to Home (rideable), a brunch whose location names nothing in our
buildings list, and a pickup game with no location at all (both listed as
location unknown).

Anchoring to now served only until a deploy landed in the late afternoon:
the page lists events that haven't started, so its evening looked empty to
the very graders the demo is for (Vivian, #126 review). Instead each seed
run re-anchors each event to its next occurrence at a fixed local time, so
something is always coming up, whatever hour the deploy went out — and the
hourly job re-anchors the ones that have since started, so the page stays
filled between deploys too. Nothing else about the events is reset. The demo
Home sits by the College / George stop (routes 1 and 2), a ride away from
Watson Center, and is created only while the rider has no Home, so it never
overrides one. It's written here rather than in `places/demo.py` only
because it exists for this page; if the places app grows its own demo, this
moves.
"""

import datetime as dt
from zoneinfo import ZoneInfo

from django.utils import timezone

from places.models import Place

from .models import Calendar, Event

ET = ZoneInfo("America/New_York")
HOME = {"lat": 41.30589, "lon": -72.93105}  # a minute's walk from College / George (stop 39)
LENGTH = dt.timedelta(minutes=75)
EVENTS = [
    # uid, title, location, the New York time it sits at every day
    ("demo-lecture@yale", "MGT 656 lecture", "Watson Center, 60 Sachem Street", dt.time(16, 0)),
    ("demo-brunch@yale", "Brunch with the cohort", "New Haven Green", dt.time(17, 30)),
    ("demo-basketball@yale", "Pick-up basketball", "", dt.time(19, 0)),
]


def next_at(time_of_day, now):
    """This New York clock time's next occurrence: today while it's still
    ahead, tomorrow once it has passed. Wall-clock arithmetic, so it reads the
    same across a daylight-saving boundary."""
    when = dt.datetime.combine(timezone.localtime(now).date(), time_of_day, tzinfo=ET)
    if when <= now:
        when += dt.timedelta(days=1)
    return when


def seed(accounts, stdout):
    rider = accounts["rider"]
    if not Place.objects.filter(user=rider, is_home=True).exists():
        _, created = Place.objects.get_or_create(
            user=rider, name="Home", defaults={**HOME, "is_home": True}
        )
        stdout.write(
            "Created the demo Home for the rider account"
            if created
            else "Kept the rider's own place named Home (it isn't their Home)"
        )

    calendar, _ = Calendar.objects.get_or_create(user=rider, is_demo=True, defaults={"ics_url": ""})
    now = timezone.now()
    for uid, title, location, time_of_day in EVENTS:
        start = next_at(time_of_day, now)
        event = Event.objects.filter(calendar=calendar, uid=uid).first()
        if event is None:
            Event.objects.create(
                calendar=calendar,
                uid=uid,
                title=title,
                location=location,
                start=start,
                end=start + LENGTH,
            )
            stdout.write(f"Created the demo event {title!r}")
        else:
            # A later deploy re-anchors the times, so the events stay upcoming.
            event.title = title
            event.location = location
            event.start = start
            event.end = start + LENGTH
            event.save(update_fields=["title", "location", "start", "end"])
            stdout.write(f"Re-anchored the demo event {title!r}")


def reanchor(now=None):
    """The demo events whose time has passed move to their next occurrence:
    the hourly job's part of keeping the page filled between deploys. Safe to
    run twice — a moved event is no longer behind now, so it stays put — and
    it leaves alone anything changed since. Returns how many moved, for the
    job's run log."""
    now = now or timezone.now()
    times = {uid: time_of_day for uid, _, _, time_of_day in EVENTS}
    moved = 0
    for event in Event.objects.filter(calendar__is_demo=True, start__lt=now):
        time_of_day = times.get(event.uid)
        if time_of_day is None:
            continue
        event.start = next_at(time_of_day, now)
        event.end = event.start + LENGTH
        event.save(update_fields=["start", "end"])
        moved += 1
    return moved
