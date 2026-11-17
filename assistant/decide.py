"""Ask the LLM whether and when to notify a rider about a trip (issue #37).

`decide_notification(trip)` is the entry point. In Sprint 3 the agent (#28) will
feed it real trips (#26), our predictions (#23) and crowding (#12).

The model's answer is checked against a fixed structure. Anything that doesn't
fit, or any failure to reach the model, counts as "don't notify" and is logged:
a missing notification is better than a wrong one.
"""

import json
import logging
import zoneinfo
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings

from . import openrouter

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 160  # one short push notification


@dataclass(frozen=True)
class Trip:
    """One upcoming trip. All datetimes must be time-zone aware."""

    route: str
    stop: str
    needed_at: datetime  # when the rider needs to be at their destination
    walking_minutes: int  # walk to the stop
    official_arrival: datetime  # the Downtowner's ETA for the next bus
    predicted_arrival: datetime | None = None  # ours, or None when we have none


@dataclass(frozen=True)
class Decision:
    notify: bool
    notify_at: datetime | None = None  # aware, UTC; set only when notify is True
    message: str = ""


DONT_NOTIFY = Decision(notify=False)

def leave_by(trip):
    """When the rider has to leave for the stop to meet the bus: arithmetic, so it's done here
    and handed to the model, not left for it to work out. Our prediction wins when we have one."""
    arrival = trip.predicted_arrival or trip.official_arrival
    return arrival - timedelta(minutes=trip.walking_minutes)


REPLY_FORMAT = f"""\
Reply with only a JSON object with exactly these keys:
  "notify": true or false
  "notify_at": an ISO 8601 time with a UTC offset, e.g. "2026-11-16T08:40:00-05:00"
               (null when notify is false)
  "message": a short plain-text notification, at most {MAX_MESSAGE_LENGTH} characters
             (empty when notify is false)
"""

SYSTEM_PROMPT = f"""\
You decide whether and when to notify a Yale shuttle rider about an upcoming trip.

You get the route, the stop, the time the rider needs to be there, how many
minutes they walk to the stop, the official arrival time of the next bus, our own
predicted arrival time (it may be missing), and "Leave for the stop by": the bus's
arrival (ours when we have one, otherwise the official time) minus the walking time.
Our prediction is usually better than the official time when it exists.

You don't know how long the ride takes, so don't promise the rider will arrive on time.

Use your judgment. A notification is worth sending when the rider has little room
for error, when the bus is meaningfully later than the official time says, or when
the bus will arrive too late for them. Stay quiet when they have plenty of room.
When you notify, send it early enough to act on, always before "Leave for the stop by"
and never after it. The message names the bus, says when it arrives and when to leave.

{REPLY_FORMAT}"""


def _local(moment):
    """A datetime as Eastern time with its offset, for the model to read."""
    eastern = zoneinfo.ZoneInfo(settings.TIME_ZONE)
    return moment.astimezone(eastern).isoformat(timespec="minutes")


def build_messages(trip):
    predicted = _local(trip.predicted_arrival) if trip.predicted_arrival else "none (no prediction)"
    facts = (
        f"Route: {trip.route}\n"
        f"Stop: {trip.stop}\n"
        f"Rider needs to be there by: {_local(trip.needed_at)}\n"
        f"Walking time to the stop: {trip.walking_minutes} minutes\n"
        f"Official arrival of the next bus: {_local(trip.official_arrival)}\n"
        f"Our predicted arrival: {predicted}\n"
        f"Leave for the stop by: {_local(leave_by(trip))}\n"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": facts},
    ]


class InvalidAnswer(ValueError):
    pass


def parse_answer(text, trip):
    """Turn the model's reply into a Decision, or raise InvalidAnswer."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as error:
        raise InvalidAnswer("not JSON") from error
    if not isinstance(data, dict):
        raise InvalidAnswer("not a JSON object")
    if set(data) != {"notify", "notify_at", "message"}:
        raise InvalidAnswer(f"keys are {sorted(data)}")

    notify, notify_at, message = data["notify"], data["notify_at"], data["message"]
    if not isinstance(notify, bool):
        raise InvalidAnswer("notify is not true or false")
    if not notify:
        return DONT_NOTIFY

    if not isinstance(message, str) or not message.strip():
        raise InvalidAnswer("message is empty")
    if len(message) > MAX_MESSAGE_LENGTH:
        raise InvalidAnswer("message is too long")
    if not isinstance(notify_at, str):
        raise InvalidAnswer("notify_at is not a time")
    try:
        when = datetime.fromisoformat(notify_at)
    except ValueError as error:
        raise InvalidAnswer("notify_at is not an ISO 8601 time") from error
    if when.tzinfo is None:
        raise InvalidAnswer("notify_at has no UTC offset")
    if when > trip.needed_at:
        raise InvalidAnswer("notify_at is after the rider needs to be there")
    return Decision(notify=True, notify_at=when.astimezone(zoneinfo.ZoneInfo("UTC")), message=message.strip())


def decide_notification(trip, *, strict=False):
    """Ask the LLM about one trip; returns a Decision.

    LLM trouble never raises: it counts as "don't notify". With strict=True, a
    failure to reach OpenRouter raises OpenRouterError instead, so the by-hand
    evaluation can't mistake an outage for a decision.
    """
    try:
        reply = openrouter.chat(build_messages(trip))
    except openrouter.OpenRouterError as error:
        if strict:
            raise
        logger.warning("Not notifying: %s", error)
        return DONT_NOTIFY
    try:
        return parse_answer(reply, trip)
    except InvalidAnswer as error:
        logger.warning("Not notifying: the LLM's answer didn't fit (%s): %.200r", error, reply)
        return DONT_NOTIFY
