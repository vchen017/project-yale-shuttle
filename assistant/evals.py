"""The evaluation set for the notification decision (issue #37).

Each case is an invented trip, what we expect, and a `mock_reply`: the answer a
good model would give. CI feeds the mock replies through `decide_notification`
with OpenRouter mocked, which checks the prompt plumbing, the validation and our
scoring. `manage.py evaluate_assistant` runs the same cases against the real model.

What's graded is deliberately modest. Two things must hold for every case: the
answer is valid, and any notification comes before the rider has to leave for the
stop. Whether to notify is graded only where the answer is clear:
  - notify=True: the bus arrives too late, or leaves the rider almost no room;
  - notify=False: the rider has plenty of room (an hour or more);
  - notify=None: a judgment call, so either answer is fine.
We don't grade the message wording. Each case has a `kind` (on time, late, tight,
no prediction, feed down), so the set provably covers every situation #39 asks for. The "judgment call" cases keep the set from
rewarding a model for matching our own rule of thumb.
"""

from datetime import datetime, timedelta, timezone

from .agent import decide_with_tools
from .decide import Trip, decide_notification, leave_by
from .tools import DataSource, TripRequest

EASTERN = timezone(timedelta(hours=-5))  # EST, the offset on Nov 16, 2026


def at(hour, minute):
    return datetime(2026, 11, 16, hour, minute, tzinfo=EASTERN)


KINDS = ("on time", "late", "tight", "no prediction", "feed down")


def case(name, kind, trip, expect, mock_reply):
    assert kind in KINDS, kind
    return {"name": name, "kind": kind, "trip": trip, "expect": expect, "mock_reply": mock_reply}


QUIET = '{"notify": false, "notify_at": null, "message": ""}'


def notify(when, message):
    """A mock reply that notifies at `when` (an ISO time) with `message`."""
    return f'{{"notify": true, "notify_at": "{when}", "message": "{message}"}}'


