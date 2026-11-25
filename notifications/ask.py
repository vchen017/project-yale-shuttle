"""Asking the trip agent about one upcoming trip, from the when-to-leave page
(#148): the part of our AI feature (#11) a rider sees without push
notifications, and graders can try on staging at any hour.

The agent (#28) gets the question the trip watch (#29) asks it, about the
same trip, planned the same way, with the live countdown, our prediction and
crowding, only on demand, and for a rider with no device too. The page shows
whether it would notify them now, when, what it would say, and what it looked
up to decide. Like the watch, it's only asked about trips in the next two
hours (AHEAD): its tools read the next buses, which say nothing about a trip
tomorrow. The demo office hours (calendars/demo.py) are always that close.

One decision can take up to 90 seconds (assistant.agent.TOTAL_SECONDS), past
the 30 seconds a request gets, so the request only saves the question as an
AgentAnswer and starts the agent in a thread of its own once that commits, as
trips.first_check_soon does. The page polls for the answer. Asking again
within AgentAnswer.REUSE shows the saved answer instead of calling the LLM
again.
"""

import datetime as dt
import functools
import logging

from django.conf import settings
from django.db import connections, transaction
from django.utils import dateformat, timezone

from assistant import openrouter
from assistant.agent import decide_with_tools
from assistant.decide import InvalidAnswer
from assistant.tools import DataSource
from calendars.models import Event
from places.models import Place
from transit.downtowner import FeedUnavailable
from transit.network import network

from . import trips
from .models import AgentAnswer

logger = logging.getLogger(__name__)

NOT_SET_UP = "this site isn't set up to reach it yet (it has no OpenRouter key)"
# The agent reads the next buses, so it's asked only about trips this close,
# as the trip watch asks it: the next bus says nothing about tomorrow's.
AHEAD = trips.WATCH


class Unanswerable(Exception):
    """There's nothing to ask the agent about: why, in words for the rider."""


class Looked(DataSource):
    """The trip watch's data for one trip, noting what the agent's tools read
    from it, so the page can show what the agent looked up."""

    def __init__(self, source):
        self.source, self.seen = source, {}

    @property
    def now(self):
        return self.source.now

    def official_minutes(self, trip):
        try:
            minutes = self.source.official_minutes(trip)
        except FeedUnavailable:
            self.seen["official"] = "down"
            raise
        self.seen["official"] = minutes
        return minutes

    def predicted_minutes(self, trip):
        self.seen["predicted"] = minutes = self.source.predicted_minutes(trip)
        return minutes

    def spare_minutes(self, trip):
        return self.source.spare_minutes(trip)

    def crowding(self, trip):
        self.seen["crowding"] = answer = self.source.crowding(trip)
        return answer


def key(event):
    """An event the way an AgentAnswer names it, which outlasts the hourly
    calendar refresh."""
    return (event.calendar_id, event.uid, event.start)


def has_a_bus(plan):
    """Whether a when-to-leave plan is one the agent can be asked about, once
    it's close enough: a bus to catch from Home rather than a walk, as the
    trip watch would watch."""
    return plan.plan is not None and not plan.plan.walking_is_quicker and bool(plan.route_names)


def ask_from(event, now):
    """When the agent can first be asked about this event: None once it can."""
    return event.start - AHEAD if event.start - now > AHEAD else None


def answers_for(user, events):
    """The rider's answer about each of these events that has one, by key(event)."""
    events = list(events)
    if not events:
        return {}
    wanted = {key(event) for event in events}
    found = AgentAnswer.objects.filter(
        user=user,
        calendar_id__in={event.calendar_id for event in events},
        event_start__in={event.start for event in events},
    )
    return {
        (answer.calendar_id, answer.event_uid, answer.event_start): answer
        for answer in found
        if (answer.calendar_id, answer.event_uid, answer.event_start) in wanted
    }


def start(user, event, now=None):
    """Ask the agent about one of the rider's events, or keep the answer it
    already has: one still on its way, or one given within REUSE. Returns the
    AgentAnswer, which the agent fills in from a thread of its own once this
    request's changes commit."""
    now = now or timezone.now()
    with transaction.atomic():
        answer, created = AgentAnswer.objects.select_for_update().get_or_create(
            calendar_id=event.calendar_id, event_uid=event.uid, event_start=event.start,
            defaults={"user": user, "asked_at": now},
        )
        if not created and answer.still_current(now):
            return answer
        answer.status, answer.asked_at, answer.answered_at = AgentAnswer.ASKING, now, None
        answer.notify, answer.notify_at, answer.message, answer.looked_up, answer.reason = None, None, "", {}, ""
        if not settings.OPENROUTER_API_KEY:
            # Say so at once, rather than start a thread that can only fail.
            answer.status, answer.answered_at, answer.reason = AgentAnswer.FAILED, now, NOT_SET_UP
        answer.save()
    if answer.status == AgentAnswer.ASKING:
        transaction.on_commit(lambda: trips._in_background(_answer_and_close, answer.pk))
    return answer


