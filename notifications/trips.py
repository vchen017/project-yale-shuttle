"""Telling riders when to leave for their upcoming events (#29): the job half
of our AI feature (#11).

Once an hour the notify_trips job, which the job clock starts on time, calls
`watch()`. It loads the trips in the next WATCH: each event on a rider's
calendar, planned from their Home the way the when-to-leave page plans it
(#26), for riders with push notifications on, with what was already sent
about each. That is its one routine visit to the database. With nothing
coming up, the run ends right there.

Otherwise it checks every trip each CHECK_EVERY against the live feed, in
memory. The time to leave comes from our prediction, the one the when-to-leave
page plans with (#153): the feed's countdown, corrected by what the recorded
history says about countdowns that size (#117), or our history prediction
when the feed has none, since a countdown is near-perfect once a bus is close
(the Nov 17 audit) and history only knows the usual wait for the time of day.
The corrections and the history (history.HistoryPredictor) are loaded with
the run. Once the time to leave is within DECIDE_AHEAD, the agent (#28)
decides whether and when to notify, reading the live countdown, that
prediction and the crowding read at the start. A notification
goes out at the time the agent picked. It's recorded first, under a unique
constraint (TripNotification), so overlapping runs or a rerun never send it
twice. When no device receives it, the record is dropped again, so the next
check can retry. A trip gets one notification, and another only when its time
to leave moves by RENOTIFY_SHIFT or more. Recording what it sends is the only
other time the watch touches the database.

The watch keeps going for up to WATCH, so a run that starts late, or never,
leaves no gap. The next hourly run takes over from it: jobs.yml cancels the
older run, and the job runner lets the new one start (takes_over).

A rider who connects a calendar mid-hour doesn't wait for the next run:
`first_check_soon(user)` checks their trips right away, in a thread of its
own, so the page never waits for the agent or the push service.
"""

import datetime as dt
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, connection, connections, transaction
from django.urls import reverse
from django.utils import timezone

from assistant.agent import decide_with_tools
from assistant.decide import Decision
from assistant.tools import DataSource, TripRequest
from calendars import plans
from calendars.models import Event
from crowding.services import crowding
from history.predictions import Corrections, HistoryPredictor, stored_corrections
from jobs import clock as job_clock
from places.models import Place
from transit import downtowner
from transit.downtowner import FeedUnavailable
from transit.network import network

from . import push
from .models import PushSubscription, TripNotification

logger = logging.getLogger(__name__)

SCHEDULE = "45 * * * *"  # the notify_trips job: hourly, 4 minutes after the calendar refresh
WATCH = dt.timedelta(hours=2)  # how far ahead a run loads trips, and the longest it watches
CHECK_EVERY = dt.timedelta(minutes=5)
DECIDE_AHEAD = dt.timedelta(minutes=30)  # ask the agent once the time to leave is this close
REDECIDE_SHIFT = dt.timedelta(minutes=2)  # a waiting "yes" is asked again once leave-by moves this much
RENOTIFY_SHIFT = dt.timedelta(minutes=5)  # another notification needs leave-by to move this much (#11)


@dataclass
class Trip:
    """One rider's upcoming event, planned once per run."""

    user_id: int
    calendar_id: int
    uid: str
    start: dt.datetime  # when the rider needs to be there
    title: str
    stop_id: int  # where they board
    stop_name: str
    routes: dict  # route id -> name: the routes from that stop to the event's
    walk_to_stop: int  # minutes
    subscriptions: list  # the rider's PushSubscriptions
    sent: list = field(default_factory=list)  # [leave_by] of each notification sent, in order
    decision: Decision | None = None  # the agent's latest "yes", waiting for its time
    decided_on: dt.datetime | None = None  # the leave-by that decision was made on

    @property
    def key(self):
        return (self.calendar_id, self.uid, self.start)


