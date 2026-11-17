"""Riders' reports of a bus that never came or skipped their stop (#6).

`report` saves one, at most one per rider, route and stop every 15 minutes;
`recent` lists what the page shows; `running_routes` is the feed's running
routes with their stops, as the report form wants them.
"""

import datetime as dt

from django.utils import timezone

from transit import downtowner

from .models import RiderReport

RATE_LIMIT = dt.timedelta(minutes=15)
RECENT_HOURS = 24


class TooSoon(Exception):
    """The rider already reported this route and stop inside the rate limit.

    args[0] is when they may report it again."""


def running_routes():
    """The routes running now and the stops each serves, as the form wants
    them: [{"id", "name", "stops": [{"id", "name"}]}].

    Raises downtowner.FeedUnavailable when the feed is down.
    """
    routes = [route for route in downtowner.routes() if route.get("active")]
    stop_names = {stop["id"]: stop["name"] for stop in downtowner.stops()}
    return [
        {
            "id": route["id"],
            "name": route["name"],
            "stops": [
                {"id": stop_id, "name": stop_names[stop_id]}
                for stop_id in route.get("stops") or []
                if stop_id in stop_names
            ],
        }
        for route in routes
    ]


def report(user, route, stop, kind, now=None):
    """Save one rider's report of a missed bus, at the current time (#6).

    `route` and `stop` are the form's dicts ({"id", "name", ...}). Raises
    TooSoon when the same rider reported the same route and stop inside the
    rate limit; sample reports never count towards it.
    """
    now = now or timezone.now()
    recent = (
        RiderReport.objects.filter(
            user=user,
            route_id=route["id"],
            stop_id=stop["id"],
            is_sample=False,
            created_at__gt=now - RATE_LIMIT,
        )
        .order_by("-created_at")
        .first()
    )
    if recent:
        raise TooSoon(recent.created_at + RATE_LIMIT)
    return RiderReport.objects.create(
        user=user,
        route_id=route["id"],
        route_name=route["name"],
        stop_id=stop["id"],
        stop_name=stop["name"],
        kind=kind,
        created_at=now,
    )


def recent(now=None, hours=RECENT_HOURS):
    """The reports of the last `hours` hours, newest first: what the page lists."""
    return RiderReport.objects.filter(
        created_at__gt=(now or timezone.now()) - dt.timedelta(hours=hours)
    )
