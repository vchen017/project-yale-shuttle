"""The accuracy dashboard (#17): where the official ETA and our prediction
miss, per route, per stop and per hour of day, next to the riders' own
missed-bus reports (#6).

The scoring is `evaluate()` applied to a chosen date range, grouped as the
dashboard wants it: each held-out day's polls stand for riders asking
QUERY_OFFSET minutes later, the actual next arrival comes from the recorded
countdowns, and the held-out discipline stays the same (every third day is
scored, the rest train on) so a dashboard run and an evaluate() run agree.
Error is predicted minus actual: positive means we said the bus was farther
than it turned out to be. The official ETA is scored the way riders see it
(#116): the freshest poll's countdown at the query instant, aged by that
poll's own lag — not the sampled poll grown stale by the whole offset,
which overstated the official's error. The history layer is reported on its
own — it can beat the live-inclusive number on held-out days (see #23).

Each side also gets an on-time rate (#156): the share of its answers that
would have got a rider to the stop in time, had they timed their walk to
the time it gave. A bus that came up to ON_TIME_SLACK minutes before that
time still counts as caught, since arrivals are only known to within a
poll or so. It's what riders care about, where the errors are what we
tune: ON_TIME_BASELINE is where ours stood when we started tracking it,
and ON_TIME_GOAL the Sprint 4 goal (#155).

Riders' reports (#6) are counted next to the errors, in the same groups.
"""

import datetime as dt
from bisect import bisect_left
from collections import defaultdict

from .models import Eta, RiderReport, RouteStopList
from .predictions import (
    ET,
    HORIZON,
    QUERY_OFFSET,
    _arrivals_by_pair,
    _fit_corrections,
    _fused_countdown,
    _last_arrival_before,
    _learn,
    _predict_history,
    _shown_countdown,
    _shown_fresh,
    _shown_residuals,
    _stop_is_served,
    current_stops_by_route,
    held_out_days,
)

HOLDOUT_MODULUS = 3  # every third day is scored, the rest train on — as in evaluate()
STRIDE = 2  # every second poll stands for a rider asking
SIDES = ("ours", "official", "history")
MAX_RANGE_DAYS = 31  # the longest range a dashboard load or a scoring run covers
ON_TIME_SLACK = 2  # minutes the bus may beat the time we gave and still be caught
ON_TIME_BASELINE = 78  # % of our answers on time over Oct 26 - Nov 25 (#153's replay)
ON_TIME_GOAL = 90  # % on time: the Sprint 4 goal (#155)


def eastern_midnight(day):
    """A date's midnight on the Eastern clock, as datetimes are stored: UTC."""
    return dt.datetime.combine(day, dt.time(0), tzinfo=ET).astimezone(dt.timezone.utc)


