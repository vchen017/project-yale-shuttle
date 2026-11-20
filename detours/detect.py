"""Routes that look detoured right now (#55).

Detection samples the live feed: every time a page computes the flags it
records what the buses' positions say now, and keeps the last few samples in
the cache — they outlive the minute-long answer cache, so a streak survives
between requests. A route shows "possible detour" when one of its buses was
on the route's official path — the line in the feed's routes answer, the same
one the home page's map draws — and has stayed farther than OFF_PATH_METERS
from it through an off-run that spans at least MIN_SPAN of the last
SAMPLE_SPAN: at least about 10 and at most about 15 minutes, which is what
makes the alert's "about the last 15 minutes" true. Samples land at most one
per SAMPLE_SPACING, so at the densest page traffic an off-run needs
CONSECUTIVE_POLLS or more appearances to get that long.

Deliberately conservative, because buses spend time off their path without
detouring:

- **A bus coming from or going to the garage was never on the path** in our
  samples (Vivian, #55 review): replaying the recorded polls flagged 23 short
  "possible detours" on 8 of 9 days, every one at the start or end of a
  route's service. Only a bus seen on the path first can flag its route, and
  a bus that misses a sample — in the garage, or gone from the feed — starts
  over when it reappears.
- **A stale fix says nothing about now**: a bus whose GPS hasn't reported in
  FRESH_FIX_SECONDS is left out (the position can be minutes old, #58).
- **One bus's excursion can flag its route**: the rule is per bus — a bus
  that was on the path and then stayed off it for CONSECUTIVE_POLLS or more
  appearances in a row, spanning at least MIN_SPAN, flags its route however
  its siblings are running, because riders on that bus's stops are the ones
  affected. (The committed version required every bus off in every poll and
  missed these; replaying the recorded polls (#22), Purple's bus sat 15
  minutes off its official path on one loop after another through the
  evening of Nov 12, and the old rule never said a word because its sibling
  ran the route as drawn.) What a route is never flagged on is a bus that
  was never on the path — fresh from the garage (Vivian, #55 review) — an
  off-run too short to be honest about "15 minutes" (Austin, #55 review),
  a route with no fresh buses, or a route without an official path to leave.
- Samples closer together than SAMPLE_SPACING count as one, so a rider
  refreshing a page can't compress "15 minutes" into seconds, and samples
  older than SAMPLE_SPAN are dropped.

The word is "possible" on purpose — a stale or moved official path can flag
a healthy route — so this is a heads-up to riders, never a verdict.

The only network read is the feed read pages already make (transit.downtowner
caches each answer for 30 seconds), and it raises
transit.downtowner.FeedUnavailable when the feed is down, so pages answer as
they already do then. Nothing here touches the database: a few small cache
reads and writes per request, no scheduled job.
"""

import datetime as dt
import math

from django.core.cache import cache
from django.utils import timezone

from transit import downtowner

CACHE_SECONDS = 60  # the computed flags; the home page's first paint reads only these
SAMPLES_SECONDS = 20 * 60  # the raw samples outlive the flags so streaks survive
SAMPLE_SPAN = dt.timedelta(minutes=15)  # samples older than this stop counting
SAMPLE_SPACING = dt.timedelta(minutes=2)  # one sample at most this often
MIN_SPAN = dt.timedelta(minutes=10)  # an off-run must span at least this, first off
# appearance to now (Austin, #55 review): three appearances two minutes apart is
# four minutes, and the alert's "about the last 15 minutes" wouldn't be true
CONSECUTIVE_POLLS = 3  # off the path this many appearances in a row, after being on it
OFF_PATH_METERS = 200  # farther than this from the official path is off it
FRESH_FIX_SECONDS = 180  # a GPS fix older than this says nothing about right now

_CACHE_KEY = "detours:flagged-routes"
_SAMPLES_KEY = "detours:position-samples"


