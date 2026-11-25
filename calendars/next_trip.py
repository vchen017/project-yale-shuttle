"""The next trip screen (#157): the rider's next trip, live, on top of the
when-to-leave page.

The card says when to leave and how much time that leaves to spare at the
stop (#155); the bus as a range, from the Downtowner's countdown to our
corrected one (#153), so a rider sees how sure we are; the bus itself on the
trip's map; and what could go wrong: how crowded the route is and whether
its buses look detoured. While there's a bus to watch for, from LIVE_WINDOW
before the event until it starts, the page asks `next_trip_card` for the
card again every REFRESH_SECONDS, as often as the feed's cache turns over,
and swaps it in without reloading, moving the bus on the map rather than
redrawing it. Outside that window it doesn't ask: each refresh reads the
database, and Neon's free plan stays awake 5 minutes after every use, so a
page left open all day mustn't keep it awake. A card for a later trip asks
once, when its live window opens.

Nothing here plans anything the page doesn't: the card is the page's first
event with a building and a Home, as calendars.plans planned it.

When no shuttle links Home to the event at its time, the card offers the
ways Yale gets riders there at night or door to door instead (#14), with
tap-to-call links.

Once it's past "Leave by", the card follows the rider's bus (#163): the bus
coming to their stop, the ride, "get off at the next stop", and when they'll
reach the event, by calendars.follow. Re-planning from Home then would pick a later bus, so while the
card watches a live bus it remembers that trip in the rider's session
(remember), and from its "Leave by" until they should be there, the card is
that trip (followed).
"""

import datetime as dt
import time
from dataclasses import dataclass, field

from crowding.services import crowding
from detours.detect import detoured_routes
from history.predictions import stored_corrections
from transit import downtowner
from transit.downtowner import FeedUnavailable

from . import follow
from .plans import LIVE_WINDOW

SESSION_KEY = "next_trip_followed"  # the trip remembered for following, in the rider's session
FOLLOW_AT_MOST = dt.timedelta(hours=2)  # a followed trip ends this long after its "Leave by" at the latest
THERE_FOR = dt.timedelta(minutes=5)  # and this long after the rider should have reached the event
LEAVE_BY_MOVES = dt.timedelta(minutes=2)  # a smaller change isn't worth rewriting the session

REFRESH_SECONDS = 30  # the card refreshes this often; the feed is cached as long
STALE_SECONDS = 180  # a bus position older than this gets its age shown (as on a shared trip, #10)
HIDE_BUS_SECONDS = 600  # older than this, the map doesn't show the bus (as the board hides ours, #58)

# Yale Transit's own ways home at night and door to door (#14), for when no
# shuttle links Home to the event. From its contact and nighttime-routes
# pages, checked Nov 25, 2026:
# https://your.yale.edu/campus-essentials/getting-around-campus/using-the-shuttle/contact-us
# https://your.yale.edu/campus-essentials/getting-around-campus/using-the-shuttle/nighttime-routes
# The two disagree on when the night shuttle stops: midnight on the contact
# page, 1 AM on the routes page. This follows the routes page, which is the
# one about the night shuttle.
NIGHT_OPTIONS = (
    {
        "name": "Night shuttle to your door",
        "icon": "bi-moon-stars",
        "hours": "6 PM – 1 AM, every night",
        "how": "Blue and Orange lines from 333 Cedar St. Tell the driver where you're going, within the shuttle's area.",
        "phone": None,
    },
    {
        "name": "Accessible transit, door to door",
        "icon": "bi-person-wheelchair",
        "hours": "Mon–Fri 6 AM – 1 AM, Sat–Sun 9 AM – 11 PM",
        "how": "Call Yale Transit's dispatch to book a ride.",
        "phone": "203-281-5470, ext. 1",
        "tel": "+12032815470,1",
    },
    {
        "name": "2-Walk, a walking escort",
        "icon": "bi-person-walking",
        "hours": "24 hours",
        "how": "Yale Security walks you there.",
        "phone": "203-432-9255",
        "tel": "+12034329255",
    },
)


