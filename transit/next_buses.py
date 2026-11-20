"""The next buses from one stop, optionally to another, for the next-buses page
(#24, story #5).

Each route that serves both stops gets a row: its name and colour, the
official ETAs from the live feed (transit.downtowner.etas), and our own
prediction from history.predictions.predict_minutes (#23), or None when we
have none. Routes are identified by their numeric id, never their short name.

A bus's position can be minutes old (#58): the feed's last-update time says
how old. The soonest bus in a row shows "last seen N min ago" once that's more
than STALE_AFTER_MINUTES, and past HIDE_PREDICTION_AFTER_MINUTES we don't show
our prediction, since a frozen bus tells us nothing about where the route is.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from django.utils import timezone

from detours.detect import detoured_routes
from history.predictions import current_stops_by_route, predict_minutes

from . import downtowner
from .network import network, text_color

LATER_BUSES = 2  # official times shown after the next one
STALE_AFTER_MINUTES = 3  # older than this, say when the bus was last seen
HIDE_PREDICTION_AFTER_MINUTES = 10  # older than this, don't show our prediction


@dataclass
class Row:
    route_id: int
    name: str
    label: str
    color: str  # six hex digits, already checked by network()
    official: list = field(default_factory=list)  # minutes, soonest first
    # True when the bus positions suggest this route has left its official
    # path for the last 15 minutes of live samples (detours.detect, #55). A
    # badge, never a verdict: the feed's path can be stale (#54).
    detoured: bool = False
    ours: int | None = None  # whole minutes, or None when we have no prediction
    # Whole minutes since the soonest bus's GPS last reported, once that's
    # older than STALE_AFTER_MINUTES; None when it's fresh or we can't tell.
    last_seen: int | None = None
    ours_hidden: bool = False  # we had a prediction but the bus's position is too old

    @property
    def text_color(self):
        """Dark text on a light route colour, white on a dark one."""
        return text_color(self.color)

    @property
    def next_official(self):
        return self.official[0] if self.official else None

    @property
    def expected(self):
        """The one number to plan around for the soonest bus: our prediction
        when we have one, else the feed's countdown, else None. The trip
        page's answer (transit.answer) reads only this, so when the rule for
        choosing between the two changes (#117 and after), this is the
        place to change it."""
        return self.ours if self.ours is not None else self.next_official

    @property
    def later_official(self):
        return self.official[1 : 1 + LATER_BUSES]

    @property
    def soonest(self):
        """For sorting: the earliest time we can tell the rider, from either source."""
        times = [t for t in (self.next_official, self.ours) if t is not None]
        return min(times) if times else float("inf")


@dataclass
class Board:
    running: bool  # False late at night, when no route runs
    rows: list  # Row, soonest first


def served_stops():
    """The stops the routes running now serve (all routes' stops when none
    runs), by name: [{"id", "name", "routes": [route ids]}].

    Raises downtowner.FeedUnavailable when the feed is down.
    """
    stops = network()["stops"]
    return sorted(stops, key=lambda stop: stop["name"].casefold())


def bus_ages(now=None):
    """Seconds since each bus's last position, by bus id: {bus_id: seconds}.

    The feed's lastUpdate is a Unix time in seconds. A bus without a usable
    one is left out, and so is everything when the buses feed is down: the
    page then simply shows no ages, as it did before (the arrival times come
    from a different feed answer and still work).
    """
    now = (now or timezone.now()).timestamp()
    try:
        buses = downtowner.buses()
    except downtowner.FeedUnavailable:
        return {}
    ages = {}
    for bus in buses if isinstance(buses, list) else []:
        try:
            ages[int(bus["id"])] = max(0, now - float(bus["lastUpdate"]))
        except (KeyError, TypeError, ValueError):
            continue
    return ages


def next_buses(stop_id, destination_id=None):
    """The next buses at the stop, on routes that also serve the destination.

    Raises downtowner.FeedUnavailable when the feed is down.
    """
    net = network()
    if not net["running"]:
        return Board(running=False, rows=[])

    routes_at = {stop["id"]: set(stop["routes"]) for stop in net["stops"]}
    serving = routes_at.get(stop_id, set())
    if destination_id is not None:
        serving &= routes_at.get(destination_id, set())
    if not serving:
        return Board(running=True, rows=[])

    try:
        detoured = {flag["route_id"] for flag in detoured_routes()}
    except downtowner.FeedUnavailable:
        detoured = set()  # the board still shows; the badge may just be missing

    minutes = defaultdict(list)
    soonest_bus = {}  # route id -> bus id of its soonest arrival
    for arrival in downtowner.etas(stop_id):  # soonest first
        if arrival["minutes"] >= 0:
            minutes[arrival["route_id"]].append(arrival["minutes"])
            soonest_bus.setdefault(arrival["route_id"], arrival["bus_id"])
    ages = bus_ages()
    # One read of every walked route's newest stop list for all the rows'
    # served checks, instead of one query per route (the same batch the
    # accuracy scoring takes, #17).
    current_by_route = current_stops_by_route()

    rows = []
    for route in net["routes"]:
        if route["id"] not in serving:
            continue
        # Ours comes from our own data, not the feed: the official column
        # already shows the feed's countdown.
        ours = predict_minutes(route["id"], stop_id, use_live_feed=False, current_by_route=current_by_route)
        age = ages.get(soonest_bus.get(route["id"]))
        too_old = age is not None and age > HIDE_PREDICTION_AFTER_MINUTES * 60
        rows.append(
            Row(
                route_id=route["id"],
                name=route["name"],
                label=route["label"],
                color=route["color"],
                official=sorted(minutes[route["id"]]),
                detoured=route["id"] in detoured,
                ours=None if ours is None or too_old else round(ours),
                last_seen=int(age // 60) if age is not None and age > STALE_AFTER_MINUTES * 60 else None,
                ours_hidden=too_old and ours is not None,
            )
        )
    rows.sort(key=lambda row: (row.soonest, row.name))
    return Board(running=True, rows=rows)