def _answer_and_close(pk):
    try:
        answer(pk)
    finally:
        connections.close_all()  # the connection this thread opened


def answer(pk, now=None, decide=None):
    """Ask the agent about a saved question and save what it says, or why it
    couldn't say. Never raises: a failure here only ever shows on the page."""
    now = now or timezone.now()
    decide = decide or functools.partial(decide_with_tools, strict=True, report_invalid=True)
    record = AgentAnswer.objects.filter(pk=pk).first()
    if record is None:
        return None  # the rider disconnected their calendar while the agent thought
    try:
        decision, request, seen = _ask(record, now, decide)
    except Unanswerable as why:
        return _failed(record, now, str(why))
    except openrouter.OpenRouterError as error:
        logger.warning("The agent couldn't answer about %s: %s", record, error)
        return _failed(record, now, "the AI service didn't answer")
    except InvalidAnswer:
        return _failed(record, now, "its answer didn't make sense")  # the agent logged it
    except Exception:
        logger.exception("Asking the agent about %s failed", record)
        return _failed(record, now, "something went wrong")
    return _finish(
        record, status=AgentAnswer.ANSWERED, answered_at=now,  # the moment the agent was told it is
        notify=decision.notify, notify_at=decision.notify_at, message=decision.message[:200],
        looked_up=_looked_up(request, seen, now),
    )


def _ask(record, now, decide):
    """The agent's Decision about the record's event, the question it was
    asked, and what it looked up. Raises Unanswerable when there's nothing to
    ask it about."""
    event = (
        Event.objects.filter(calendar_id=record.calendar_id, uid=record.event_uid, start=record.event_start)
        .select_related("calendar")
        .first()
    )
    if event is None:
        raise Unanswerable("the event isn't on your calendar anymore")
    if event.start <= now:
        raise Unanswerable("the event has started")
    if ask_from(event, now):
        when = dateformat.format(timezone.localtime(ask_from(event, now)), "g:i A")
        raise Unanswerable(f"it's more than {trips._hours(AHEAD)} away, and the agent looks at the next buses: ask from {when}")
    home = Place.objects.filter(user_id=record.user_id, is_home=True).first()
    if home is None:
        raise Unanswerable("there's no Home to plan the trip from")
    try:
        net = network()
    except FeedUnavailable:
        raise Unanswerable("live shuttle times are unavailable right now") from None
    trip = trips.plan_trip(
        event, home, now=now, stops=net["stops"], routes_by_id={route["id"]: route for route in net["routes"]},
    )
    if trip is None:
        raise Unanswerable("there's no bus to catch for this trip right now")
    data = Looked(trips.watch_data([trip], now))
    request, _ = trips._request(trip, data)
    if request is None:
        raise Unanswerable("there's no bus time to go by: the feed lists no bus and we have no prediction")
    data.seen.clear()  # picking the route read the times too; keep only what the agent looks up
    return decide(request, data, now=now), request, data.seen


def _looked_up(request, seen, now):
    """What the agent's tools read, for the page: times as ISO text, so the
    JSON field can hold them."""
    looked = {"route": request.route, "stop": request.stop}
    for name in ("official", "predicted"):
        if name in seen:
            minutes = seen[name]
            if minutes not in (None, "down"):
                # To the minute, the way the agent's tools told the model.
                minutes = (now + dt.timedelta(minutes=minutes)).replace(second=0, microsecond=0).isoformat()
            looked[name] = minutes
    if "crowding" in seen:
        looked["crowding"] = seen["crowding"]
    return looked


def _failed(record, now, reason):
    return _finish(record, status=AgentAnswer.FAILED, answered_at=now, reason=reason[:200])


def _finish(record, **fields):
    """Save the agent's answer onto the question it answers, if it's still the
    question: a rider who asked again since (after GIVE_UP) has a newer one,
    and a disconnected calendar has none."""
    AgentAnswer.objects.filter(pk=record.pk, asked_at=record.asked_at).update(**fields)
    for name, value in fields.items():
        setattr(record, name, value)
    return record