CASES = [
    case(
        "an hour and a half to spare: stay quiet",
        "on time",
        Trip("Blue", "Grove Street", needed_at=at(9, 0), walking_minutes=5,
             official_arrival=at(7, 30), predicted_arrival=at(7, 31)),
        expect=False,
        mock_reply='{"notify": false, "notify_at": null, "message": ""}',
    ),
    case(
        "bus ten minutes before the deadline: notify",
        "tight",
        Trip("Blue", "Grove Street", needed_at=at(9, 0), walking_minutes=10,
             official_arrival=at(8, 45), predicted_arrival=at(8, 50)),
        expect=True,
        mock_reply='{"notify": true, "notify_at": "2026-11-16T08:30:00-05:00", '
                   '"message": "Blue reaches Grove St at 8:50. Leave by 8:40."}',
    ),
    case(
        "the bus will arrive after the rider's deadline: notify",
        "late",
        Trip("Gold", "Science Hill", needed_at=at(9, 0), walking_minutes=10,
             official_arrival=at(8, 50), predicted_arrival=at(9, 5)),
        expect=True,
        mock_reply='{"notify": true, "notify_at": "2026-11-16T13:45:00+00:00", '
                   '"message": "Gold is running late: due 9:05 at Science Hill. You may miss 9:00."}',
    ),
    case(
        "our prediction says 15 minutes late, little room: notify",
        "late",
        Trip("Gold", "Science Hill", needed_at=at(9, 10), walking_minutes=5,
             official_arrival=at(8, 40), predicted_arrival=at(8, 55)),
        expect=True,
        mock_reply='{"notify": true, "notify_at": "2026-11-16T08:42:00-05:00", '
                   '"message": "Gold is running ~15 min late. Leave by 8:50 instead."}',
    ),
    case(
        "judgment call: no prediction, official time only, 25 minutes to spare",
        "no prediction",
        Trip("Orange", "Broadway", needed_at=at(8, 45), walking_minutes=8,
             official_arrival=at(8, 20), predicted_arrival=None),
        expect=None,
        mock_reply='{"notify": true, "notify_at": "2026-11-16T08:05:00-05:00", '
                   '"message": "Orange is due at 8:20 (official time). Leave by 8:12."}',
    ),
    case(
        "judgment call: a bus 48 minutes early with a short walk",
        "on time",
        Trip("Green", "Hillhouse", needed_at=at(9, 0), walking_minutes=3,
             official_arrival=at(8, 10), predicted_arrival=at(8, 12)),
        expect=None,
        mock_reply='{"notify": false, "notify_at": null, "message": ""}',
    ),
    case(
        "judgment call: a long walk, 18 minutes to spare",
        "tight",
        Trip("Blue", "Prospect Street", needed_at=at(10, 0), walking_minutes=20,
             official_arrival=at(9, 40), predicted_arrival=at(9, 42)),
        expect=None,
        mock_reply='{"notify": true, "notify_at": "2026-11-16T09:12:00-05:00", '
                   '"message": "20 min walk to Prospect St: leave by 9:22 for the 9:42 Blue."}',
    ),
    # ---- on time ----
    case(
        "on time, two hours to spare: stay quiet",
        "on time",
        Trip("Orange", "Broadway", needed_at=at(11, 0), walking_minutes=6,
             official_arrival=at(9, 0), predicted_arrival=at(9, 1)),
        expect=False,
        mock_reply=QUIET,
    ),
    case(
        "on time, an hour and ten minutes to spare, no prediction: stay quiet",
        "no prediction",
        Trip("Blue", "Grove Street", needed_at=at(10, 0), walking_minutes=5,
             official_arrival=at(8, 50), predicted_arrival=None),
        expect=False,
        mock_reply=QUIET,
    ),
    case(
        "the bus is early and there are 16 minutes of room: either answer",
        "on time",
        Trip("Gold", "Science Hill", needed_at=at(9, 0), walking_minutes=6,
             official_arrival=at(8, 50), predicted_arrival=at(8, 44)),
        expect=None,
        mock_reply=QUIET,
    ),
    # ---- late ----
    case(
        "our prediction is 20 minutes later than the official time, past the deadline: notify",
        "late",
        Trip("Red", "Hillhouse", needed_at=at(9, 0), walking_minutes=5,
             official_arrival=at(8, 40), predicted_arrival=at(9, 0)),
        expect=True,
        mock_reply=notify("2026-11-16T08:20:00-05:00", "Red looks 20 min late: due 9:00. You may miss your 9:00."),
    ),
    case(
        "the official time looks fine but we predict 12 minutes later, 5 minutes of room: notify",
        "late",
        Trip("Green", "Elm Street", needed_at=at(9, 20), walking_minutes=4,
             official_arrival=at(8, 58), predicted_arrival=at(9, 10)),
        expect=True,
        mock_reply=notify("2026-11-16T08:50:00-05:00", "Green is running late: due 9:10. Leave by 9:06."),
    ),
    case(
        "the bus comes half an hour after the rider's deadline: notify",
        "late",
        Trip("Blue", "Prospect Street", needed_at=at(9, 0), walking_minutes=8,
             official_arrival=at(9, 30), predicted_arrival=at(9, 32)),
        expect=True,
        mock_reply=notify("2026-11-16T08:00:00-05:00", "Blue won't reach Prospect St until 9:32, after your 9:00."),
    ),
    case(
        "a long walk and a late bus: notify",
        "late",
        Trip("Purple", "Whitney Avenue", needed_at=at(9, 10), walking_minutes=25,
             official_arrival=at(8, 50), predicted_arrival=at(8, 58)),
        expect=True,
        mock_reply=notify("2026-11-16T08:15:00-05:00", "Purple is due 8:58. It's a 25 min walk: leave by 8:33."),
    ),
    # ---- tight ----
    case(
        "five minutes between the bus and the deadline: notify",
        "tight",
        Trip("Orange", "Broadway", needed_at=at(9, 0), walking_minutes=2,
             official_arrival=at(8, 55), predicted_arrival=at(8, 55)),
        expect=True,
        mock_reply=notify("2026-11-16T08:30:00-05:00", "Orange reaches Broadway at 8:55. Leave by 8:53."),
    ),
    case(
        "the rider is already at the stop and the bus is two minutes before the deadline: notify",
        "tight",
        Trip("Blue", "Grove Street", needed_at=at(9, 0), walking_minutes=0,
             official_arrival=at(8, 58), predicted_arrival=at(8, 58)),
        expect=True,
        mock_reply=notify("2026-11-16T08:40:00-05:00", "Blue reaches Grove St at 8:58. Be at the stop by then."),
    ),
    case(
        "our prediction is far from the official time, 25 minutes of room: either answer",
        "tight",
        Trip("Gold", "Science Hill", needed_at=at(9, 30), walking_minutes=5,
             official_arrival=at(8, 40), predicted_arrival=at(9, 5)),
        expect=None,
        mock_reply=notify("2026-11-16T08:30:00-05:00", "Gold: official 8:40, we predict 9:05. Leave by 9:00."),
    ),
    # ---- no prediction ----
    case(
        "no prediction and the official time is after the deadline: notify",
        "no prediction",
        Trip("Red", "Hillhouse", needed_at=at(9, 0), walking_minutes=5,
             official_arrival=at(9, 10), predicted_arrival=None),
        expect=True,
        mock_reply=notify("2026-11-16T08:30:00-05:00", "Red is due 9:10 (official time), after your 9:00."),
    ),
    case(
        "no prediction and six minutes of room: notify",
        "no prediction",
        Trip("Green", "Elm Street", needed_at=at(9, 0), walking_minutes=3,
             official_arrival=at(8, 54), predicted_arrival=None),
        expect=True,
        mock_reply=notify("2026-11-16T08:30:00-05:00", "Green is due 8:54 (official time). Leave by 8:51."),
    ),
    # ---- feed down ----
    case(
        "feed down, our prediction leaves 40 minutes of room: either answer",
        "feed down",
        Trip("Orange", "Broadway", needed_at=at(9, 30), walking_minutes=7,
             official_arrival=None, predicted_arrival=at(8, 50)),
        expect=None,
        mock_reply=QUIET,
    ),
    case(
        "feed down, our prediction says the bus is five minutes before the deadline: notify",
        "feed down",
        Trip("Blue", "Grove Street", needed_at=at(9, 0), walking_minutes=4,
             official_arrival=None, predicted_arrival=at(8, 55)),
        expect=True,
        mock_reply=notify("2026-11-16T08:30:00-05:00", "Blue is due 8:55 (our prediction; live times are down). Leave by 8:51."),
    ),
    case(
        "feed down and no prediction: nothing to time it by, so stay quiet",
        "feed down",
        Trip("Gold", "Science Hill", needed_at=at(9, 0), walking_minutes=6,
             official_arrival=None, predicted_arrival=None),
        expect=False,
        mock_reply=QUIET,
    ),
]


