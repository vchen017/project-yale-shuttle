"""The one answer the trip page leads with (#131): when to leave, and for
which bus, with a little time to spare.

    answer(plan, board, walk_all_the_way, now) -> Answer

Where the bus's arrival time comes from is not decided here. Each row of the
board has one number for its soonest bus, Row.expected, and the later buses
follow it from the feed's list. How `expected` is chosen (our prediction, the
feed's countdown, or whichever the rule for the distance says) lives in
transit.next_buses, so changing that rule changes this page without touching
this file.

The bus is the soonest one the rider can still catch: its minutes at the
boarding stop have to cover the walk there, so the page never says to leave
in the past. The rider is told to set out BUFFER_MINUTES early, and the
headline says how early they will be. When the bus is too close for the whole
buffer, they leave now and are told the (smaller) margin.

Walking is never the headline, only a notice beside it: walking_is_quicker is
True when walking out the door right now would get there no later than the
bus does. Both sides are built from the same minutes the page shows (the
bus's wait + the ride + the walk at the far end, against the walk all the
way), so the notice always agrees with the two arrival times on the page.
With no ride time there is nothing to compare, so no notice.

How long the ride takes is the gap between the same bus's feed countdowns at
the two stops, whichever number said when it comes: a duration, so it doesn't
depend on that rule. When the feed doesn't show the bus at the second stop we
say nothing about the ride rather than guess.

route_segment() cuts the stretch of a route's drawn path between two stops,
for the trip map.

Raises downtowner.FeedUnavailable when the feed is down, like the rest of the
trip page.
"""

import datetime as dt
from dataclasses import dataclass

from django.utils import timezone

from . import downtowner
from .trips import meters_between

# Minutes to be at the stop before the bus: walking paces and the feed's
# countdowns are both rough, and missing a shuttle costs far more than
# standing at a stop for two minutes. Tune it here.
BUFFER_MINUTES = 2

RIDE = "ride"  # a bus to catch
NO_BUS = "no-bus"  # a route links the stops, but no bus we know of can be caught
NO_ROUTE = "no-route"  # no running route links the two places


@dataclass
class Answer:
    kind: str
    walk_all_the_way: int  # minutes, for every kind: what walking costs
    route_id: int | None = None  # RIDE only, from here down
    route_name: str = ""
    route_color: str = ""
    bus_minutes: int | None = None  # minutes until the bus reaches the boarding stop
    leave_in: int | None = None  # minutes until the rider should set out; 0 means now
    early_minutes: int = 0  # minutes the rider waits at the stop if they set out then
    walk_to_stop: int = 0
    walk_from_stop: int = 0
    bus_at: dt.datetime | None = None  # the bus at the boarding stop
    ride_minutes: int | None = None  # None when the feed can't say
    walk_arrive_at: dt.datetime | None = None  # walking from now, all the way
    arrive_at: dt.datetime | None = None  # at the destination; None with no ride time

    @property
    def headline(self):
        if self.kind == RIDE:
            bus = f"{clock(self.bus_at)} {self.route_name}"
            if self.walk_to_stop == 0:
                return f"The {bus} reaches your stop in {self.bus_minutes} min"
            if self.leave_in <= 0:
                start = "Leave now for"
            else:
                start = f"Leave in {self.leave_in} min for"
            return f"{start} the {bus}, to be at the stop {self.early_phrase}"
        if self.kind == NO_BUS:
            return "No bus to catch right now"
        return "No shuttle running now links these places"

    @property
    def walking_is_quicker(self):
        """Walking the whole way from now arrives no later than the bus does.
        False when there's no bus, or no ride time to compare against."""
        return self.arrive_at is not None and self.walk_arrive_at <= self.arrive_at

    @property
    def early_phrase(self):
        if self.early_minutes <= 0:
            return "just as it arrives"
        return f"{self.early_minutes} min early"

    @property
    def detail(self):
        """What goes under the headline when there's no bus to show."""
        return f"Walking the whole way takes about {self.walk_all_the_way} min."


