"""Our own arrival predictions, learned from the imported history (#23).

`predict_minutes(route_id, stop_id, now=None)` returns our predicted minutes
until that route's next bus reaches the stop, or None when we have no
prediction. The next-buses page (#24), trip plans (#26) and the AI agent
(#28) call it, so the signature stays.

Two layers, live first:

*Live* — a countdown, corrected. The first source is the feed itself,
through the transit app's helper (`transit.downtowner.etas`, cached 30
seconds), used only when the caller asks about right now: its countdown is
about the present, so a simulated `now` never reads it, and a feed outage
falls through quietly to the next source instead of crashing the page. The
second is the freshest recorded poll for the stop: its soonest countdown,
aged by the minutes since that poll landed, so the number ticks down a
minute per minute between polls. This is the countdown the official app
shows, and the Nov 17 audit (#117) said to answer with it rather than our
earlier rule — which held the soonest implied arrival of the whole
15-minute window and never let it rise, promising buses that came later
than predicted 56% of the time. The countdown can rise again when a new
poll revises the bus farther out: the audit measured that as the more
honest promise (late answers 56% -> 40%), and it is what riders of the
official app already live with.

A countdown at or below RAW_MAX is answered as-is: when the feed says the
bus is five minutes out it is five minutes out (MAE 0.08 in the audit at
actual waits ≤3), and a learned correction there only wrecked the imminent
answer. Above that, the correction the recorded history teaches for
countdowns of that size (Corrections, refit nightly) is added — the
history's median "how wrong is a countdown of this size" per route and
size bucket. Beyond LIVE_MAX_AGE with no fresher poll, or once the
countdown has run more than LIVE_EXPIRY past due, the countdown has
nothing left to say.

*History* is the fallback, for when nothing live is fresh enough (imports
lag, the feed is down), the bus is overdue, or nothing is coming (service
hours ended). How it learns: a poll with eta_minutes at or below
ARRIVAL_ETA is the end of a bus's approach — the recorder polls every five
minutes, so the poll rarely lands on the arrival itself, and the moment is
the countdown run to zero. Those arrival moments fill half-hour buckets of
the Eastern clock, one set per day type (weekday, Saturday, Sunday). Each
bucket keeps the distribution of "minutes until the next bus, if you walk
up then", sampled every five minutes across four weeks of history. The
prediction is the bucket's median wait, widened to neighbouring buckets
when the exact one has no data (late night, quiet stops). When even the
neighbours are empty — outside service hours, mostly — there is no
prediction: a route that isn't running has no wait to predict.

The history layer also conditions on what today already told us: when a
bus reached the stop recently (from polls, so the elapsed time is
trustworthy), the wait is the median residual for how long the rider has
been since that arrival, not the blind walk-up median — a bus three
minutes gone means a shorter wait than a bus that just came. A stop the
route no longer serves gets no prediction: routes and stops change during
the term (#57), and a moved stop's old arrivals must not look like a
prediction.

`evaluate()` scores what a rider would be told on days held out from
training and reports our average error next to the official ETA's; the
evaluate_predictions command prints it. Its "ours" is the fused countdown
above, fitted on the training days only, so the score is the predictor
production actually runs.
"""

import datetime as dt
import statistics
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.utils import timezone

from transit import downtowner
from transit.downtowner import FeedUnavailable

from .models import Calibration, Eta, RouteStopList

ET = ZoneInfo("America/New_York")

HISTORY_DAYS = 28  # how far back one prediction looks
BUCKET_MINUTES = 30  # width of a time-of-day bucket
SAMPLE_STEP = dt.timedelta(minutes=5)  # resolution of sampled walk-up waits
HORIZON = dt.timedelta(hours=6)  # ground truth: next arrival within this long
ARRIVAL_ETA = 5  # an ETA this low is the end of an approach: the bus is here
APPROACH_GAP = dt.timedelta(minutes=12)  # sightings this far apart are separate
LIVE_MAX_AGE = dt.timedelta(minutes=15)  # polls older than this are not live
LIVE_EXPIRY = dt.timedelta(minutes=3)  # an implied arrival this far past is gone
QUERY_OFFSET = dt.timedelta(minutes=7)  # evaluate(): rider opens the app this
# long after a poll — an arbitrary minute, not the instant data landed
MAX_CONDITIONING = dt.timedelta(minutes=45)  # condition only on arrivals this
# recent: older than that, and unseen buses make the elapsed time a lie
MIN_BAND_SAMPLES = 8  # a residual point sampled fewer times defers to the buckets
RAW_MAX = 5  # countdowns this small are answered untouched (#117): the
# audit's imminent regime is near-perfect raw, and correcting it hurt
COUNTDOWN_BUCKETS = ((0, 5), (5, 10), (10, 15), (15, 20), (20, 30), (30, float("inf")))
MIN_CALIBRATION_SAMPLES = 20  # a (route, bucket) with fewer residuals defers
# to the all-routes row for its bucket
OVERALL_BUCKET = -1  # the Corrections row that covers every countdown
CALIBRATION_CACHE_SECONDS = 600  # the stored corrections, re-read this often


