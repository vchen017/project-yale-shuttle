"""Demo calendar events for graders (#8), run by `manage.py seed`.

The rider account gets a demo calendar — the kind the hourly refresh never
touches — with three events in the next few hours, so the when-to-leave page
shows all three kinds of plan: a lecture at a building a route links to Home
(rideable), a brunch whose location names nothing in our buildings list, and
a pickup game with no location at all (both listed as location unknown).

Each seed run re-anchors the events' times to now, so a deploy days later
still shows them as upcoming; nothing else about them is reset. The demo Home
sits by the College / George stop (routes 1 and 2), a ride away from Watson
Center, and is created only while the rider has no Home, so it never
overrides one. It's written here rather than in `places/demo.py` only because
it exists for this page; if the places app grows its own demo, this moves.
"""

import datetime as dt

from django.utils import timezone

from places.models import Place

from .models import Calendar, Event

HOME = {"lat": 41.30589, "lon": -72.93105}  # a minute's walk from College / George (stop 39)
LENGTH = dt.timedelta(minutes=75)
EVENTS = [
    # uid, title, location, how long from now
    ("demo-lecture@yale", "MGT 656 lecture", "Watson Center, 60 Sachem Street", dt.timedelta(hours=2)),
    ("demo-brunch@yale", "Brunch with the cohort", "New Haven Green", dt.timedelta(hours=5)),
    ("demo-basketball@yale", "Pick-up basketball", "", dt.timedelta(hours=7)),
]


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
    for uid, title, location, after in EVENTS:
        start = (now + after).replace(second=0, microsecond=0)
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