def clock(moment):
    """A time as riders read it, in Eastern time: 12:20 PM."""
    local = timezone.localtime(moment)
    return f"{local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"


def answer(plan, board, walk_all_the_way, now=None):
    now = now or timezone.now()
    if plan is None:
        return Answer(NO_ROUTE, walk_all_the_way)
    rows = {row.route_id: row for row in board.rows} if board else {}
    shared = set(plan.board_at["routes"]) & set(plan.get_off_at["routes"]) & set(rows)
    feed_here = [a for a in downtowner.etas(plan.board_at["id"]) if a["route_id"] in shared and a["minutes"] >= 0]

    best = None  # (minutes, row, the feed's arrival for that bus or None)
    for route_id in sorted(shared):
        row = rows[route_id]
        buses = [a for a in feed_here if a["route_id"] == route_id]
        # The soonest bus is `expected`; the buses after it are the feed's.
        times = ([row.expected] if row.expected is not None else []) + row.official[1:]
        for i, minutes in enumerate(times):
            if minutes >= plan.walk_to_stop:
                if best is None or minutes < best[0]:
                    best = (minutes, row, buses[i] if i < len(buses) else None)
                break
    if best is None:
        return Answer(NO_BUS, plan.walk_all_the_way)
    minutes, row, bus = best
    ride = _ride_minutes(bus, downtowner.etas(plan.get_off_at["id"])) if bus else None
    leave_in = max(0, minutes - plan.walk_to_stop - BUFFER_MINUTES)
    early = minutes - plan.walk_to_stop - leave_in
    bus_at = now + dt.timedelta(minutes=minutes)
    result = Answer(
        RIDE,
        plan.walk_all_the_way,
        route_id=row.route_id,
        route_name=row.name,
        route_color=row.color,
        bus_minutes=minutes,
        leave_in=leave_in,
        early_minutes=early,
        walk_to_stop=plan.walk_to_stop,
        walk_from_stop=plan.walk_from_stop,
        bus_at=bus_at,
        ride_minutes=ride,
        walk_arrive_at=now + dt.timedelta(minutes=plan.walk_all_the_way),
    )
    if ride is not None:
        result.arrive_at = bus_at + dt.timedelta(minutes=ride + plan.walk_from_stop)
    return result


def _ride_minutes(bus, arrivals_at_the_end):
    """Minutes the bus takes to get from the boarding stop to the stop where
    the rider gets off: its first countdown at the end after the one at the
    start, minus the start's. None when the feed doesn't name the bus or
    doesn't show it at the end."""
    if bus["bus_id"] is None:
        return None
    later = [
        arrival["minutes"]
        for arrival in arrivals_at_the_end
        if arrival["bus_id"] == bus["bus_id"]
        and arrival["route_id"] == bus["route_id"]
        and arrival["minutes"] > bus["minutes"]
    ]
    return min(later) - bus["minutes"] if later else None


def route_segment(path, start, end):
    """The part of a route's drawn path a rider rides, as [(lat, lon)] from the
    boarding stop to the getting-off stop. `path` is the feed's flat
    [lat, lon, lat, lon, ...] list; start and end are stops (dicts with lat and
    lon). The path is the bus's direction of travel, so when the end's nearest
    corner comes before the start's, the ride runs on past the path's end and
    around from its beginning. Empty when the route has no usable path."""
    corners = [(path[i], path[i + 1]) for i in range(0, len(path) - 1, 2)]
    if len(corners) < 2:
        return []
    first = _nearest_corner(corners, start)
    last = _nearest_corner(corners, end)
    stretch = corners[first : last + 1] if first <= last else corners[first:] + corners[: last + 1]
    return [(start["lat"], start["lon"]), *stretch, (end["lat"], end["lon"])]


def _nearest_corner(corners, stop):
    point = (stop["lat"], stop["lon"])
    return min(range(len(corners)), key=lambda i: meters_between(point, corners[i]))