def detoured_routes(now=None):
    """The running routes that look detoured, as
    [{"route_id", "route_name", "bus_count", "since"}], with the buses counted
    from the newest sample and since that sample's time (what riders see as
    when the detour was noticed). [] until some bus's off-run has CONSECUTIVE_POLLS
    or more appearances spanning at least MIN_SPAN of the last SAMPLE_SPAN:
    less than that, and "about the last 15 minutes" would be a guess.

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
    samples = record_sample(_positions_from_feed(now.timestamp()), now)
    flagged = flags_from_samples(samples, active, now)
    cache.set(_CACHE_KEY, flagged, CACHE_SECONDS)
    return flagged


def cached_detoured_routes():
    """What detoured_routes found last, straight from the cache: [] unless a
    detection ran in the last minute. The home page's first paint reads only
    this, so it never waits on the feed (#13); the page then swaps in the
    full alert list, which computes it for real."""
    return cache.get(_CACHE_KEY, [])


def _positions_from_feed(now_ts):
    """Where the buses are now, freshest fixes only, as
    {route_id: {bus_id: (lat, lon)}}. A bus without a usable id, route,
    position or recent lastUpdate is left out (the position can be minutes
    old, #58), and so is everything when the buses feed is down —
    downtowner.buses() raises, and detoured_routes passes that on."""
    buses = downtowner.buses()
    positions = {}
    for bus in buses if isinstance(buses, list) else []:
        try:
            route_id, bus_id = int(bus["route"]), int(bus["id"])
            updated = float(bus["lastUpdate"])
            lat, lon = float(bus["lat"]), float(bus["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if now_ts - updated > FRESH_FIX_SECONDS:
            continue
        positions.setdefault(route_id, {})[bus_id] = (lat, lon)
    return positions


def record_sample(positions, now):
    """Fold one moment's bus positions into the cached samples, and return
    every sample still in SAMPLE_SPAN, oldest first, as
    [{"at": unix seconds, "routes": {route_id: {bus_id: off_the_path}}}.
    Samples closer together than SAMPLE_SPACING count as one — the newest
    positions win nothing by arriving twice — and anything older than the
    span plus a sample is dropped. Whether a bus was off its route's path is
    judged here, where the live paths are at hand, and stored per bus so the
    flagging itself stays pure (and testable)."""
    now_ts = now.timestamp() if isinstance(now, dt.datetime) else now
    samples = cache.get(_SAMPLES_KEY, [])
    samples = [s for s in samples if now_ts - s["at"] <= SAMPLE_SPAN.total_seconds()]
    if not samples or now_ts - samples[-1]["at"] >= SAMPLE_SPACING.total_seconds():
        samples.append({"at": now_ts, "routes": _off_by_bus(positions)})
        cache.set(_SAMPLES_KEY, samples, SAMPLES_SECONDS)
    return samples


def flags_from_samples(samples, active, now):
    """The flagged routes from recorded samples, as detoured_routes returns
    them. Pure, so the recorded history (#22) can replay it and tests can
    drive it directly: `samples` is record_sample's output, `active` the
    running routes by id from the feed, `now` an aware datetime."""
    now_ts = now.timestamp() if isinstance(now, dt.datetime) else now
    recent = [s for s in samples if now_ts - s["at"] <= SAMPLE_SPAN.total_seconds()]
    if len(recent) < CONSECUTIVE_POLLS:
        return []
    flagged = []
    for route_id, route in active.items():
        path = route.get("path") or []
        if not path:
            continue  # no official path to leave: never flagged (#55)
        streaks = _bus_streaks(recent, route_id)
        if any(
            streak["saw_on_path"]
            and streak["off_run"] >= CONSECUTIVE_POLLS
            and recent[-1]["at"] - streak["off_since"] >= MIN_SPAN.total_seconds()
            for streak in streaks.values()
        ):
            flagged.append(
                {
                    "route_id": route_id,
                    "route_name": route.get("name") or f"Route {route_id}",
                    "bus_count": len(recent[-1]["routes"].get(route_id) or {}),
                    "since": dt.datetime.fromtimestamp(recent[-1]["at"], tz=dt.timezone.utc),
                }
            )
    return flagged


def _bus_streaks(samples, route_id):
    """Each bus's run of off-path appearances on one route, oldest sample
    first: {bus_id: {"saw_on_path", "off_run", "off_since"}} — off_since the
    time of the first appearance of the current off-run, which flags_from_samples
    holds against MIN_SPAN. A bus flags its route only after being on the path
    (buses from the garage never were, Vivian, #55 review) and then staying
    farther than OFF_PATH_METERS from it for CONSECUTIVE_POLLS appearances in
    a row across at least MIN_SPAN (Austin, #55 review); a sample the bus
    missed — in the garage, or gone from the feed — starts its run over."""
    streaks = {}
    for sample in samples:
        buses = sample["routes"].get(route_id) or {}
        for bus_id in list(streaks):
            if bus_id not in buses:
                streaks[bus_id]["off_run"] = 0
                streaks[bus_id]["off_since"] = None
        for bus_id, off in buses.items():
            streak = streaks.setdefault(
                bus_id, {"saw_on_path": False, "off_run": 0, "off_since": None}
            )
            if off:
                if streak["off_run"] == 0:
                    streak["off_since"] = sample["at"]
                streak["off_run"] += 1
            else:
                streak["off_run"] = 0
                streak["off_since"] = None
                streak["saw_on_path"] = True
    return streaks


def _off_by_bus(positions):
    """Which of one moment's buses are off their route's official path, as
    {route_id: {bus_id: off}} — only routes the feed gives a path for, since
    a route with no path has nothing to leave. Route entries the feed answer
    carries without a usable id are left out."""
    routes = {
        route["id"]: route
        for route in downtowner.routes()
        if isinstance(route, dict) and isinstance(route.get("id"), int)
    }
    return {
        route_id: {
            bus_id: meters_off_path(position, routes[route_id].get("path") or []) > OFF_PATH_METERS
            for bus_id, position in buses.items()
        }
        for route_id, buses in positions.items()
        if route_id in routes
    }


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
        return math.hypot(*_flat(point, corners[0]))
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
