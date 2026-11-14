"""Our own arrival predictions, learned from the imported history (#23).

`predict_minutes(route_id, stop_id, now=None)` returns our predicted minutes
until that route's next bus reaches the stop, or None when we have no
prediction. The next-buses page (#24), trip plans (#26) and the AI agent
(#28) call it, so the signature stays.

How it learns: a poll where a bus shows eta_minutes 0 is a bus at that stop.
Those arrival moments — one per bus arrival, merged when the same bus sits
at the stop across several polls — fill half-hour buckets of the Eastern
clock, one set per day type (weekday, Saturday, Sunday). Each bucket keeps
the distribution of "minutes until the next bus, if you walk up then",
sampled every five minutes across four weeks of history. The prediction is
the bucket's median wait, widened to neighbouring buckets when the exact one
has no data (late night, quiet stops). When even the neighbours are empty —
outside service hours, mostly — there is no prediction: a route that isn't
running has no wait to predict. It is
history only: the live feed plays no part, so a prediction works even when
the feed is down.

`evaluate()` scores the prediction on days held out from training and
reports our average error next to the official ETA's; the
evaluate_predictions command prints it.
"""

import datetime as dt
import statistics
from bisect import bisect_left
from collections import defaultdict
from zoneinfo import ZoneInfo

from django.utils import timezone

from .models import Eta

ET = ZoneInfo("America/New_York")

HISTORY_DAYS = 28  # how far back one prediction looks
BUCKET_MINUTES = 30  # width of a time-of-day bucket
SAMPLE_STEP = dt.timedelta(minutes=5)  # resolution of sampled walk-up waits
DWELL_TOLERANCE = dt.timedelta(minutes=5)  # zero-ETAs this close are one bus dwelling
HORIZON = dt.timedelta(hours=6)  # ground truth: next arrival within this long


def predict_minutes(route_id, stop_id, now=None):
    """Our predicted minutes until the route's next bus reaches the stop.

    None when there's nothing to learn from. `now` defaults to now; only
    history from before it is used.
    """
    now = now or timezone.now()
    rows = (
        Eta.objects.filter(
            route_id=route_id,
            stop_id=stop_id,
            eta_minutes__lte=0,
            polled_at__gt=now - dt.timedelta(days=HISTORY_DAYS),
            polled_at__lte=now,
        )
        .order_by("polled_at", "bus_id")
        .values_list("polled_at", "bus_id")
    )
    return _predict(_learn(_arrival_moments(rows)), now)


def evaluate(holdout_modulus=3, stride=2, days=HISTORY_DAYS, now=None):
    """Score our prediction against the official ETA on held-out days.

    Every holdout_modulus-th day of history (by its Eastern date) is held
    out from training, deterministically, so runs are comparable. At every
    `stride`-th poll on those days, each (route, stop) with an official ETA
    is scored against the minutes until its next recorded arrival. A query
    with no measurable next arrival, or no prediction of ours, is skipped
    and counted instead. Returns a dict with ours_mae and official_mae, the
    average absolute errors in minutes.
    """
    now = now or timezone.now()
    since = now - dt.timedelta(days=days)
    arrivals_by_pair = _arrivals_by_pair(since, until=now)
    history_days = {
        moment.astimezone(ET).date() for moments in arrivals_by_pair.values() for moment in moments
    }
    test_days = held_out_days(history_days, holdout_modulus)

    # One profile per pair, trained on everything except the held-out days.
    profiles = {
        pair: _learn(moments, exclude_dates=test_days)
        for pair, moments in arrivals_by_pair.items()
    }

    queries = no_prediction = 0
    ours_error = official_error = 0.0
    for day in sorted(test_days):
        day_start = dt.datetime.combine(day, dt.time(0), tzinfo=ET)
        # Just the held-out day's polls: an arrival after midnight is found
        # through arrivals_by_pair, which spans the whole window.
        day_start_utc = day_start.astimezone(dt.UTC)
        polls = Eta.objects.filter(
            polled_at__gte=day_start_utc,
            polled_at__lt=day_start_utc + dt.timedelta(days=1),
        ).values_list("polled_at", "route_id", "stop_id", "eta_minutes")
        by_poll = defaultdict(lambda: defaultdict(list))
        for polled_at, route_id, stop_id, eta_minutes in polls:
            by_poll[polled_at][(route_id, stop_id)].append(eta_minutes)

        for polled_at in sorted(by_poll)[::stride]:
            for pair, etas in sorted(by_poll[polled_at].items()):
                moments = arrivals_by_pair.get(pair, ())
                index = bisect_left(moments, polled_at)
                next_arrival = moments[index] if index < len(moments) else None
                if next_arrival is None or next_arrival > polled_at + HORIZON:
                    continue  # no measurable arrival: neither side gets scored
                actual = (next_arrival - polled_at).total_seconds() / 60
                ours = _predict(profiles.get(pair), polled_at)
                if ours is None:
                    no_prediction += 1
                    continue
                queries += 1
                ours_error += abs(ours - actual)
                official_error += abs(min(etas) - actual)

    return {
        "test_days": sorted(test_days),
        "history_days": len(history_days),
        "queries": queries,
        "no_prediction": no_prediction,
        "ours_mae": round(ours_error / queries, 2) if queries else None,
        "official_mae": round(official_error / queries, 2) if queries else None,
    }


