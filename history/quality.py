"""How good is our shuttle data? Where it has gaps, how stale the buses' GPS
gets, how much each route gives us, and a spot-check of the arrivals we infer
against the buses' own GPS (#74, from Kyle's Sprint 1 feedback in #71).

The spot-check: the feed reports each bus's last stop. When that changes
between two polls five minutes apart, the bus reached the new stop somewhere
in that window, by its own GPS. The arrival our history infers for the same
bus and stop (`predictions._arrival_events`, from the ETA counting down)
should fall in the same window.

`python manage.py data_quality` prints the whole report.
"""

import datetime as dt
import statistics
from collections import Counter, defaultdict

from .predictions import ARRIVAL_ETA, _arrival_events

MAX_POLL_GAP = dt.timedelta(minutes=6)  # polls further apart (a missed poll) aren't compared
SLACK = dt.timedelta(minutes=10)  # an inferred arrival further than this from a GPS window is another visit


def _time(value):
    return dt.datetime.fromisoformat(value)


def inferred_arrivals(eta_rows):
    """{(route_id, stop_id, bus_id): [arrival, ...]}: the arrivals the history
    learns from these ETA rows (dicts, as in the recorder's etas.csv), worked
    out the way the predictions do."""
    approaches = defaultdict(list)
    for row in eta_rows:
        if not row["eta_minutes"] or int(row["eta_minutes"]) > ARRIVAL_ETA:
            continue
        bus = int(row["bus_id"])
        key = (int(row["route_id"]), int(row["stop_id"]), bus)
        approaches[key].append((_time(row["polled_at_utc"]), bus, int(row["eta_minutes"])))
    return {key: _arrival_events(sorted(rows)) for key, rows in approaches.items()}


def gps_visits(bus_rows):
    """[(route_id, stop_id, bus_id, after, by)]: each time a bus's own GPS put
    it at a new stop, the stop and the window it got there in. `bus_rows` are
    dicts, as in the recorder's buses.csv."""
    polls = defaultdict(list)
    for row in bus_rows:
        if row["last_stop_id"] and row["route_id"] and row["last_update_utc"]:
            polls[int(row["bus_id"])].append(
                (_time(row["polled_at_utc"]), int(row["route_id"]), int(row["last_stop_id"]), _time(row["last_update_utc"]))
            )
    visits = []
    for bus, rows in polls.items():
        rows.sort()
        for (before, route, stop, _), (polled, next_route, next_stop, fix) in zip(rows, rows[1:]):
            # A new last stop on the same route, from a fresh GPS fix, between
            # two consecutive polls: the bus reached it after `before` and by `fix`.
            if next_stop != stop and next_route == route and polled - before <= MAX_POLL_GAP and fix > before:
                visits.append((route, next_stop, bus, before, min(polled, fix)))
    return visits


def offset_minutes(arrival, after, by):
    """How far an inferred arrival falls outside the window (after, by]: 0 inside
    it, negative when it's early, positive when it's late."""
    if arrival < after:
        return (arrival - after).total_seconds() / 60
    if arrival > by:
        return (arrival - by).total_seconds() / 60
    return 0.0


def spot_check(visits, arrivals, served_pairs):
    """Compare each GPS visit with the nearest inferred arrival for the same
    route, stop and bus. Only stops the route gives ETAs for count.

    Returns (offsets, missed): offsets in minutes for the visits we inferred an
    arrival for, and the visits we didn't.
    """
    offsets, missed = [], []
    for visit in visits:
        route, stop, bus, after, by = visit
        if (route, stop) not in served_pairs:
            continue
        nearest = min(
            (offset_minutes(arrival, after, by) for arrival in arrivals.get((route, stop, bus), [])),
            key=abs,
            default=None,
        )
        if nearest is None or abs(nearest) > SLACK.total_seconds() / 60:
            missed.append(visit)
        else:
            offsets.append(nearest)
    return offsets, missed


def summarize_offsets(offsets, missed):
    total = len(offsets) + len(missed)
    if not total:
        return {"visits": 0}
    return {
        "visits": total,
        "inferred": len(offsets),
        "inside": sum(1 for offset in offsets if offset == 0),
        "within_2": sum(1 for offset in offsets if abs(offset) <= 2),
        "median_abs": statistics.median(abs(offset) for offset in offsets) if offsets else None,
        "early": sum(1 for offset in offsets if offset < -2),
        "late": sum(1 for offset in offsets if offset > 2),
    }


def gps_staleness(bus_rows, minutes=(3, 10)):
    """{route_name: (positions, {minutes: share older than that})}: how often a
    bus's reported position was older than each threshold when we polled."""
    ages = defaultdict(list)
    for row in bus_rows:
        if row["last_update_utc"] and row["route_name"]:
            age = (_time(row["polled_at_utc"]) - _time(row["last_update_utc"])).total_seconds() / 60
            ages[row["route_name"]].append(age)
    return {
        route: (len(values), {limit: sum(1 for age in values if age > limit) / len(values) for limit in minutes})
        for route, values in ages.items()
    }


def add_coverage(eta_rows, polls, stops):
    """Add these ETA rows to `polls` and `stops`, defaultdict(set)s by route name:
    the polls each route had ETAs in, and the stops it had them for."""
    for row in eta_rows:
        polls[row["route_name"]].add(row["polled_at_utc"])
        stops[row["route_name"]].add(row["stop_id"])


def gaps_by_day(gaps):
    """{date: Counter(cause)} from the recorder's gaps.csv rows (history.sources.read_gaps)."""
    days = defaultdict(Counter)
    for gap in gaps:
        days[gap["polled_at"].date()][gap["cause"] or "unknown"] += 1
    return dict(sorted(days.items()))
