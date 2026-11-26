"""Following one bus through a trip (#163): which part of the trip a rider
on it is at, and when it reaches their stops.

The next trip card (calendars.next_trip) shows it to the rider once it's
past "Leave by". It reads the live feed, each answer cached for
downtowner.CACHE_SECONDS:
- the bus's ETAs at the stop to get on at and the stop to get off at. A
  bus's ETAs come in its route's order, and a stop it has passed drops off
  its list (checked against the recorded polls), so while it comes to the
  first stop that ETA is the smaller one, and once it's past it, the second;
- where the bus is, and its `lastStop`, the stop it last served;
- the route's stops in loop order, so "your stop is next" means the bus's
  last stop is the one before it.

The feed can't say whether the rider got on. The trip assumes they did once
the bus is past their stop, and remembers it in a Progress the card keeps
in the rider's session, so it only moves forward: a bus that drops off a
rider and starts its next loop is coming to their first stop again, by its
ETAs, and mustn't read as "your bus is coming" a second time.

When is a bus past a stop? When its ETAs order it so, or its own GPS says
it served that stop, or moved past it since the last look (its last stop
can skip a stop it didn't stop at). And when its predicted time there jumps
more than JUMP minutes later (the next loop, or on an out-and-back route,
like 9 and 10, the second visit), but only if its GPS moved it past the
stop meanwhile, or there's no fresh position to say. A bus held at a stop,
or delayed after moving on a little, has its countdowns jump too; replayed
over the recorded polls, reading that as "passed" told riders they were
off before their bus had come.
"""

import datetime as dt
import time
from dataclasses import dataclass

from transit import downtowner

COMING = "coming"  # the bus is on its way to the stop where the rider gets on
BOARDING = "boarding"  # arriving there: a countdown of 0, which can sit at 0 for minutes before the bus comes
RIDING = "riding"  # past it, on its way to the stop where they get off
NEXT_STOP = "next-stop"  # that stop is the bus's next one
GET_OFF = "get-off"  # arriving there
OFF = "off"  # past it: the rider walks the rest of the way
LOST = "lost"  # the feed doesn't time the bus at either stop

ON_BOARD = (RIDING, NEXT_STOP, GET_OFF)
JUMP = dt.timedelta(minutes=8)  # a predicted time this much later than the last, once the bus has moved: past it
PASSED = dt.timedelta(minutes=3)  # this long after its predicted time, a bus the feed stopped timing has passed
STALE_SECONDS = 180  # a position older than this gets its age shown, and its last stop isn't trusted
HIDE_BUS_MINUTES = 10  # the card's map leaves out a position older than this, as the board does (#58)


@dataclass
class Progress:
    """What the trip has seen so far, kept between refreshes: when the bus
    was last predicted at each stop, and whether it has passed them."""

    board_at: dt.datetime | None = None
    get_off_at: dt.datetime | None = None
    boarded: bool = False
    off: bool = False
    last_stop: int | None = None  # the bus's last stop by its GPS, at the last fresh position

    def as_dict(self):
        """For a session: JSON, with times as ISO strings."""
        return {
            "board_at": self.board_at.isoformat() if self.board_at else None,
            "get_off_at": self.get_off_at.isoformat() if self.get_off_at else None,
            "boarded": self.boarded,
            "off": self.off,
            "last_stop": self.last_stop,
        }

    @classmethod
    def from_dict(cls, data):
        data = data or {}
        return cls(
            board_at=_when(data.get("board_at")),
            get_off_at=_when(data.get("get_off_at")),
            boarded=bool(data.get("boarded")),
            off=bool(data.get("off")),
            last_stop=_int(data.get("last_stop")),
        )


@dataclass
class Follow:
    """The trip now: its phase, the bus's minutes to each stop the feed still
    times (corrected as the pages show them, #153), when it reaches the stop
    to get off at, and where it is ({"lat", "lon", "name", "last_stop",
    "age_minutes"}, or None when the feed doesn't show it)."""

    phase: str
    to_board: int | None = None
    to_get_off: int | None = None
    get_off_at: dt.datetime | None = None
    bus: dict | None = None
    get_off_by: dt.datetime | None = None  # get_off_at plus the arrival cushion (#174): when it's there by, 90% of the time

    @property
    def on_board(self):
        return self.phase in ON_BOARD

    @property
    def step(self):
        """Where the journey line puts the bus: 0 before the first stop, 1 at
        it, 2 between the stops, 3 at the second, 4 past it."""
        return {COMING: 0, LOST: 0, BOARDING: 1, RIDING: 2, NEXT_STOP: 2, GET_OFF: 3, OFF: 4}[self.phase]