class WatchData(DataSource):
    """What the agent's tools read during a watch, never the database: the
    live feed's countdown (cached 30 seconds), the countdown corrections and
    our history prediction, both loaded with the run, and the crowding read
    when it started. `now` is set before each check. Without corrections
    nothing is corrected, as on a fresh database."""

    def __init__(self, history, crowding_by_route, now, corrections=None):
        self.history, self.crowding_by_route, self.now = history, crowding_by_route, now
        self.corrections = corrections or Corrections(by_route={}, by_bucket={}, overall=0.0)

    def _countdowns(self, trip):
        """The feed's countdowns to the route's buses at the stop, soonest first."""
        return sorted(
            arrival["minutes"]
            for arrival in downtowner.etas(trip.stop_id)
            if arrival["route_id"] == trip.route_id and arrival["minutes"] >= 0
        )

    def official_minutes(self, trip):
        """The feed's countdown to the route's soonest bus the rider can still
        walk to the stop for. A bus that comes sooner is skipped, as on the
        trip page (calendars.plans.catchable_bus). None when the feed lists no
        such bus."""
        times = [minutes for minutes in self._countdowns(trip) if minutes >= trip.walking_minutes]
        return times[0] if times else None

    def predicted_minutes(self, trip):
        """Our prediction, the one the when-to-leave page plans with (#153):
        the feed's countdown to the route's soonest bus, corrected (#117), or
        our history's when the feed lists no bus or is down, in whole minutes
        as the page shows it. When the rider can't walk to the stop in time
        for that bus, the next bus in the feed they can make, as on the page
        (calendars.plans.catchable_minutes). None when there's no such bus."""
        try:
            countdowns = self._countdowns(trip)
        except FeedUnavailable:
            countdowns = []
        if countdowns:
            ours = self.corrections.correct(trip.route_id, countdowns[0])
        else:
            ours = self.history.minutes(trip.route_id, trip.stop_id, self.now)
        return plans.catchable_minutes(None if ours is None else round(ours), countdowns, trip.walking_minutes)

    def crowding(self, trip):
        return self.crowding_by_route.get(trip.route_id)


def load_trips(now, users=None):
    """The trips starting within WATCH of `now`, for riders with push
    notifications on (all of them, or just `users`) and a Home, planned the
    way the when-to-leave page plans them. With the feed down there's no plan
    to make, so no trips: the next run tries again."""
    subscriptions = PushSubscription.objects.all()
    if users is not None:
        subscriptions = subscriptions.filter(user__in=users)
    by_user = defaultdict(list)
    for subscription in subscriptions:
        by_user[subscription.user_id].append(subscription)
    if not by_user:
        return []
    events = list(
        Event.objects.filter(calendar__user_id__in=by_user, start__gt=now, start__lte=now + WATCH)
        .select_related("calendar")
        .order_by("start")
    )
    homes = {place.user_id: place for place in Place.objects.filter(user_id__in=by_user, is_home=True)}
    events = [event for event in events if event.calendar.user_id in homes]
    if not events:
        return []
    try:
        net = network()
    except FeedUnavailable:
        logger.warning("The feed is down: no trips to plan")
        return []
    stops, routes_by_id = net["stops"], {route["id"]: route for route in net["routes"]}

    sent = defaultdict(list)
    for record in TripNotification.objects.filter(
        calendar__in={event.calendar_id for event in events}, event_start__gt=now
    ).order_by("sequence"):
        sent[(record.calendar_id, record.event_uid, record.event_start)].append(record.leave_by)

    trips = []
    for event in events:
        user_id = event.calendar.user_id
        trip = plan_trip(
            event, homes[user_id], now=now, stops=stops, routes_by_id=routes_by_id,
            subscriptions=by_user[user_id], sent=sent[(event.calendar_id, event.uid, event.start)],
        )
        if trip is not None:
            trips.append(trip)
    return trips


def plan_trip(event, home, *, now, stops, routes_by_id, subscriptions=None, sent=None):
    """One event as a Trip, planned from Home the way the when-to-leave page
    plans it (#26). None when there's no bus to catch: the location is
    unknown, no route links the two, or walking is quicker. The trip watch
    passes the rider's devices and what was sent; asking the agent from the
    when-to-leave page (#148) passes neither."""
    plan = plans.plan_event(
        event, home, now=now, stops=stops, routes_by_id=routes_by_id, running_at=plans.routes_running_at,
    )
    ride = plan.plan
    if ride is None or ride.walking_is_quicker:
        return None
    routes = {
        route_id: route["name"] for route_id, route in routes_by_id.items() if route["name"] in plan.route_names
    }
    if not routes:
        return None
    return Trip(
        user_id=event.calendar.user_id, calendar_id=event.calendar_id, uid=event.uid, start=event.start,
        title=event.title, stop_id=ride.board_at["id"], stop_name=ride.board_at["name"],
        routes=routes, walk_to_stop=ride.walk_to_stop,
        subscriptions=subscriptions if subscriptions is not None else [],
        sent=sent if sent is not None else [],
    )