@dataclass
class Profile:
    """What one (route, stop) knows: the walk-up median per time-of-day
    bucket, and the median still-to-wait per (day type, minutes since the
    last arrival), interpolated between the sampled elapsed points."""

    buckets: dict  # (day type, bucket) -> median walk-up wait in minutes
    residuals: dict  # (day type, elapsed minutes) -> median residual wait


@dataclass
class Corrections:
    """What the recorded history says about the feed's countdowns (#117):
    the median minutes a countdown of a given size was off by, per (route,
    countdown bucket), with the all-routes medians beneath and the overall
    median beneath those. `delta` walks that ladder; an empty fit — a new
    database, no history yet — corrects nothing, so the raw countdown
    answers and a fresh deploy degrades safely."""

    by_route: dict  # (route id, bucket) -> median residual
    by_bucket: dict  # bucket -> median residual
    overall: float  # median residual over everything fitted

    def delta(self, route_id, countdown):
        """The minutes to add to a countdown of this size for this route."""
        bucket = countdown_bucket(countdown)
        if (route_id, bucket) in self.by_route:
            return self.by_route[(route_id, bucket)]
        if bucket in self.by_bucket:
            return self.by_bucket[bucket]
        return self.overall


def countdown_bucket(countdown):
    """Which slice of COUNTDOWN_BUCKETS a countdown falls in."""
    for index, (lo, hi) in enumerate(COUNTDOWN_BUCKETS):
        if lo <= countdown < hi:
            return index
    return len(COUNTDOWN_BUCKETS) - 1


def _last_arrival_before(arrivals, now):
    """The newest arrival at or before `now`, or None. `arrivals` is sorted."""
    index = bisect_right(arrivals, now)
    return arrivals[index - 1] if index else None


def current_stops(route_id):
    """The stop ids in the route's latest observed stop list (#57), or None
    when no routes.json walk has run yet.

    Routes and stops change during the term (#57: on Nov 13 Red and Brown
    gained 100 Church Street South and Red dropped Amistad / Church St
    South), so a stop's history is only trustworthy while the route still
    serves it.
    """
    row = RouteStopList.objects.filter(route_id=route_id).order_by("-first_seen", "-id").first()
    return set(row.stop_ids) if row else None


def current_stops_by_route():
    """What current_stops() answers for every walked route, in one query.

    For a caller that asks about thousands of (route, stop) pairs: the
    accuracy scoring (#17) asks once per scored query, and against the
    production database each ask was a network round trip. A route the
    walk never recorded is left out, so its stops all count as served.
    """
    current = {}
    rows = RouteStopList.objects.order_by("route_id", "-first_seen", "-id").values_list(
        "route_id", "stop_ids"
    )
    for route_id, stop_ids in rows:
        if route_id not in current:  # the newest version, as current_stops() picks it
            current[route_id] = set(stop_ids)
    return current


def _stop_is_served(route_id, stop_id, current_by_route=None):
    """False only once the walk has run and shows the route no longer
    serves the stop; before any walk, everything counts as served.

    `current_by_route`, from current_stops_by_route(), answers without a
    query of its own."""
    if current_by_route is None:
        current = current_stops(route_id)
    else:
        current = current_by_route.get(route_id)
    return current is None or stop_id in current