def follow(route_id, bus_id, board_stop_id, get_off_stop_id, progress, now, correct=None, late=None):
    """The Follow for that bus now, moving `progress` along. With no bus_id
    (the feed didn't name one), it follows the route's soonest bus. The
    phases read the feed's own countdowns; the minutes shown, and when the
    bus reaches the stop to get off at, are corrected by `correct`
    (history.predictions.Corrections.correct, as the pages use it, #153)
    when it's given, and `late` (Corrections.late, #174) gives how much
    later it may come, for get_off_by. Raises downtowner.FeedUnavailable
    when the feed is down; progress is unchanged then."""
    at_board = _minutes(downtowner.etas(board_stop_id), route_id, bus_id)
    at_get_off = _minutes(downtowner.etas(get_off_stop_id), route_id, bus_id)
    bus = _bus(downtowner.buses(), bus_id)
    routes = downtowner.routes()
    before_get_off = _stops_before(routes, route_id, get_off_stop_id)

    def minutes_after(minutes):
        return now + dt.timedelta(minutes=minutes)

    def shown(minutes):
        """A countdown as the page shows it: corrected, in whole minutes."""
        if minutes is None or correct is None:
            return minutes
        corrected = correct(route_id, minutes)
        return minutes if corrected is None else round(corrected)

    def reaches_get_off():
        return minutes_after(shown(at_get_off)) if at_get_off is not None else progress.get_off_at

    def there_by():
        """When it's at the stop to get off at by, ON_TIME_TARGET of the time (#174)."""
        if at_get_off is None or late is None:
            return reaches_get_off()
        return minutes_after(shown(at_get_off) + late(route_id, at_get_off))

    # Its GPS, while fresh: the stop it last served, and the one it had at the last look.
    fresh = bus is not None and bus["age_minutes"] is None and bus["last_stop"] is not None
    last_stop = bus["last_stop"] if fresh else None
    before = progress.last_stop
    if last_stop is not None:
        progress.last_stop = last_stop
    order = _route_stops(routes, route_id)

    def passed(stop):
        """Its GPS served `stop`, or moved past it since the last look; None
        when there's no fresh position now and then to say."""
        if last_stop is None or before is None:
            return None
        return last_stop == stop or (last_stop != before and bool(_crossed(order, before, last_stop, stop)))

    def jumped(minutes, last_time, stop):
        """Its predicted time at `stop` is a loop (or a second visit) later
        than the last: past it, if its GPS agrees or can't say."""
        if last_time is None or minutes_after(minutes) - last_time <= JUMP:
            return False
        gps = passed(stop)
        return True if gps is None else gps

    if not progress.boarded and (last_stop == board_stop_id or passed(board_stop_id)):
        progress.boarded = True  # its GPS served the stop to get on at, or went past it
    if progress.boarded and not progress.off and (last_stop == get_off_stop_id or passed(get_off_stop_id)):
        progress.off = True  # and then the stop to get off at

    if not progress.boarded:
        coming = at_board is not None and (at_get_off is None or at_board <= at_get_off)
        if coming and not jumped(at_board, progress.board_at, board_stop_id):
            progress.board_at = minutes_after(at_board)
            if at_get_off is not None:
                progress.get_off_at = minutes_after(at_get_off)
            return Follow(
                BOARDING if at_board == 0 else COMING,
                to_board=shown(at_board), to_get_off=shown(at_get_off), get_off_at=reaches_get_off(), bus=bus,
                get_off_by=there_by(),
            )
        # Past the first stop: timed to the second one first, coming round
        # again on its next loop, or no longer timed well after it was due.
        if coming or at_get_off is not None or (progress.board_at and now > progress.board_at + PASSED):
            progress.boarded = True
        else:
            return Follow(LOST, get_off_at=progress.get_off_at, bus=bus)

    if not progress.off:
        # On its way to the second stop: timed there before the first, or still
        # at the first (a countdown of 0 or 1 there is this visit, not the next loop).
        riding = at_get_off is not None and (at_board is None or at_get_off < at_board or at_board <= 1)
        if riding and not jumped(at_get_off, progress.get_off_at, get_off_stop_id):
            progress.get_off_at = minutes_after(at_get_off)
            if at_get_off == 0:
                phase = GET_OFF
            elif last_stop in before_get_off:  # only a fresh position's: an old one's last stop may be behind
                phase = NEXT_STOP
            else:
                phase = RIDING
            return Follow(phase, to_get_off=shown(at_get_off), get_off_at=reaches_get_off(), bus=bus, get_off_by=there_by())
        # Not timed to the second stop any more, or only on the next loop:
        # past it, once it was due there. Before then, the feed lost it.
        due = progress.get_off_at is None or now >= progress.get_off_at - dt.timedelta(minutes=1)
        if riding or (at_board is not None and at_get_off is not None) or due:
            progress.off = True
        else:
            return Follow(LOST, get_off_at=progress.get_off_at, bus=bus)

    return Follow(OFF, get_off_at=progress.get_off_at, bus=bus)