def watch_data(trips, now):
    """The WatchData for these trips: the history, the countdown corrections
    and the crowding they need, read now, with the trips."""
    pairs = {(route_id, trip.stop_id) for trip in trips for route_id in trip.routes}
    routes = {route_id: name for trip in trips for route_id, name in trip.routes.items()}
    answers = crowding([
        {"id": route_id, "name": name, "label": name, "color": "00356b"} for route_id, name in routes.items()
    ], now=now)
    crowding_by_route = {
        route_id: {
            "current": answer.current_label,
            "typical_now": answer.typical_now.label if answer.typical_now else None,
        }
        for route_id, answer in zip(routes, answers)
    }
    return WatchData(HistoryPredictor(pairs, now=now), crowding_by_route, now, stored_corrections())


def check(trips, now, data, decide=None):
    """One pass over the trips at `now`: ask the agent about each trip whose
    time to leave is close and whose facts changed, and keep its "yes" for
    when it's due. Touches no database. Returns how many decisions it asked for."""
    decide = decide or decide_with_tools
    data.now = now
    asked = 0
    for trip in trips:
        if now >= trip.start or not trip.subscriptions:
            trip.decision = None
            continue  # it's started, or the rider has no device left to tell
        request, leave_by = _request(trip, data)
        if request is None:
            continue
        if trip.sent and abs(leave_by - trip.sent[-1]) < RENOTIFY_SHIFT:
            trip.decision = None  # they were told, and nothing moved enough to tell them again
            continue
        if leave_by - now > DECIDE_AHEAD:
            continue  # too early: the agent decides once the time to leave is near
        if trip.decision and abs(leave_by - trip.decided_on) < REDECIDE_SHIFT:
            continue  # its "yes" still stands
        decision = decide(request, data, now=now)
        asked += 1
        if decision.notify:
            trip.decision, trip.decided_on = decision, leave_by
        else:
            trip.decision = None
    return asked


def _request(trip, data):
    """The agent's question about the trip now, and its time to leave: for the
    linking route whose next bus the rider can still catch soonest. Each
    route's bus is timed by our prediction, the one the when-to-leave page
    plans with (#153): the feed's countdown, corrected, or our history
    prediction when the feed has none. (None, None) when no route has a time."""
    best = None
    for route_id, name in sorted(trip.routes.items()):
        request = TripRequest(
            route=name, stop=trip.stop_name, needed_at=trip.start,
            walking_minutes=trip.walk_to_stop, route_id=route_id, stop_id=trip.stop_id,
        )
        minutes = data.predicted_minutes(request)
        if minutes is None:
            continue
        catchable = minutes >= trip.walk_to_stop
        rank = (not catchable, minutes)  # a bus they can make first, then the soonest
        if best is None or rank < best[0]:
            best = (rank, request, minutes)
    if best is None:
        return None, None
    _, request, minutes = best
    return request, data.now + dt.timedelta(minutes=minutes - trip.walk_to_stop)


def send_due(trips, now, reconnect=False):
    """Send each waiting "yes" whose time has come. Returns how many went out.
    `reconnect` starts each send on a fresh database connection: the watch
    asks for it, since an hour's quiet can leave its connection dead."""
    sent = 0
    for trip in trips:
        decision = trip.decision
        if decision is None or decision.notify_at > now:
            continue
        trip.decision = None
        if now < trip.start and _send(trip, decision, trip.decided_on, now, reconnect):
            sent += 1
    return sent


def _send(trip, decision, leave_by, now, reconnect=False):
    """Record the notification, then push it to the rider's devices. False when
    another run already sent this one, or when no device received it: then the
    record is dropped, so a later check can try again."""
    sequence = len(trip.sent)
    if reconnect and not connection.in_atomic_block:
        connection.close()  # Django opens a new one for the next query
    try:
        with transaction.atomic():
            record = TripNotification.objects.create(
                user_id=trip.user_id, calendar_id=trip.calendar_id, event_uid=trip.uid,
                event_start=trip.start, event_title=trip.title[:200], sequence=sequence,
                leave_by=leave_by, message=decision.message[:200], sent_at=now,
            )
    except IntegrityError:
        # Another run sent it first: catch up with what it sent.
        trip.sent = list(
            TripNotification.objects.filter(
                calendar_id=trip.calendar_id, event_uid=trip.uid, event_start=trip.start
            ).order_by("sequence").values_list("leave_by", flat=True)
        )
        return False
    trip.sent.append(leave_by)
    delivered = 0
    for subscription in list(trip.subscriptions):
        try:
            if push.send(
                subscription, trip.title[:60] or "Your next trip", decision.message,
                url=reverse("when-to-leave"), tag=f"trip-{trip.calendar_id}-{trip.uid}-{trip.start:%Y%m%d%H%M}",
                ttl=max(60, int((trip.start - now).total_seconds())),
            ):
                delivered += 1
            else:
                trip.subscriptions.remove(subscription)  # gone: push.send deleted it
        except push.PushError:
            logger.exception("Couldn't send the notification about %s to %s", trip.title, subscription)
    if not delivered:
        record.delete()
        trip.sent.pop()
        return False
    record.devices = delivered
    record.save(update_fields=["devices"])
    return True