def predict_minutes(route_id, stop_id, now=None, use_live_feed=True, current_by_route=None):
    """Our predicted minutes until the route's next bus reaches the stop.

    None when nothing live is fresh and history has nothing to learn from,
    or when the route no longer serves the stop (#57): a moved or removed
    stop's old arrivals must not look like a prediction. `now` defaults to
    now, and only data from before it is used — the live feed answers only
    calls that take the default, because its countdown is about the
    present, not a simulated moment.

    `use_live_feed=False` skips the feed's own countdown, so the answer comes
    only from our recorded polls and history. Pages that show the official
    time next to ours use it (the next-buses page, #83): with the feed, our
    number would just repeat the official one.

    A caller asking about many routes at one stop (the next-buses board)
    passes `current_by_route` from current_stops_by_route(), so the served
    check reads one query instead of one per route.
    """
    if not _stop_is_served(route_id, stop_id, current_by_route=current_by_route):
        return None
    asked_about_now = now is None
    now = now or timezone.now()
    corrections = stored_corrections()
    if asked_about_now and use_live_feed:
        fused = _fused_countdown(route_id, _live_feed_minutes(route_id, stop_id), corrections)
        if fused is not None:
            return fused
    fused = _fused_countdown(route_id, _live_minutes(route_id, stop_id, now), corrections)
    if fused is not None:
        return fused
    rows = (
        Eta.objects.filter(
            route_id=route_id,
            stop_id=stop_id,
            eta_minutes__lte=ARRIVAL_ETA,
            polled_at__gt=now - dt.timedelta(days=HISTORY_DAYS),
            polled_at__lte=now,
        )
        .order_by("polled_at", "bus_id")
        .values_list("polled_at", "bus_id", "eta_minutes")
    )
    arrivals = _arrival_events(rows)
    profile = _learn(arrivals)
    return _predict_history(profile, now, _last_arrival_before(arrivals, now))


def _fused_countdown(route_id, countdown, corrections):
    """The live answer (#117): the countdown, plus the history's correction
    for countdowns of its size — unless it is at or below RAW_MAX, where the
    countdown is already near-perfect and a correction only hurt. None
    passes through, sending the caller to the history layer. A corrected
    answer never lands below zero: a countdown of 7 taught −10 is "boarding
    now", not a negative wait."""
    if countdown is None:
        return None
    if countdown <= RAW_MAX:
        return round(float(countdown), 1)
    return round(max(countdown + corrections.delta(route_id, countdown), 0.0), 1)


def stored_corrections():
    """The fitted Corrections, from the table the nightly fit_calibration
    job rewrites (#117), held in the cache between reads. Nothing stored —
    a fresh database, or the job yet to run — is an empty fit: the raw
    countdown answers uncorrected."""
    rows = cache.get("history.calibration")
    if rows is None:
        rows = list(Calibration.objects.values_list("route_id", "bucket", "delta_minutes"))
        cache.set("history.calibration", rows, CALIBRATION_CACHE_SECONDS)
    by_route = {
        (route_id, bucket): delta
        for route_id, bucket, delta in rows
        if route_id is not None and bucket != OVERALL_BUCKET
    }
    by_bucket = {
        bucket: delta
        for route_id, bucket, delta in rows
        if route_id is None and bucket != OVERALL_BUCKET
    }
    overall = next(
        (delta for route_id, bucket, delta in rows if route_id is None and bucket == OVERALL_BUCKET),
        0.0,
    )
    return Corrections(by_route=by_route, by_bucket=by_bucket, overall=overall)


def _fit_corrections(residuals):
    """The Corrections for residuals keyed (route id, countdown bucket): the
    median per key where at least MIN_CALIBRATION_SAMPLES taught it, the
    all-routes median per bucket beneath, the overall median beneath that."""
    by_route = {
        key: statistics.median(values)
        for key, values in residuals.items()
        if len(values) >= MIN_CALIBRATION_SAMPLES
    }
    pooled = defaultdict(list)
    for (route_id, bucket), values in residuals.items():
        pooled[bucket].extend(values)
    by_bucket = {bucket: statistics.median(values) for bucket, values in pooled.items()}
    everything = [value for values in residuals.values() for value in values]
    overall = statistics.median(everything) if everything else 0.0
    return Corrections(by_route=by_route, by_bucket=by_bucket, overall=overall)


