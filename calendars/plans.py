"""Turn an event and a place into a trip plan: the route, the stop to walk
to, and the time to leave (#26).

The building comes from matching the event's location text against our list
of Yale buildings (#98). An event location is usually "Watson Center, 60
Sachem Street", and the address half only gets in the way, so we try the
whole text and then its pieces, dropping street numbers, and take the first
match. When nothing matches, the plan says location unknown instead of
guessing.

The ride itself is transit's plan(): the pair of stops on a shared route with
the least walking at each end. Within LIVE_WINDOW of the event, the next
buses' board picks the route and says when to be at the stop — the feed's
countdown, or our prediction, whichever it has (#117). Further out there is
nothing live to predict from — our predictions count minutes from now — so
the time to leave is the event's start, minus the walks, minus a typical wait
at the stop, and the page labels it as an estimate.
"""

import datetime as dt
import re
from dataclasses import dataclass, field

from buildings.search import search as building_search
from transit import downtowner, trips
from transit.next_buses import next_buses

# How soon before an event the live board still answers "your bus is N
# minutes away": riders leave about the walk plus a wait before the start.
LIVE_WINDOW = dt.timedelta(minutes=45)
# The wait at the stop we assume when nothing live can tell us. The Nov 17
# audit's median actual wait was about 16 minutes, so 15 it is, until waits
# are modeled from the recorded history.
TYPICAL_WAIT = dt.timedelta(minutes=15)


@dataclass
class EventPlan:
    """One event's way to get there, or what's missing from it."""

    event: object  # calendars.Event
    building: dict | None  # the Yale building it's in, as buildings.search gives them
    plan: trips.Plan | None = None  # None: no Home, or no route links the two, or no live stops
    route_names: list = field(default_factory=list)  # the routes that link the two stops
    row: object | None = None  # the live bus to catch (transit Row), when there is one
    leave_by: dt.datetime | None = None  # when to leave Home
    estimate: bool = False  # leave_by assumes a typical wait, not a live bus

    @property
    def location_unknown(self):
        return self.building is None

    @property
    def walking_is_quicker(self):
        return bool(self.plan and self.plan.walking_is_quicker)


def plan_event(event, home, *, now, stops, routes_by_id):
    """The plan for one event from one place. `stops` is served_stops() and
    `routes_by_id` the running routes by id; pass both None when the feed is
    down, and the plan still matches the building but can't say how to ride."""
    building = match_building(event.location)
    if building is None:
        return EventPlan(event=event, building=None)
    if home is None or stops is None:
        return EventPlan(event=event, building=building)
    plan = trips.plan((home.lat, home.lon), (building["lat"], building["lon"]), stops)
    if plan is None:
        return EventPlan(event=event, building=building)
    route_names = [
        routes_by_id[route_id]["name"]
        for route_id in sorted(set(plan.board_at["routes"]) & set(plan.get_off_at["routes"]))
        if route_id in routes_by_id
    ]
    plan_info = dict(event=event, building=building, plan=plan, route_names=route_names)

    if plan.walking_is_quicker:
        return EventPlan(leave_by=event.start - dt.timedelta(minutes=plan.walk_all_the_way), **plan_info)

    if event.start - now <= LIVE_WINDOW:
        try:
            board = next_buses(plan.board_at["id"], plan.get_off_at["id"])
        except downtowner.FeedUnavailable:
            board = None  # fall through to the estimate; the page never waits on the feed
        row = board.rows[0] if board and board.rows else None  # rows come soonest first
        minutes = None
        if row is not None:
            minutes = row.ours if row.ours is not None else row.next_official
        if minutes is not None:
            return EventPlan(
                row=row,
                leave_by=now + dt.timedelta(minutes=minutes - plan.walk_to_stop),
                **plan_info,
            )

    return EventPlan(
        leave_by=event.start - dt.timedelta(minutes=plan.walk_from_stop) - TYPICAL_WAIT - dt.timedelta(minutes=plan.walk_to_stop),
        estimate=True,
        **plan_info,
    )


def match_building(location):
    """The Yale building an event's location names, best match, or None.
    "Watson Center, 60 Sachem Street" matches on its building half; a street
    address on its own matches nothing, because that would be a guess."""
    text = (location or "").strip()
    if not text:
        return None
    pieces = [text, *[piece.strip() for piece in re.split(r"[;,]", text)]]
    seen = set()
    for piece in pieces:
        words = [word for word in piece.split() if not word[:1].isdigit()]
        query = " ".join(words)
        if not query or query in seen:
            continue
        seen.add(query)
        found = building_search(query)
        if found:
            return found[0]
    return None
