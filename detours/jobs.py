"""The detours app's scheduled jobs (see jobs/registry.py, #40)."""

import datetime as dt
from collections import defaultdict

from django.utils import timezone

from history.models import BusPosition
from jobs.registry import job
from transit import downtowner

from .detect import (
    ET,
    FRESH_FIX_SECONDS,
    OFF_PATH_METERS,
    meters_off_path,
)
from .models import UsualOffPath

WINDOW_DAYS = 14  # how much recorded history the usual shapes read
MIN_FIXES = 20  # an hour with fewer fixes than this says nothing either way
# An hour counts as a route's usual off-path shape when its recorded fixes
# are off the drawn path this often, on at least ROUTINE_DAYS of the window's
# days: Purple's central-campus loop and Blue-Night's northern loop run most
# evenings (17 of 18, #54), and the walk back to the garage closes most
# routes' days (Vivian, #55 review). A real detour is novel — one day, not
# half the window — so it stays flaggable.
ROUTINE_FRACTION = 0.15
ROUTINE_DAYS = 3


@job(schedule="41 9 * * *", timeout=dt.timedelta(minutes=45))
def record_usual_shapes():
    """Sum up when each route is routinely off its drawn path (#55): daily,
    9:41 UTC (4:41 AM Eastern), after the import has brought in yesterday's
    history and before the predictions' nightly jobs. Detection refuses to
    flag a route in one of its usual hours, so the evening loops and the
    end-of-service walks to the garage stop flagging while a novel detour
    still does. Rerunning it rewrites the table, so it is safe to run late
    or twice; until its first run, nothing is suppressed."""
    routes = {
        route["id"]: route
        for route in downtowner.routes()
        if isinstance(route, dict) and isinstance(route.get("id"), int)
    }
    since = timezone.now() - dt.timedelta(days=WINDOW_DAYS)
    fixes = (
        BusPosition.objects.filter(polled_at__gte=since, last_update__isnull=False)
        .order_by("polled_at")
        .values_list("polled_at", "route_id", "bus_id", "lat", "lon", "last_update")
    )
    counted = defaultdict(lambda: defaultdict(lambda: [0, 0, set()]))
    for polled_at, route_id, _bus_id, lat, lon, last_update in fixes:
        if route_id not in routes:
            continue
        age = (polled_at - last_update).total_seconds()
        if age > FRESH_FIX_SECONDS or age < -300:
            continue  # the same freshness rule detection applies live
        path = routes[route_id].get("path") or []
        if not path:
            continue
        cell = counted[route_id][polled_at.astimezone(ET).hour]
        cell[1] += 1
        if meters_off_path((lat, lon), path) > OFF_PATH_METERS:
            cell[0] += 1
            cell[2].add(polled_at.date())

    usual = {
        route_id: [
            hour
            for hour, (off, total, days) in sorted(hours.items())
            if total >= MIN_FIXES and off / total >= ROUTINE_FRACTION and len(days) >= ROUTINE_DAYS
        ]
        for route_id, hours in counted.items()
    }
    usual = {route_id: hours for route_id, hours in usual.items() if hours}
    now = timezone.now()
    for route_id, hours in usual.items():
        UsualOffPath.objects.update_or_create(
            route_id=route_id,
            defaults={"hours": hours, "window_days": WINDOW_DAYS, "updated_at": now},
        )
    stale = UsualOffPath.objects.exclude(route_id__in=usual.keys())
    stale_routes = list(stale.values_list("route_id", flat=True))
    stale.delete()
    found = ", ".join(f"route {r} at {h}" for r, h in sorted(usual.items())) or "no usual off-path hours"
    dropped = f"; dropped {stale_routes}" if stale_routes else ""
    return f"Usual shapes from {WINDOW_DAYS} days: {found}{dropped}"