def accuracy(since, until):
    """The dashboard's numbers for the days in [since, until).

    Returns the overall row and one row per route, stop and hour of day
    (Eastern), each with the queries scored, the mean signed error and the
    mean absolute error of each side that answered, and the rider reports
    filed there (real and sample counted apart).

    Its database reads don't grow with the queries scored — the range's
    arrivals and polls in bulk, each scored day's polls, the routes' stop
    lists once — because the nightly job runs against the production
    database, where every query is a network round trip.
    """
    arrivals_by_pair = _arrivals_by_pair(since, until)
    history_days = {
        moment.astimezone(ET).date() for moments in arrivals_by_pair.values() for moment in moments
    }
    test_days = held_out_days(history_days, HOLDOUT_MODULUS)
    profiles = {
        pair: _learn(moments, exclude_dates=test_days) for pair, moments in arrivals_by_pair.items()
    }
    residuals = _shown_residuals(
        since, until, exclude_dates=test_days, arrivals_by_pair=arrivals_by_pair
    )
    corrections = _fit_corrections(residuals)
    current_stops = current_stops_by_route()  # one query, not one per scored query

    names = {}  # (kind, key) -> the name the recorded polls served
    signed = defaultdict(float)  # (side, kind, key) -> sum of (predicted - actual)
    absolute = defaultdict(float)  # ... -> sum of |predicted - actual|
    answered = defaultdict(int)  # ... -> queries this side answered
    on_time = defaultdict(int)  # ... -> of those, how many would have got the rider there in time
    scored = defaultdict(int)  # (kind, key) -> queries scored at all
    no_prediction = not_served = 0

    for day in sorted(test_days):
        day_start_utc = dt.datetime.combine(day, dt.time(0), tzinfo=ET).astimezone(dt.UTC)
        polls = (
            Eta.objects.filter(
                polled_at__gte=day_start_utc,
                polled_at__lt=day_start_utc + dt.timedelta(days=1),
            )
            .order_by("polled_at", "id")
            .values_list(
                "polled_at", "route_id", "route_name", "stop_id", "stop_name", "eta_minutes"
            )
        )
        by_poll = defaultdict(lambda: defaultdict(list))
        # Ordered by polled_at, so setdefault keeps the first name the pair
        # was known by in the window — the same name every run, even where
        # the feed renamed it mid-window; unordered rows made that pick
        # arbitrary on PostgreSQL (#190).
        for polled_at, route_id, route_name, stop_id, stop_name, eta_minutes in polls:
            names.setdefault(("route", route_id), route_name)
            names.setdefault(("stop", (route_id, stop_id)), stop_name)
            by_poll[polled_at][(route_id, stop_id)].append(eta_minutes)
        poll_times = sorted(by_poll)

        # What the pair was being shown at each poll: the countdown the
        # official app displays (#116), and the guarded copy ours answers
        # from (#117) — as in evaluate().
        shown_times = defaultdict(list)
        shown_values = defaultdict(list)
        for polled_at in poll_times:
            for pair, etas in by_poll[polled_at].items():
                shown_times[pair].append(polled_at)
                shown_values[pair].append(min(etas))

        for polled_at in poll_times[::STRIDE]:
            query_at = polled_at + QUERY_OFFSET
            hour = query_at.astimezone(ET).hour
            for pair, etas in sorted(by_poll[polled_at].items()):
                if not _stop_is_served(*pair, current_by_route=current_stops):
                    not_served += 1
                    continue
                moments = arrivals_by_pair.get(pair, ())
                index = bisect_left(moments, query_at)
                next_arrival = moments[index] if index < len(moments) else None
                if next_arrival is None or next_arrival > query_at + HORIZON:
                    continue  # no measurable arrival: neither side gets scored
                actual = (next_arrival - query_at).total_seconds() / 60
                profile = profiles.get(pair)
                ours = _fused_countdown(
                    pair[0],
                    _shown_fresh(shown_times.get(pair, []), shown_values.get(pair, []), query_at),
                    corrections,
                )
                if ours is None and profile is not None:
                    ours = _predict_history(
                        profile, query_at, _last_arrival_before(moments, query_at)
                    )
                if ours is None:
                    no_prediction += 1
                    continue
                history = (
                    _predict_history(profile, query_at, _last_arrival_before(moments, query_at))
                    if profile is not None
                    else None
                )
                official = _shown_countdown(
                    shown_times.get(pair, []), shown_values.get(pair, []), query_at
                )
                for kind, key in (
                    ("route", pair[0]),
                    ("stop", pair),
                    ("hour", hour),
                    ("all", None),
                ):
                    scored[(kind, key)] += 1
                    for side, value in (
                        ("ours", ours),
                        ("official", official),
                        ("history", history),
                    ):
                        if value is None:
                            continue
                        error = value - actual  # predicted minus actual (#17)
                        signed[(side, kind, key)] += error
                        absolute[(side, kind, key)] += abs(error)
                        answered[(side, kind, key)] += 1
                        if error <= ON_TIME_SLACK:  # the bus didn't come before the rider did
                            on_time[(side, kind, key)] += 1

    reports = {"route": defaultdict(int), "stop": defaultdict(int), "hour": defaultdict(int)}
    sample_reports = {
        "route": defaultdict(int),
        "stop": defaultdict(int),
        "hour": defaultdict(int),
    }
    for row in RiderReport.objects.filter(created_at__gte=since, created_at__lt=until):
        bucket = sample_reports if row.is_sample else reports
        bucket["route"][row.route_id] += 1
        bucket["stop"][(row.route_id, row.stop_id)] += 1
        bucket["hour"][row.created_at.astimezone(ET).hour] += 1

    def row(kind, key):
        data = {
            "key": key,
            "name": names.get((kind, key)),
            "queries": scored[(kind, key)],
            "reports": reports.get(kind, {}).get(key, 0),
            "sample_reports": sample_reports.get(kind, {}).get(key, 0),
        }
        for side in SIDES:
            count = answered.get((side, kind, key))
            if count:
                mean = signed[(side, kind, key)] / count
                mae = absolute[(side, kind, key)] / count
                rate = on_time[(side, kind, key)] / count
                data[side] = {
                    "mean": mean,
                    "mae": mae,
                    "answered": count,
                    "mean_display": _signed(mean),
                    "mae_display": f"{mae:.1f}",
                    "on_time": rate,
                    "on_time_percent": round(100 * rate),
                }
            else:
                data[side] = None
        return data

    def fill_names(rows, kind):
        """Names the polls never served (a report-only route, say) still read."""
        for data in rows:
            if data["name"] is None and kind == "route":
                data["name"] = route_label(data["key"])
            elif data["name"] is None:
                data["name"] = f"stop {data['key'][1]}"
        return rows

    def keys(kind):
        """Every group of this kind that has queries or reports to show."""
        return (
            {key for (that, key) in scored if that == kind}
            | set(reports[kind])
            | set(sample_reports[kind])
        )

    overall = row("all", None)
    overall["label"] = "Whole range"
    by_route = fill_names(
        sorted((row("route", key) for key in keys("route")), key=_route_sort), "route"
    )
    for data in by_route:
        data["label"] = data["name"]
    by_stop = fill_names(
        sorted((row("stop", key) for key in keys("stop")), key=_stop_sort), "stop"
    )
    for data in by_stop:
        route_name = names.get(("route", data["key"][0])) or route_label(data["key"][0])
        data["label"] = f"{route_name} · {data['name']}"
    by_hour = sorted((row("hour", key) for key in keys("hour")), key=lambda data: data["key"])
    for data in by_hour:
        data["label"] = _hour_label(data["key"])
    return {
        "since": since,
        "until": until,
        "scored_days": sorted(test_days),
        "history_days": len(history_days),
        "no_prediction": no_prediction,
        "not_served": not_served,
        "reports": sum(reports["route"].values()),
        "sample_reports": sum(sample_reports["route"].values()),
        "overall": overall,
        "overall_list": [overall],
        "by_route": by_route,
        "by_stop": by_stop,
        "by_hour": by_hour,
    }