def held_out_days(days, modulus):
    """The days to hold out: their Eastern date's ordinal divides by modulus."""
    return {day for day in days if modulus and day.toordinal() % modulus == 0}


def _arrival_moments(rows):
    """Zero-ETA rows, [(polled_at, bus_id)], sorted by time: arrival moments.

    A bus dwelling at the stop spans several zero-ETA polls; that's one
    arrival, at the first poll it shows 0. Two buses at the stop together
    are one moment: the next bus is the one already there.
    """
    last_zero = {}
    moments = set()
    for polled_at, bus_id in rows:
        previous = last_zero.get(bus_id)
        if previous is None or polled_at - previous > DWELL_TOLERANCE:
            moments.add(polled_at)
        last_zero[bus_id] = polled_at
    return sorted(moments)


def _arrivals_by_pair(since, until=None):
    """All arrivals in the window, grouped by (route_id, stop_id)."""
    queryset = Eta.objects.filter(eta_minutes__lte=0, polled_at__gte=since)
    if until:
        queryset = queryset.filter(polled_at__lt=until)
    rows = (
        queryset.order_by("polled_at", "route_id", "stop_id", "bus_id")
        .values_list("polled_at", "route_id", "stop_id", "bus_id")
    )
    grouped = defaultdict(list)
    for polled_at, route_id, stop_id, bus_id in rows:
        grouped[(route_id, stop_id)].append((polled_at, bus_id))
    return {pair: _arrival_moments(moments) for pair, moments in grouped.items()}


def _learn(arrivals, exclude_dates=frozenset()):
    """Median walk-up wait per (day type, half-hour bucket) of the Eastern clock."""
    by_date = defaultdict(list)
    for moment in arrivals:
        by_date[moment.astimezone(ET).date()].append(moment.astimezone(ET))

    waits = defaultdict(list)
    for day, moments in by_date.items():
        if day in exclude_dates:
            continue
        # Walk up at any minute t between two arrivals and the next bus is
        # the later one: from t in [a, b) the wait is b - t, sampled every
        # five minutes. Before the first arrival too (a long wait, but a
        # real one). After the day's last arrival it isn't knowable: skip.
        interval_start = dt.datetime.combine(day, dt.time(0), tzinfo=ET)
        for next_arrival in moments:
            t = interval_start
            while t < next_arrival:
                waits[(_day_type(t), _bucket(t))].append(
                    (next_arrival - t).total_seconds() / 60
                )
                t += SAMPLE_STEP
            interval_start = next_arrival
    return {key: statistics.median(values) for key, values in waits.items()}


def _predict(profile, now):
    """The median wait for now's bucket, widened to the neighbouring
    buckets; None when even they have no data, so a route that isn't
    running right now (service hours ended, mostly) predicts nothing
    instead of borrowing a wait from a different time of day."""
    if not profile:
        return None
    local = now.astimezone(ET)
    key = (_day_type(local), _bucket(local))
    for offsets in ([0], [0, -1, 1], [0, -1, 1, -2, 2]):
        values = [
            value
            for offset in offsets
            if (value := profile.get((key[0], key[1] + offset))) is not None
        ]
        if values:
            return round(statistics.median(values), 1)
    return None


def _day_type(local):
    if local.weekday() == 5:
        return "saturday"
    if local.weekday() == 6:
        return "sunday"
    return "weekday"


def _bucket(local):
    return (local.hour * 60 + local.minute) // BUCKET_MINUTES