def _shown_residuals(since, until=None, exclude_dates=frozenset(), arrivals_by_pair=None):
    """What the countdowns got wrong, for the correction fit (#117): over
    the window's polls, each poll's soonest countdown against the next
    recorded arrival — the residual (arrival - poll) - countdown, in
    minutes, keyed (route id, countdown bucket). Days in exclude_dates are
    skipped, so an evaluation fits on its training days only; a poll whose
    countdown had already run more than LIVE_EXPIRY past due is skipped
    too, because production defers to history there and it has nothing to
    teach the countdown fit. Every poll teaches, not the evaluation's
    strided sample: the fit wants all the evidence it can get.

    A caller that already holds _arrivals_by_pair(since, until) passes it
    as `arrivals_by_pair`, so the window's arrivals are read only once.
    """
    if arrivals_by_pair is None:
        arrivals_by_pair = _arrivals_by_pair(since, until)
    rows = (
        Eta.objects.filter(polled_at__gte=since)
        .filter(polled_at__lte=until)
        .order_by("polled_at", "route_id", "stop_id", "bus_id")
        .values_list("polled_at", "route_id", "stop_id", "eta_minutes")
    )
    by_poll = defaultdict(lambda: defaultdict(list))
    for polled_at, route_id, stop_id, eta_minutes in rows:
        by_poll[(route_id, stop_id)][polled_at].append(eta_minutes)
    overdue = -LIVE_EXPIRY.total_seconds() / 60
    residuals = defaultdict(list)
    for pair, poll_times in by_poll.items():
        moments = arrivals_by_pair.get(pair, ())
        if not moments:
            continue
        for polled_at, etas in poll_times.items():
            if polled_at.astimezone(ET).date() in exclude_dates:
                continue
            countdown = min(etas)
            if countdown < overdue:
                continue
            index = bisect_left(moments, polled_at)
            next_arrival = moments[index] if index < len(moments) else None
            if next_arrival is None or next_arrival > polled_at + HORIZON:
                continue  # nothing measurable in sight: production defers to history
            residuals[(pair[0], countdown_bucket(countdown))].append(
                (next_arrival - polled_at).total_seconds() / 60 - countdown
            )
    return residuals


def _live_feed_minutes(route_id, stop_id):
    """The route's countdown at the stop, straight from the live Downtowner
    feed (transit.downtowner.etas, cached 30 seconds), the live layer's
    first source and the fusion's base. None when the feed serves no eta
    for this route at this stop, or when the feed is down:
    FeedUnavailable means the recorded polls answer next, then history —
    never a crashed page. Transit logs the outage itself.
    """
    try:
        arrivals = downtowner.etas(stop_id)
    except FeedUnavailable:
        return None
    minutes = [
        arrival["minutes"]
        for arrival in arrivals
        if arrival["route_id"] == route_id and arrival["minutes"] >= 0
    ]
    if not minutes:
        return None
    return round(float(min(minutes)), 1)


def _live_minutes(route_id, stop_id, now):
    """The freshest recorded poll's countdown for the route at the stop,
    aged to `now` (#117) — the same number the evaluation scores, so the
    fallback production runs is the fallback the evaluation measured. The
    poll's soonest eta minus the minutes since the poll, floored at 0.
    None when no poll is fresh enough (LIVE_MAX_AGE) or that countdown has
    run more than LIVE_EXPIRY past due (the bus was promised and never
    shown): then the history layer answers instead.
    """
    rows = list(
        Eta.objects.filter(
            route_id=route_id,
            stop_id=stop_id,
            polled_at__gt=now - LIVE_MAX_AGE,
            polled_at__lte=now,
        )
        .order_by("polled_at")
        .values_list("polled_at", "eta_minutes")
    )
    if not rows:
        return None
    freshest = max(polled_at for polled_at, _ in rows)
    soonest = min(eta for polled_at, eta in rows if polled_at == freshest)
    return _shown_fresh([freshest], [soonest], now)


def _shown_fresh(times, values, now):
    """The freshest poll's countdown at `now`, aged by that poll's own lag
    and floored at 0 — among polls no older than LIVE_MAX_AGE, and None
    once it has run more than LIVE_EXPIRY past due, where production
    defers to history. This is the evaluation's copy of the live answer;
    `_shown_countdown` is the official app's copy, which carries no
    freshness guard because the official app shows whatever it has."""
    start = bisect_right(times, now - LIVE_MAX_AGE)
    end = bisect_right(times, now)
    if end <= start:
        return None
    index = end - 1
    countdown = values[index] - (now - times[index]).total_seconds() / 60
    if countdown < -LIVE_EXPIRY.total_seconds() / 60:
        return None
    return max(countdown, 0.0)


