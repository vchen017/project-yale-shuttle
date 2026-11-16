"""The next buses from one stop, optionally to another, for the next-buses page
(#24, story #5).

Each route that serves both stops gets a row: its name and colour, the
official ETAs from the live feed (transit.downtowner.etas), and our own
prediction from history.predictions.predict_minutes (#23), or None when we
have none. Routes are identified by their numeric id, never their short name.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from history.predictions import predict_minutes

from . import downtowner
from .network import network

LATER_BUSES = 2  # official times shown after the next one


@dataclass
class Row:
    route_id: int
    name: str
    label: str
    color: str  # six hex digits, already checked by network()
    official: list = field(default_factory=list)  # minutes, soonest first
    ours: int | None = None  # whole minutes, or None when we have no prediction

    @property
    def text_color(self):
        """Dark text on a light route colour, white on a dark one."""
        r, g, b = (int(self.color[i : i + 2], 16) / 255 for i in (0, 2, 4))
        return "#212529" if 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.6 else "#fff"

    @property
    def next_official(self):
        return self.official[0] if self.official else None

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

    minutes = defaultdict(list)
    for arrival in downtowner.etas(stop_id):
        if arrival["minutes"] >= 0:
            minutes[arrival["route_id"]].append(arrival["minutes"])

    rows = []
    for route in net["routes"]:
        if route["id"] not in serving:
            continue
        # Ours comes from our own data, not the feed: the official column
        # already shows the feed's countdown.
        ours = predict_minutes(route["id"], stop_id, use_live_feed=False)
        rows.append(
            Row(
                route_id=route["id"],
                name=route["name"],
                label=route["label"],
                color=route["color"],
                official=sorted(minutes[route["id"]]),
                ours=None if ours is None else round(ours),
            )
        )
    rows.sort(key=lambda row: (row.soonest, row.name))
    return Board(running=True, rows=rows)