def to_storage(result):
    """The result as JSON-safe data, for an AccuracySnapshot's payload (#17).

    Dates become ISO strings and tuples become lists; from_storage() undoes
    both, so what the dashboard reads back behaves like a fresh accuracy()
    result."""
    data = dict(result)
    data["since"] = result["since"].isoformat()
    data["until"] = result["until"].isoformat()
    data["scored_days"] = [day.isoformat() for day in result["scored_days"]]
    return data


def from_storage(data):
    """A snapshot's payload, as accuracy() would have returned it."""
    result = dict(data)
    # since and until were the UTC datetimes accuracy() was given; the scored
    # days are Eastern dates.
    result["since"] = dt.datetime.fromisoformat(result["since"])
    result["until"] = dt.datetime.fromisoformat(result["until"])
    result["scored_days"] = [dt.date.fromisoformat(day) for day in result["scored_days"]]
    # JSON turned the stop pairs into lists; the template indexes key.1 either
    # way, but keep what accuracy() produced so a saved and a fresh result
    # compare equal.
    result["by_stop"] = [dict(row, key=tuple(row["key"])) for row in result["by_stop"]]
    return result


def _signed(value):
    """A mean error as the page shows it: '+2.4' means we said the bus was
    farther than it was; '0.0' never carries a sign."""
    rounded = round(value, 1)
    if rounded == 0:
        return "0.0"
    return f"+{rounded:.1f}" if rounded > 0 else f"{rounded:.1f}"


def _hour_label(hour):
    """An Eastern hour as riders say it: 9 -> '9 AM', 22 -> '10 PM'."""
    if hour == 0:
        return "12 AM"
    if hour < 12:
        return f"{hour} AM"
    if hour == 12:
        return "12 PM"
    return f"{hour - 12} PM"


def route_label(route_id):
    """The route's newest recorded name, or 'Route N' when history has none."""
    name = (
        RouteStopList.objects.filter(route_id=route_id)
        .order_by("-first_seen", "-id")
        .values_list("route_name", flat=True)
        .first()
    )
    return name or f"Route {route_id}"


def _route_sort(data):
    return (data["name"] or "", data["key"])


def _stop_sort(data):
    return (data["key"][0], data["key"][1], data["name"] or "")