def _shown_countdown(times, values, now):
    """The countdown a rider is shown at `now` (#116): the freshest poll's
    soonest eta, aged by how long ago that poll landed and floored at 0 —
    what the official app displays between its refreshes. None when no poll
    precedes `now`. The arrays are the per-pair shown_times/shown_values the
    evaluation loops build, one entry per poll where the pair appeared; the
    eta may be negative when the feed lists the bus as already at the stop,
    and the floor is what turns that into "boarding now".
    """
    index = bisect_right(times, now) - 1
    if index < 0:
        return None
    lag = (now - times[index]).total_seconds() / 60
    return max(values[index] - lag, 0.0)


def evaluate(holdout_modulus=3, stride=2, days=HISTORY_DAYS, now=None):
    """Score what riders would be told against the official ETA on held-out
    days.

    Every holdout_modulus-th day of history (by its Eastern date) is held
    out from training, deterministically, so runs are comparable. Each
    sampled poll stands for a rider opening the app QUERY_OFFSET minutes
    later — an arbitrary minute, not the instant a poll landed. Our answer
    there is exactly what predict_minutes would give: the live layer over
    that day's own polls, the history profile beneath it. Both are measured
    against the minutes until the next recorded arrival. A query with no
    measurable next arrival, or no prediction of ours, is skipped and
    counted instead. The history layer is scored on its own too
    (history_mae): it is what runs whenever no poll is fresh enough, so its
    error is the floor beneath the live layer's.

    The official ETA is scored twice, and the two numbers are not the same
    (see #116). official_mae is what a rider with the official app open
    would act on: the freshest poll's countdown at the query instant, aged
    by that poll's own lag and floored at 0 — the app recomputes from its
    newest data, whatever the evaluation's sampling did. official_aged_mae
    is the older scoring, which aged the sampled poll's implied arrival by
    the whole QUERY_OFFSET; it overstated the official's error by minutes
    (17.3 vs 13.5 on the recorded history), and it is kept only so numbers
    quoted before #116 stay reproducible.
    """
    now = now or timezone.now()
    since = now - dt.timedelta(days=days)
    arrivals_by_pair = _arrivals_by_pair(since, until=now)
    history_days = {
        moment.astimezone(ET).date() for moments in arrivals_by_pair.values() for moment in moments
    }
    test_days = held_out_days(history_days, holdout_modulus)

    # One profile per pair, trained on everything except the held-out days,
    # and the countdown corrections fitted the same way (#117) — so "ours"
    # below is the predictor production runs, learned from nothing it is
    # scored on.
    profiles = {
        pair: _learn(moments, exclude_dates=test_days)
        for pair, moments in arrivals_by_pair.items()
    }
    corrections = _fit_corrections(_shown_residuals(since, until=now, exclude_dates=test_days))
    # One read of every walked route's newest stop list: scoring asks
    # whether the route serves the stop for every query, and per-query
    # lookups were minutes of extra latency against production Postgres —
    # the same fix accuracy() already has (#17).
    served_by_route = current_stops_by_route()

    queries = no_prediction = not_served = 0
    history_queries = official_answered = 0
    ours_error = official_error = official_aged_error = history_error = 0.0
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
        poll_times = sorted(by_poll)

        # What the pair was being shown at each poll: its soonest countdown,
        # the number the official app displays until the next poll replaces
        # it. Both scorers of the live layer read from here — the official
        # without a freshness guard, ours with production's (#117).
        shown_times = defaultdict(list)
        shown_values = defaultdict(list)
        for polled_at in poll_times:
            for pair, etas in by_poll[polled_at].items():
                shown_times[pair].append(polled_at)
                shown_values[pair].append(min(etas))

        for polled_at in poll_times[::stride]:
            query_at = polled_at + QUERY_OFFSET
            for pair, etas in sorted(by_poll[polled_at].items()):
                # Pairs at stops the route no longer serves are skipped, so
                # the score describes what riders would actually be told (#57).
                if not _stop_is_served(*pair, current_by_route=served_by_route):
                    not_served += 1
                    continue
                moments = arrivals_by_pair.get(pair, ())
                index = bisect_left(moments, query_at)
                next_arrival = moments[index] if index < len(moments) else None
                if next_arrival is None or next_arrival > query_at + HORIZON:
                    continue  # no measurable arrival: neither side gets scored
                actual = (next_arrival - query_at).total_seconds() / 60
                ours = _fused_countdown(
                    pair[0],
                    _shown_fresh(shown_times.get(pair, []), shown_values.get(pair, []), query_at),
                    corrections,
                )
                last_arrival = _last_arrival_before(moments, query_at)
                if ours is None:
                    ours = _predict_history(profiles.get(pair), query_at, last_arrival)
                if ours is None:
                    no_prediction += 1
                    continue
                queries += 1
                ours_error += abs(ours - actual)
                # The history layer is also scored on its own, even where the
                # live layer answered: it is what runs when no poll is fresh.
                history = _predict_history(profiles.get(pair), query_at, last_arrival)
                if history is not None:
                    history_queries += 1
                    history_error += abs(history - actual)
                # The official's two scores. App-like: the freshest poll's
                # countdown at the query instant (#116). Aged: the sampled
                # poll's implied arrival, grown stale by the whole offset.
                official = _shown_countdown(
                    shown_times.get(pair, []), shown_values.get(pair, []), query_at
                )
                if official is not None:
                    official_answered += 1
                    official_error += abs(official - actual)
                official_implied = polled_at + dt.timedelta(minutes=min(etas))
                official_aged = max((official_implied - query_at).total_seconds() / 60, 0.0)
                official_aged_error += abs(official_aged - actual)

    return {
        "test_days": sorted(test_days),
        "history_days": len(history_days),
        "queries": queries,
        "no_prediction": no_prediction,
        "not_served": not_served,
        "ours_mae": round(ours_error / queries, 2) if queries else None,
        "history_mae": (
            round(history_error / history_queries, 2) if history_queries else None
        ),
        "official_mae": (
            round(official_error / official_answered, 2) if official_answered else None
        ),
        "official_aged_mae": round(official_aged_error / queries, 2) if queries else None,
    }