def _minutes(arrivals, route_id, bus_id):
    """The feed's minutes for that bus on that route at a stop (its soonest
    on the route, with no bus_id), or None when the stop's ETAs don't list it."""
    times = [
        arrival["minutes"]
        for arrival in arrivals
        if arrival["route_id"] == route_id
        and (bus_id is None or arrival["bus_id"] == bus_id)
        and arrival["minutes"] >= 0
    ]
    return min(times) if times else None


def _bus(buses, bus_id):
    """The bus from the feed's buses answer: where it is, its name, the stop
    it last served, and its position's age in minutes once that's stale.
    None when there's no bus to follow or the feed doesn't show it."""
    if bus_id is None:
        return None
    for bus in buses if isinstance(buses, list) else []:
        try:
            if int(bus["id"]) != bus_id:
                continue
            age = max(0.0, time.time() - float(bus["lastUpdate"]))
            return {
                "lat": float(bus["lat"]),
                "lon": float(bus["lon"]),
                "name": str(bus.get("name") or ""),
                "last_stop": _int(bus.get("lastStop")),
                "age_minutes": round(age / 60) if age > STALE_SECONDS else None,
            }
        except (KeyError, TypeError, ValueError):
            continue
    return None


def _route_stops(routes, route_id):
    """The route's stops in loop order, from the feed's routes answer ([] when it doesn't list them)."""
    for route in routes if isinstance(routes, list) else []:
        if isinstance(route, dict) and route.get("id") == route_id:
            return [_int(stop) for stop in route.get("stops") or []]
    return []


def _crossed(order, before, after, stop):
    """Whether a bus whose last stop went from `before` to `after` went past
    `stop` on the way, forward round the loop (the shortest way, where a
    stop is served twice). None when the loop doesn't list them."""
    best = None
    for start, here in enumerate(order):
        if here != before:
            continue
        for step in range(1, len(order) + 1):
            if order[(start + step) % len(order)] == after:
                if best is None or step < best[1]:
                    best = (start, step)
                break
    if best is None:
        return None
    start, steps = best
    return any(order[(start + step) % len(order)] == stop for step in range(1, steps + 1))


def _stops_before(routes, route_id, stop_id):
    """The stops the route serves right before `stop_id`, in its loop order
    (a stop it serves twice has two). Empty when the routes answer doesn't
    list the route's stops."""
    for route in routes if isinstance(routes, list) else []:
        if not isinstance(route, dict) or route.get("id") != route_id:
            continue
        stops = [_int(stop) for stop in route.get("stops") or []]
        return {stops[index - 1] for index, stop in enumerate(stops) if stop == stop_id and len(stops) > 1}
    return set()


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _when(text):
    try:
        return dt.datetime.fromisoformat(text) if text else None
    except (TypeError, ValueError):
        return None
