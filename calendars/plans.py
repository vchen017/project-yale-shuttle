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
countdown, or our prediction, whichever it has (#117), with a few minutes to
spare at the stop: the cushion the recorded history teaches for countdowns
like that bus's (#155), or as much of it as there's time for. Further out there is
nothing live to predict from — our predictions count minutes from now — so
the time to leave is the event's start, minus the walks, minus a typical wait
at the stop, and the page labels it as an estimate. An estimate also plans on
the routes that run at the event's weekday and hour, from the recorded
history, not on the routes running now — the 9 PM lecture checked in the
afternoon rides the night route, not the daytime one (Vivian, #126 review).
"""

import datetime as dt
import re
from collections import Counter
from dataclasses import dataclass, field

from django.utils import timezone

from buildings.search import search as building_search
from history.models import RouteStatus
from transit import downtowner, trips
from transit.next_buses import next_buses

# How soon before an event the live board still answers "your bus is N
# minutes away": riders leave about the walk plus a wait before the start.
LIVE_WINDOW = dt.timedelta(minutes=45)
# The wait at the stop we assume when nothing live can tell us. The Nov 17
# audit's median actual wait was about 16 minutes, so 15 it is, until waits
# are modeled from the recorded history.
TYPICAL_WAIT = dt.timedelta(minutes=15)
# How many past weeks of recorded polls vote on what runs at the event's
# hour, and how far from its time of day a poll still counts. Three weeks is
# most of the term's history; fifteen minutes each side is seven polls at the
# feed's five-minute cadence.
HISTORY_WEEKS = 3
SLOT = dt.timedelta(minutes=15)


@dataclass
class EventPlan:
    """One event's way to get there, or what's missing from it."""

    event: object  # calendars.Event
    building: dict | None  # the Yale building it's in, as buildings.search gives them
    plan: trips.Plan | None = None  # None: no Home, or no route links the two, or no live stops
    route_names: list = field(default_factory=list)  # the routes that link the two stops
    row: object | None = None  # the live bus to catch (transit Row), when there is one
    bus_minutes: int | None = None  # the countdown the plan used: ours, or the feed's
    # (it may be a later bus than the row's soonest, when the walk needs more time)
    leave_by: dt.datetime | None = None  # when to leave Home
    spare_minutes: int = 0  # minutes at the stop before the bus that leave_by builds in (#155)
    estimate: bool = False  # leave_by assumes a typical wait, not a live bus
    # Minutes' walk straight from Home, for events we can't ride: with the feed
    # down or no route linking the two, the page can still say how far it is.
    # None when there's no Home to walk from, or a ride exists (the ride's
    # walks live on the plan).
    walk_all: int | None = None

    @property
    def location_unknown(self):
        return self.building is None

    @property
    def walking_is_quicker(self):
        return bool(self.plan and self.plan.walking_is_quicker)


def routes_running_at(start, now):
    """The route ids that run at the event's weekday and time of day, from the
    recorded polls (history.RouteStatus): a route counts when it was active in
    at least half the polls within SLOT of that time on the same weekday over
    the past HISTORY_WEEKS weeks. None when our history has no polls there —
    the caller then plans on the routes running now, as it did before."""
    local = timezone.localtime(start)
    polls = set()
    active = Counter()
    for week in range(1, HISTORY_WEEKS + 1):
        then = dt.datetime.combine(local.date() - dt.timedelta(days=7 * week), local.timetz())
        rows = RouteStatus.objects.filter(polled_at__range=(then - SLOT, then + SLOT))
        for polled_at, route_id, was_active in rows.values_list("polled_at", "route_id", "active"):
            polls.add(polled_at)
            if was_active:
                active[route_id] += 1
    if not polls:
        return None
    return {route_id for route_id, count in active.items() if count * 2 >= len(polls)}


def plan_event(event, home, *, now, stops, routes_by_id, running_at=None):
    """The plan for one event from one place. `stops` is served_stops() and
    `routes_by_id` the running routes by id; pass both None when the feed is
    down, and the plan still matches the building but can't say how to ride.

    `running_at` (the view passes routes_running_at) says which routes run at
    the event's weekday and hour; events past the live window plan on those
    instead of the routes running now, so a 9 PM event checked in the
    afternoon rides the night route. When it says None — no history — the
    plan falls back to the routes running now."""
    building = match_building(event.location)
    if building is None:
        return EventPlan(event=event, building=None)
    if home is None:
        return EventPlan(event=event, building=building)
    straight = trips.walk_minutes(trips.meters_between((home.lat, home.lon), (building["lat"], building["lon"])))
    if stops is None:
        # The feed is down: the building and the walk still show, no ride times.
        return EventPlan(event=event, building=building, walk_all=straight)
    plan_stops, plan_routes = stops, routes_by_id
    if event.start - now > LIVE_WINDOW and running_at is not None:
        running = running_at(event.start, now)
        if running is not None:
            plan_routes = {route_id: route for route_id, route in routes_by_id.items() if route_id in running}
            plan_stops = [
                {**stop, "routes": [route_id for route_id in stop["routes"] if route_id in running]}
                for stop in stops
                if set(stop["routes"]) & running
            ]
    plan = trips.plan((home.lat, home.lon), (building["lat"], building["lon"]), plan_stops)
    if plan is None:
        # No route running at the event's hour links the two: the walk all the
        # way is the news.
        return EventPlan(event=event, building=building, walk_all=straight)
    route_names = [
        plan_routes[route_id]["name"]
        for route_id in sorted(set(plan.board_at["routes"]) & set(plan.get_off_at["routes"]))
        if route_id in plan_routes
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
        minutes, countdown = catchable(row.ours, row.official, plan.walk_to_stop) if row else (None, None)
        if minutes is not None:
            cushion = row.cushions.get(countdown, 0) if countdown is not None else 0
            spare = spare_minutes(cushion, minutes, plan.walk_to_stop)
            return EventPlan(
                row=row,
                bus_minutes=minutes,
                spare_minutes=spare,
                leave_by=now + dt.timedelta(minutes=minutes - plan.walk_to_stop - spare),
                **plan_info,
            )

    return EventPlan(
        leave_by=event.start - dt.timedelta(minutes=plan.walk_from_stop) - TYPICAL_WAIT - dt.timedelta(minutes=plan.walk_to_stop),
        estimate=True,
        **plan_info,
    )


def catchable_bus(row, walk):
    """The soonest bus the rider can still make, in minutes from now, or None
    when no bus we know of leaves enough time for the walk to the stop — the
    plan never says to leave in the past (#26). The soonest bus counts only
    when we'd still be at the stop for it, by our prediction or, when we have
    none, the feed's countdown; otherwise the first later bus in the feed's
    list that allows the walk. official[0] is that same soonest bus, so it is
    skipped along with it."""
    if row is None:
        return None
    return catchable_minutes(row.ours, row.official, walk)


def catchable_minutes(ours, official, walk):
    """catchable_bus without a board row: `ours` is our prediction for the
    route's soonest bus, or None, and `official` the feed's countdowns,
    soonest first. The trip watch (#29) times trips with it, so the AI plans
    around the same bus as this page (#153)."""
    return catchable(ours, official, walk)[0]


def catchable(ours, official, walk):
    """catchable_minutes, with the feed's countdown for the bus it picked:
    (minutes, countdown), the countdown being what the cushion is looked up
    by (#155). It's None when `ours` came from history, with no bus in the
    feed; (None, None) when there's no bus to catch."""
    if ours is not None:
        soonest = (ours, official[0] if official else None)
    else:
        soonest = (official[0], official[0]) if official else None
    if soonest is not None and soonest[0] >= walk:
        return soonest
    for minute in official[1:]:
        if minute >= walk:
            return (minute, minute)
    return (None, None)


def spare_minutes(cushion, minutes, walk):
    """The minutes to spare at the stop that "Leave by" builds in (#155): the
    cushion, or as much of it as there's time for before a bus `minutes`
    away, so the time to leave is never in the past."""
    return max(0, min(cushion, minutes - walk))


def match_building(location):
    """The Yale building an event's location names, best match, or None.
    "Watson Center, 60 Sachem Street" matches on its building half; a street
    address on its own matches nothing, because that would be a guess — and
    neither does an address's city, state, ZIP or country."""
    text = (location or "").strip()
    if not text:
        return None
    pieces = [text, *[piece.strip() for piece in re.split(r"[;,]", text)]]
    seen = set()
    for piece in pieces:
        words = [word for word in piece.split() if not word[:1].isdigit()]
        query = " ".join(words)
        if not query or query in seen or query.casefold() in PLACE_TAILS:
            continue
        seen.add(query)
        found = building_search(query)
        if found:
            return found[0]
    return None


# Pieces of a Google-style location that name no building of ours, tried on
# their own: the city, the state and its abbreviations, the country. "New
# Haven" alone would match Yale New Haven Hospital — a guess, not a match.
PLACE_TAILS = {
    "connecticut",
    "conn",
    "ct",
    "new haven",
    "west haven",
    "east haven",
    "north haven",
    "hamden",
    "usa",
    "us",
    "united states",
    "united states of america",
}