def held_out_days(days, modulus):
    """The days to hold out: their Eastern date's ordinal divides by modulus."""
    return {day for day in days if modulus and day.toordinal() % modulus == 0}


def _arrival_events(rows):
    """Rows [(polled_at, bus_id, eta_minutes)], time-ordered, for one stop:
    the moments buses reach it, each countdown run out to zero.

    The recorder polls every five minutes, so a poll rarely lands on the
    arrival itself — the countdown reaches eta 4 and the bus is gone by the
    next poll. A poll with eta at or below ARRIVAL_ETA is therefore the end
    of an approach, and the arrival is `polled_at + eta`: the countdown
    extrapolated the last few minutes. A bus dwelling at the stop spans
    several such polls; that's one arrival, at the first. Two buses
    reaching the stop together are one moment: the next bus is the one
    already there. The same bus's eta wrapping back up is its next lap
    approaching, and sightings farther apart than APPROACH_GAP are separate
    approaches; a countdown that merely stops — a bus taken off the feed —
    is no arrival at all.
    """
    state = {}  # bus_id -> (last_seen, arrived)
    arrivals = set()
    for polled_at, bus_id, eta in rows:
        last_seen, arrived = state.get(bus_id, (None, False))
        if last_seen is not None and polled_at - last_seen > APPROACH_GAP:
            arrived = False
        if eta <= ARRIVAL_ETA and not arrived:
            arrivals.add(polled_at + dt.timedelta(minutes=max(eta, 0)))
            arrived = True
        elif eta > ARRIVAL_ETA:
            arrived = False
        state[bus_id] = (polled_at, arrived)
    return sorted(arrivals)


def _arrivals_by_pair(since, until=None):
    """All arrivals in the window, grouped by (route_id, stop_id)."""
    queryset = Eta.objects.filter(eta_minutes__lte=ARRIVAL_ETA, polled_at__gte=since)
    if until:
        queryset = queryset.filter(polled_at__lt=until)
    rows = (
        queryset.order_by("polled_at", "route_id", "stop_id", "bus_id")
        .values_list("polled_at", "route_id", "stop_id", "bus_id", "eta_minutes")
    )
    grouped = defaultdict(list)
    for polled_at, route_id, stop_id, bus_id, eta in rows:
        grouped[(route_id, stop_id)].append((polled_at, bus_id, eta))
    return {pair: _arrival_events(moments) for pair, moments in grouped.items()}