@dataclass
class NextTrip:
    """The card's facts, read now."""

    plan: object  # calendars.plans.EventPlan, with the page's extras (leave_in, board_time, map)
    now: dt.datetime
    bus_low: int | None = None  # "Bus in 12–14 min": the sooner of the feed's countdown and ours
    bus_high: int | None = None
    bus: dict | None = None  # {"lat", "lon", "age_minutes"}: the bus to catch, while the feed shows it
    bus_id: int | None = None  # that bus, as the feed names it: the one to follow after "Leave by" (#163)
    crowd_label: str = ""  # "Packed", ...: riders' reports, or the usual for this hour
    crowd_level: int | None = None  # 0-3, for the meter
    crowd_usual: bool = False  # the label is the usual for this hour, not a recent report
    detoured: bool = False
    no_shuttle: bool = False  # nothing links Home to the event then: offer NIGHT_OPTIONS
    options: tuple = field(default=NIGHT_OPTIONS)

    following = False  # a Following is the card once it's past "Leave by"

    @property
    def map(self):
        """The trip's map, as the page drew it for the plan."""
        return getattr(self.plan, "map", None)

    @property
    def key(self):
        """Which event the card is about: the page reloads when that changes."""
        event = self.plan.event
        return f"{event.calendar_id}-{event.uid}-{event.start:%Y%m%d%H%M}"

    @property
    def live(self):
        """A live bus times it, rather than an estimate or a walk."""
        return self.plan.row is not None and not self.plan.estimate

    @property
    def bus_range(self):
        """"12–14" or "12", for "Bus in … min"."""
        if self.bus_low is None:
            return ""
        return str(self.bus_low) if self.bus_low == self.bus_high else f"{self.bus_low}–{self.bus_high}"

    @property
    def watching(self):
        """Whether the page should keep refreshing the card: a bus to catch,
        and the event's live window open but the event not started."""
        ride = self.plan.plan is not None and not self.plan.walking_is_quicker
        return ride and self.live_from <= self.now < self.plan.event.start

    @property
    def live_from(self):
        """When an estimate turns into a live time: once the event is close
        enough for the live board (calendars.plans.LIVE_WINDOW)."""
        return self.plan.event.start - LIVE_WINDOW


def next_plan(plans, now):
    """The page's next trip: the first plan, soonest first, for an event that
    hasn't started, whose building we know, planned from a Home: a ride, or
    a building with no shuttle linking it to Home (walk_all is set only
    then). None when there's no such event. The view leaves the card out
    while the feed is down, when "no shuttle" couldn't be told apart."""
    for plan in plans:
        if plan.event.start <= now or plan.building is None:
            continue
        if plan.plan is not None or plan.walk_all is not None:
            return plan
    return None


def read(plan, now):
    """The NextTrip for this plan: its live facts read now, from the feed's
    cached answers and the crowding reports. A feed that's down leaves out
    what it would have said, never the card."""
    trip = NextTrip(plan=plan, now=now)
    if plan.plan is None:
        trip.no_shuttle = True  # a building and a Home, and no shuttle linking them then
        return trip
    if not trip.live:
        return trip
    row = plan.row
    countdown = plan.bus_countdown
    if countdown is not None:
        trip.bus_low, trip.bus_high = sorted((countdown, plan.bus_minutes))
        trip.bus_id, trip.bus = _bus(plan.plan.board_at["id"], row.route_id, countdown)
    else:
        trip.bus_low = trip.bus_high = plan.bus_minutes
    route = {"id": row.route_id, "name": row.name, "label": row.label, "color": row.color}
    (crowd,) = crowding([route], now=now)
    if crowd.current_level is not None:
        trip.crowd_label, trip.crowd_level = crowd.current_label, crowd.current_level
    elif crowd.typical_now is not None:
        trip.crowd_label, trip.crowd_level = crowd.typical_now.label, crowd.typical_now.level
        trip.crowd_usual = True
    try:
        trip.detoured = any(flag["route_id"] == row.route_id for flag in detoured_routes(now))
    except FeedUnavailable:
        pass  # the card still shows; the chip may just be missing
    return trip


def _bus(stop_id, route_id, countdown):
    """The bus the rider is catching, (bus_id, where it is): the one the feed
    gives that countdown at the boarding stop, from the buses answer. Where
    it is is None when the feed doesn't show it, or its position is too old
    to be worth a dot (#58); bus_id is None when the feed didn't name it."""
    try:
        arrivals = downtowner.etas(stop_id)
        buses = downtowner.buses()
    except FeedUnavailable:
        return None, None
    bus_id = next(
        (arrival["bus_id"] for arrival in arrivals if arrival["route_id"] == route_id and arrival["minutes"] == countdown),
        None,
    )
    if bus_id is None:
        return None, None
    for bus in buses if isinstance(buses, list) else []:
        try:
            if int(bus["id"]) != int(bus_id):
                continue
            age = max(0.0, time.time() - float(bus["lastUpdate"]))
            if age > HIDE_BUS_SECONDS:
                return bus_id, None
            return bus_id, {
                "lat": float(bus["lat"]),
                "lon": float(bus["lon"]),
                "age_minutes": round(age / 60) if age > STALE_SECONDS else None,
            }
        except (KeyError, TypeError, ValueError):
            continue
    return bus_id, None


