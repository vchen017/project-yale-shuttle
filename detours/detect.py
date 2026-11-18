"""Routes that look detoured right now (#55).

A route shows "possible detour" when every bus on it has been farther than
OFF_PATH_METERS from the route's official path — the line in the feed's
routes answer, the same one the home page's map draws — in each of the
CONSECUTIVE_POLLS most recent recorded polls (#22), about 15 minutes at one
poll every five minutes.

Deliberately conservative, because one bus sometimes drives another route's
loop (#54): a single wandering bus never flags its route, and a poll where
the route shows no buses breaks the streak. The word is "possible" on
purpose — a stale or moved official path can flag a healthy route — so this
is a heads-up to riders, never a verdict.

The paths come from the live feed; the positions come from the recorded
polls in our own database. The only network read is the feed read pages
already make, and it raises transit.downtowner.FeedUnavailable when the
feed is down, so pages answer as they already do then. Results are cached
for a minute: three small database reads at most, no scheduled job, well
inside the hourly budget jobs keep to.
"""

import datetime as dt
import math
from collections import defaultdict

from django.core.cache import cache
from django.utils import timezone

from history.models import BusPosition, Poll
from transit import downtowner
from transit.trips import meters_between

CACHE_SECONDS = 60
OFF_PATH_METERS = 200  # farther than this from the official path is off it
CONSECUTIVE_POLLS = 3  # the story: three polls in a row (~15 minutes)
FRESH_HOURS = 2  # polls older than this say nothing about right now

_CACHE_KEY = "detours:flagged-routes"


def detoured_routes(now=None):
    """The running routes that look detoured, as
    [{"route_id", "route_name", "bus_count", "since"}], with the newest
    poll's buses counted and since the newest poll's time (what riders see
    as when the detour was noticed). [] when the recorded history has fewer
    than CONSECUTIVE_POLLS recent polls: not enough to say "in a row".

    Raises downtowner.FeedUnavailable when the feed is down.
    """
    now = now or timezone.now()
    cached = cache.get(_CACHE_KEY)
    if cached is not None:
        return cached

    active = {
        route["id"]: route
        for route in downtowner.routes()
        if isinstance(route, dict) and route.get("active") and isinstance(route.get("id"), int)
    }
    polls = list(
        Poll.objects.filter(
            status=Poll.Status.RECORDED,
            polled_at__lte=now,
            polled_at__gte=now - dt.timedelta(hours=FRESH_HOURS),
        ).order_by("-polled_at")[:CONSECUTIVE_POLLS]
    )
    flagged = []
    if len(polls) == CONSECUTIVE_POLLS:
        by_poll = defaultdict(list)
        for position in BusPosition.objects.filter(poll_id__in=[poll.id for poll in polls]):
            by_poll[position.poll_id].append(position)

        for route_id, route in active.items():
            path = route.get("path") or []
            per_poll = [
                [p for p in by_poll[poll.id] if p.route_id == route_id] for poll in polls
            ]
            # Every poll needs buses, and every bus in every poll has to be off
            # the path: one bus back on it, or one poll with none running,
            # breaks the streak (#54, one bus straying isn't a detour).
            if all(per_poll) and all(
                meters_off_path((p.lat, p.lon), path) > OFF_PATH_METERS
                for buses in per_poll
                for p in buses
            ):
                flagged.append(
                    {
                        "route_id": route_id,
                        "route_name": route.get("name") or f"Route {route_id}",
                        "bus_count": len(per_poll[0]),
                        "since": polls[0].polled_at,
                    }
                )
    cache.set(_CACHE_KEY, flagged, CACHE_SECONDS)
    return flagged


def cached_detoured_routes():
    """What detoured_routes found last, straight from the cache: [] unless a
    detection ran in the last minute. The home page's first paint reads only
    this, so it never waits on the feed (#13); the page then swaps in the
    full alert list, which computes it for real."""
    return cache.get(_CACHE_KEY, [])


def meters_off_path(point, path):
    """The least meters from a (lat, lon) point to a route's path — the
    feed's flat [lat, lon, lat, lon, ...] list — measured to the nearest
    point on the nearest segment, not just the nearest corner. 0.0 for a
    path too thin to describe a line (nothing, or a single corner): with no
    official path to leave, we never claim a detour."""
    corners = [(path[i], path[i + 1]) for i in range(0, len(path) - 1, 2)]
    if not corners:
        return 0.0
    if len(corners) == 1:
        return meters_between(point, corners[0])
    return min(_segment_meters(point, a, b) for a, b in zip(corners, corners[1:]))


def _segment_meters(point, a, b):
    """Meters from a (lat, lon) point to the segment a-b, flat because New
    Haven is small: the usual projection onto the segment, in a local plane."""
    ax, ay = _flat(a, a)
    bx, by = _flat(b, a)
    px, py = _flat(point, a)
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _flat(point, anchor):
    """(x, y) meters of a (lat, lon) point in a plane pinned at `anchor` —
    exact enough across a city, and only ever compared within one path."""
    meters_per_degree = 111_320.0
    return (
        (point[1] - anchor[1]) * meters_per_degree * math.cos(math.radians(anchor[0])),
        (point[0] - anchor[0]) * meters_per_degree,
    )