def _learn(arrivals, exclude_dates=frozenset()):
    """The profile for one (route, stop): walk-up medians per Eastern-clock
    (day type, half-hour bucket), plus residual medians per elapsed band.

    Residuals learn the same gaps from the other side: minutes after a bus
    arrived, how long is still to wait for the next one. They are what the
    prediction conditions on when today's polls say a bus came recently.
    """
    by_date = defaultdict(list)
    for moment in arrivals:
        by_date[moment.astimezone(ET).date()].append(moment.astimezone(ET))

    waits = defaultdict(list)
    residuals = defaultdict(list)
    for day, moments in by_date.items():
        if day in exclude_dates:
            continue
        # Walk up at any minute t between two arrivals and the next bus is
        # the later one: from t in [a, b) the wait is b - t, sampled every
        # five minutes. Before the first arrival too (a long wait, but a
        # real one). After the day's last arrival it isn't knowable: skip.
        interval_start = dt.datetime.combine(day, dt.time(0), tzinfo=ET)
        previous_bus = None
        for next_arrival in moments:
            t = interval_start
            while t < next_arrival:
                waits[(_day_type(t), _bucket(t))].append(
                    (next_arrival - t).total_seconds() / 60
                )
                t += SAMPLE_STEP
            # The same gap, read as elapsed time: a rider who saw the bus at
            # `previous_bus` and is still there at previous_bus + l waits
            # next_arrival - (previous_bus + l). The walk up to the day's
            # first bus starts at midnight, which was no bus, so it teaches
            # residuals nothing. Each day type learns its own service
            # pattern: a Saturday bus every 20 minutes is not a weekday bus
            # every 12.
            if previous_bus is not None:
                gap = (next_arrival - previous_bus).total_seconds() / 60
                kind = _day_type(next_arrival)
                elapsed_max = min(int(gap), int(MAX_CONDITIONING.total_seconds() / 60))
                for elapsed in range(0, elapsed_max, 5):
                    residuals[(kind, elapsed)].append(gap - elapsed)
            previous_bus = next_arrival
            interval_start = next_arrival

    buckets = {key: statistics.median(values) for key, values in waits.items()}
    learned_residuals = {
        key: statistics.median(values)
        for key, values in residuals.items()
        if len(values) >= MIN_BAND_SAMPLES
    }
    return Profile(buckets=buckets, residuals=learned_residuals)


def _predict_history(profile, now, last_arrival):
    """The history layer's answer: conditioned on the last arrival when we
    saw one recently enough, else the plain walk-up median; None when the
    buckets have nothing either."""
    conditioned = _conditioned_wait(profile.residuals, now, last_arrival)
    if conditioned is not None:
        return conditioned
    return _predict(profile.buckets, now)


def _conditioned_wait(residuals, now, last_arrival):
    """The median minutes still to wait, given how long the stop has been
    without a bus, interpolated between the sampled elapsed points of
    today's day type.

    None when there is no last arrival, when it is too old to trust
    (MAX_CONDITIONING: buses we never saw may have come and gone since),
    or when today's day type has no residual data thin enough to reject
    (MIN_BAND_SAMPLES): then the plain buckets answer instead.
    """
    if last_arrival is None or not residuals:
        return None
    elapsed = (now - last_arrival).total_seconds() / 60
    if elapsed < 0 or elapsed > MAX_CONDITIONING.total_seconds() / 60:
        return None
    kind = _day_type(now.astimezone(ET))
    below = max((point for day, point in residuals if day == kind and point <= elapsed), default=None)
    if below is None:
        return None
    above = min((point for day, point in residuals if day == kind and point > elapsed), default=None)
    if above is None:
        return round(residuals[(kind, below)], 1)
    weight = (elapsed - below) / (above - below)
    value = residuals[(kind, below)] + weight * (residuals[(kind, above)] - residuals[(kind, below)])
    return round(value, 1)


def _predict(buckets, now):
    """The median walk-up wait for now's bucket, widened to the neighbouring
    buckets; None when even they have no data, so a route that isn't
    running right now (service hours ended, mostly) predicts nothing
    instead of borrowing a wait from a different time of day."""
    if not buckets:
        return None
    local = now.astimezone(ET)
    key = (_day_type(local), _bucket(local))
    for offsets in ([0], [0, -1, 1], [0, -1, 1, -2, 2]):
        values = [
            value
            for offset in offsets
            if (value := buckets.get((key[0], key[1] + offset))) is not None
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
