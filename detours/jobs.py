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
# An hour is one of a route's usual off-path hours — detection refuses to
# flag the route in it — when ROUTINE_DAYS or more of the window's days show
# one of three shapes there. A real detour is novel: one day, not several,
# so it stays flaggable in every hour the shapes don't cover.
#
# - **The whole route rides off the drawn path** (fraction rule): the hour's
#   recorded fixes sit off it ROUTINE_FRACTION of the time. Purple's
#   central-campus loop and Blue-Night's northern loop run most evenings
#   (17 of 18, #54), and a drawn path that is wrong for an hour (#54) puts
#   the whole route off it.
# - **One bus makes detour-sized runs off it** (run rule): a single bus at
#   least RUN_MIN_SPAN off the path in the hour, day after day. One bus's
#   loop never moves a fifteenth of the route's fixes, so the fraction rule
#   cannot see it — replaying Nov 9-20 at the live two-minute page pace
#   (Vivian, #55 review) flagged the usual evening loops 20 times this way,
#   and Pink's walk to the garage at 6:50 PM on 4 of 12 days.
# - **The day ends off the drawn path** (tail rule): the route's last fresh
#   fix of the service day sits off it, and the TAIL_MINUTES before that
#   fall in the hour. That is the walk back to the garage (Vivian, #55
#   review: 12 of her replay's 35 flags were buses leaving service), where
#   a detour alert cannot name a bus a rider can still catch.
ROUTINE_FRACTION = 0.15
ROUTINE_DAYS = 3
RUN_MIN_SPAN = dt.timedelta(minutes=10)  # three 5-minute fixes in a row span this
RUN_MAX_GAP = dt.timedelta(minutes=12)  # one missed poll doesn't end a run; two do
TAIL_MINUTES = dt.timedelta(minutes=25)  # how long before the day's last fix is the walk home


def service_day(polled_at):
    """The service day a fix belongs to: its Eastern date shifted back twelve
    hours, so the day runs noon to noon and the fixes after midnight Eastern
    stay with the evening that spawned them instead of starting a new day."""
    return (polled_at.astimezone(ET) - dt.timedelta(hours=12)).date()


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
    # route -> hour -> [off fixes, all fixes, {service days with an off fix}]
    counted = defaultdict(lambda: defaultdict(lambda: [0, 0, set()]))
    # route -> hour -> {service days on which a single bus ran >= RUN_MIN_SPAN off the path}
    run_days = defaultdict(lambda: defaultdict(set))
    # route -> hour -> {service days whose walk home overlaps the hour}
    tail_days = defaultdict(lambda: defaultdict(set))
    # (route, service day) -> the day's last fresh fix: (polled_at, off the path)
    last_fix = {}
    runs = {}  # (route, bus) -> the open run: [first off, last off, start hour, service day]

    def close_run(key):
        first_off, last_off, hour, day = runs.pop(key)
        if last_off - first_off >= RUN_MIN_SPAN:
            run_days[key[0]][hour].add(day)

    for polled_at, route_id, bus_id, lat, lon, last_update in fixes:
        if route_id not in routes:
            continue
        age = (polled_at - last_update).total_seconds()
        if age > FRESH_FIX_SECONDS or age < -300:
            continue  # the same freshness rule detection applies live
        path = routes[route_id].get("path") or []
        if not path:
            continue
        hour = polled_at.astimezone(ET).hour
        day = service_day(polled_at)
        off = meters_off_path((lat, lon), path) > OFF_PATH_METERS

        cell = counted[route_id][hour]
        cell[1] += 1
        if off:
            cell[0] += 1
            cell[2].add(day)
        last_fix[(route_id, day)] = (polled_at, off)

        key = (route_id, bus_id)
        if off:
            run = runs.get(key)
            if run and polled_at - run[1] > RUN_MAX_GAP:
                close_run(key)
                run = None
            if run:
                run[1] = polled_at
            else:
                runs[key] = [polled_at, polled_at, hour, day]
        elif key in runs:
            close_run(key)

    for key in list(runs):
        close_run(key)  # a run still open when the history ends
    for (route_id, day), (polled_at, off) in last_fix.items():
        if not off:
            continue  # the day's recorded fixes end on the path: no walk home in them
        for hour in {
            polled_at.astimezone(ET).hour,
            (polled_at - TAIL_MINUTES).astimezone(ET).hour,
        }:
            tail_days[route_id][hour].add(day)

    usual = {}
    for route_id, hours in counted.items():
        marked = {
            hour
            for hour, (off, total, days) in hours.items()
            if total >= MIN_FIXES and off / total >= ROUTINE_FRACTION and len(days) >= ROUTINE_DAYS
        }
        marked |= {hour for hour, days in run_days.get(route_id, {}).items() if len(days) >= ROUTINE_DAYS}
        marked |= {hour for hour, days in tail_days.get(route_id, {}).items() if len(days) >= ROUTINE_DAYS}
        if marked:
            usual[route_id] = sorted(marked)
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