@dataclass
class Following:
    """The next trip card once it's past "Leave by" (#163): the trip as the
    card remembered it then (a session record, from remember), and its bus
    now (a calendars.follow.Follow)."""

    record: dict
    follow: object
    now: dt.datetime
    map: dict | None = None  # the trip's map, on the page's first render

    following = True
    live = True
    watching = True  # the page keeps refreshing it until it's over
    no_shuttle = False

    @property
    def key(self):
        return self.record["key"]

    @property
    def live_from(self):
        return _when(self.record["leave_by"])

    @property
    def title(self):
        return self.record["title"]

    @property
    def start(self):
        return _when(self.record["start"])

    @property
    def route(self):
        return self.record["route"]

    @property
    def board(self):
        return self.record["board"]

    @property
    def get_off(self):
        return self.record["get_off"]

    @property
    def building(self):
        return self.record["building"]

    @property
    def bus_id(self):
        return self.record.get("bus_id")

    @property
    def walk_from_stop(self):
        return self.record.get("walk_from_stop") or 0

    @property
    def bus(self):
        """The bus for the map, unless its position is too old to show (#58)."""
        bus = self.follow.bus
        if bus is None or (bus["age_minutes"] or 0) > follow.HIDE_BUS_MINUTES:
            return None
        return {"lat": bus["lat"], "lon": bus["lon"], "age_minutes": bus["age_minutes"]}

    @property
    def arrive_at(self):
        """When the rider reaches the event, "There by": the bus at their
        stop, by the time it's there 90% of the time (#174), then the walk.
        None when the feed hasn't timed the bus there, or it's past."""
        at_stop = self.follow.get_off_by or self.follow.get_off_at
        if at_stop is None:
            return None
        arrive = at_stop + dt.timedelta(minutes=self.walk_from_stop)
        return arrive if arrive >= self.now else None


def remember(session, trip, home, now):
    """While the card watches a live bus before "Leave by", remember the trip
    in the rider's session, for following it once it's past "Leave by"
    (#163). The newest plan wins, so the bus followed is the one the card
    last told them to catch. The session is rewritten only when the trip or
    its bus changes, or "Leave by" moves by LEAVE_BY_MOVES or more."""
    plan = trip.plan
    if not (trip.live and trip.watching and home is not None and plan.leave_by and now < plan.leave_by):
        return
    old = session.get(SESSION_KEY) or {}
    old_leave_by = _when(old.get("leave_by"))
    if (
        old.get("key") == trip.key
        and old.get("bus_id") == trip.bus_id
        and old_leave_by is not None
        and abs(old_leave_by - plan.leave_by) < LEAVE_BY_MOVES
    ):
        return
    row, board, get_off = plan.row, plan.plan.board_at, plan.plan.get_off_at
    session[SESSION_KEY] = {
        "key": trip.key,
        "title": plan.event.title,
        "start": plan.event.start.isoformat(),
        "leave_by": plan.leave_by.isoformat(),
        "home": [home.lat, home.lon],
        "building": {"name": plan.building["name"], "lat": plan.building["lat"], "lon": plan.building["lon"]},
        "route": {"id": row.route_id, "name": row.name, "label": row.label, "color": row.color, "text_color": row.text_color},
        "bus_id": trip.bus_id,
        "board": {"id": board["id"], "name": board["name"], "lat": board["lat"], "lon": board["lon"]},
        "get_off": {"id": get_off["id"], "name": get_off["name"], "lat": get_off["lat"], "lon": get_off["lon"]},
        "walk_from_stop": plan.plan.walk_from_stop,
        "progress": follow.Progress().as_dict(),
    }


def followed(session, now):
    """The remembered trip (#163) once it's past its "Leave by", until the
    rider should be at the event: None before then, and after, when it's
    forgotten. It ends FOLLOW_AT_MOST after "Leave by" at the latest."""
    record = session.get(SESSION_KEY)
    leave_by = _when(record.get("leave_by")) if isinstance(record, dict) else None
    if leave_by is None:
        session.pop(SESSION_KEY, None)
        return None
    if now < leave_by:
        return None
    progress = follow.Progress.from_dict(record.get("progress"))
    ends = leave_by + FOLLOW_AT_MOST
    if progress.off:
        walk = dt.timedelta(minutes=record.get("walk_from_stop") or 0)
        ends = min(ends, (progress.get_off_at or now) + walk + THERE_FOR)
    if now > ends:
        session.pop(SESSION_KEY, None)
        return None
    return record


def follow_card(session, record, now):
    """The card for a followed trip: its bus now, by calendars.follow, with
    the countdowns corrected as on the rest of the pages (#153) and the
    trip's progress kept in the session. Raises FeedUnavailable when the
    feed is down."""
    progress = follow.Progress.from_dict(record.get("progress"))
    corrections = stored_corrections()
    now_trip = follow.follow(
        record["route"]["id"], record.get("bus_id"), record["board"]["id"], record["get_off"]["id"], progress, now,
        correct=corrections.correct, late=corrections.late,
    )
    if progress.as_dict() != record.get("progress"):
        record = {**record, "progress": progress.as_dict()}
        session[SESSION_KEY] = record
    return Following(record=record, follow=now_trip, now=now)


def forget(session):
    """Stop following: the card goes back to the next trip."""
    session.pop(SESSION_KEY, None)


def _when(text):
    try:
        return dt.datetime.fromisoformat(text) if text else None
    except (TypeError, ValueError):
        return None