def watch(clock=timezone.now, sleep=time.sleep, decide=None):
    """The notify_trips job: load the trips, then check them every CHECK_EVERY
    and send what's due, for up to WATCH or until the next run takes over.
    Returns a summary for the run log."""
    started = clock()
    trips = load_trips(started)
    if not trips:
        return f"No trips in the next {_hours(WATCH)} with notifications on"
    missing = [
        name for name in ("OPENROUTER_API_KEY", "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY")
        if not getattr(settings, name)
    ]
    if missing:
        # Fail where admins look, rather than watch and tell nobody.
        raise ImproperlyConfigured(
            f"{len(trips)} trip(s) coming up, but this job can't notify anyone without "
            f"{', '.join(missing)}: add them to this site's GitHub environment "
            "(Settings → Environments), with the same values as on Render."
        )
    data = watch_data(trips, started)
    checks = asked = sent = 0
    next_check = started
    handed_over = False
    try:
        while True:
            now = clock()
            if now - started >= WATCH or all(now >= trip.start for trip in trips):
                break
            if now >= next_check:
                asked += check(trips, now, data, decide)
                checks += 1
                next_check = now + CHECK_EVERY
            sent += send_due(trips, now, reconnect=True)
            wake = min([next_check, *(trip.decision.notify_at for trip in trips if trip.decision)])
            sleep(max((wake - clock()).total_seconds(), 0))
    except KeyboardInterrupt:  # the next run took over, and the workflow cancelled this one
        handed_over = True
    summary = (
        f"Watched {len(trips)} trip(s) for {round((clock() - started).total_seconds() / 60)} minutes: "
        f"{checks} checks, {asked} decisions, {sent} notification(s) sent"
    )
    return summary + (", then handed over to the next run" if handed_over else "")


def first_check_soon(user):
    """first_check(user) once this request's changes are committed, in a thread
    of its own, so the page doesn't wait for it: it may ask the agent and push
    to devices, which can take longer than the 30 seconds Render gives a
    request (one gunicorn worker, at its default timeout). Best effort: if the
    server stops first, the hourly watch still checks those trips."""
    transaction.on_commit(lambda: _in_background(_first_check_and_close, user))


def _in_background(target, *args):
    threading.Thread(target=target, args=args, daemon=True).start()


def _first_check_and_close(user):
    try:
        first_check(user)
    finally:
        connections.close_all()  # the connection this thread opened


def first_check(user, now=None, decide=None):
    """Check a rider's trips in the next WATCH right away: for a calendar just
    connected, whose trips the hourly watch hasn't loaded yet (#29). A
    notification the agent wants before the next hourly run starts goes out
    now, a little early rather than late. Returns how many went out. Never
    raises: a failure here mustn't break the page, and the next run checks
    again."""
    now = now or timezone.now()
    next_run = next_run_at(now)
    try:
        trips = load_trips(now, users=[user])
        if not trips:
            return 0
        check(trips, now, watch_data(trips, now), decide)
        for trip in trips:
            if trip.decision and trip.decision.notify_at <= next_run:
                trip.decision = Decision(notify=True, notify_at=now, message=trip.decision.message)
        return send_due(trips, now)
    except Exception:
        logger.exception("The first check of %s's trips failed", user)
        return 0


def next_run_at(now):
    """When the next hourly watch starts, by its cron line (UTC)."""
    minute = now.astimezone(dt.timezone.utc).replace(second=0, microsecond=0)
    for _ in range(24 * 60):
        minute += dt.timedelta(minutes=1)
        if job_clock.matches(SCHEDULE, minute):
            return minute
    return now + dt.timedelta(hours=1)


def _hours(span):
    hours = span.total_seconds() / 3600
    return f"{hours:g} hour" + ("" if hours == 1 else "s")