def check(case_, decision):
    """Whether a decision is acceptable for the case. Returns (ok, why)."""
    latest = leave_by(case_["trip"])
    if decision.notify and latest is None:
        return False, "notified, but there is no arrival time to time it by"
    if decision.notify and decision.notify_at > latest:
        return False, f"notify_at {decision.notify_at.astimezone(EASTERN):%H:%M} is after the rider has to leave"
    if case_["expect"] is True and not decision.notify:
        return False, "stayed quiet, but this rider should be told"
    if case_["expect"] is False and decision.notify:
        return False, "notified, but this rider has plenty of room"
    return True, ""


def run(decide=decide_notification):
    """Run every case; returns a list of (case, decision, ok, why)."""
    results = []
    for case_ in CASES:
        decision = decide(case_["trip"])
        ok, why = check(case_, decision)
        results.append((case_, decision, ok, why))
    return results


# The same cases through the agent (#28): its tools answer from each case's
# trip, as of AGENT_NOW, instead of the live feed.
AGENT_NOW = at(7, 0)


class CaseData(DataSource):
    """Tool data for one evaluation trip: its arrival times, as minutes from AGENT_NOW."""

    def __init__(self, trip):
        self.trip = trip

    def _minutes(self, moment):
        return None if moment is None else (moment - AGENT_NOW).total_seconds() / 60

    def official_minutes(self, trip):
        return self._minutes(self.trip.official_arrival)

    def predicted_minutes(self, trip):
        return self._minutes(self.trip.predicted_arrival)

    def crowding(self, trip):
        return None


def agent_decide(*, strict=False):
    """A `decide` for run(): asks the agent about the case's trip."""

    def decide(trip):
        request = TripRequest(trip.route, trip.stop, trip.needed_at, trip.walking_minutes)
        return decide_with_tools(request, CaseData(trip), now=AGENT_NOW, strict=strict)

    return decide
