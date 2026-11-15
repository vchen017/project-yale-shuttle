"""The evaluation set for the notification decision (issue #37).

Each case is an invented trip, the decision we expect from a good model, and a
`mock_reply`: the answer a good model would give. CI feeds the mock replies
through `decide_notification` with OpenRouter mocked, which checks the prompt
plumbing, the validation and our scoring. `manage.py evaluate_assistant` runs
the same cases against the real model once we have a key.

"Tight" means 30 minutes of slack or less between the bus arriving and the
rider's deadline (`TIGHT_SLACK_MINUTES` in decide.py): a team rule of thumb that
the other cases are built around.

A case expects either "don't notify", or "notify at some time in `window(trip)`"
(the message wording isn't scored): at most LEAD_MINUTES_LATEST before the rider
has to leave for the stop, and no later than that moment.
"""

from datetime import datetime, timedelta, timezone

from .decide import LEAD_MINUTES_LATEST, Trip, decide_notification

EASTERN = timezone(timedelta(hours=-5))  # EST, the offset on Nov 16, 2026


def at(hour, minute):
    return datetime(2026, 11, 16, hour, minute, tzinfo=EASTERN)


def case(name, trip, notify, mock_reply):
    return {"name": name, "trip": trip, "notify": notify, "mock_reply": mock_reply}


def window(trip):
    """When a notification for this trip is on time: the same rule the prompt gives
    the model (LEAD_MINUTES_LATEST before the leave-by time, up to the leave-by time).
    Computed, not hand-written, so the cases can't drift from the prompt."""
    arrival = trip.predicted_arrival or trip.official_arrival
    leave_by = arrival - timedelta(minutes=trip.walking_minutes)
    return leave_by - timedelta(minutes=LEAD_MINUTES_LATEST), leave_by


CASES = [
    case(
        "on-time bus and plenty of slack (44 min): stay quiet",
        Trip("Blue", "Grove Street", needed_at=at(9, 0), walking_minutes=5,
             official_arrival=at(8, 15), predicted_arrival=at(8, 16)),
        notify=False,
        mock_reply='{"notify": false, "notify_at": null, "message": ""}',
    ),
    case(
        "tight connection: leave by 8:40",
        Trip("Blue", "Grove Street", needed_at=at(9, 0), walking_minutes=10,
             official_arrival=at(8, 45), predicted_arrival=at(8, 50)),
        notify=True,
        mock_reply='{"notify": true, "notify_at": "2026-11-16T08:35:00-05:00", '
                   '"message": "Blue reaches Grove St at 8:50. Leave by 8:40."}',
    ),
    case(
        "our prediction says the bus is late: trust it over the official time",
        Trip("Gold", "Science Hill", needed_at=at(9, 10), walking_minutes=5,
             official_arrival=at(8, 40), predicted_arrival=at(8, 55)),
        notify=True,
        mock_reply='{"notify": true, "notify_at": "2026-11-16T13:40:00+00:00", '
                   '"message": "Gold is running ~15 min late. Leave by 8:50 instead."}',
    ),
    case(
        "no prediction, official time only, tight",
        Trip("Orange", "Broadway", needed_at=at(8, 45), walking_minutes=8,
             official_arrival=at(8, 20), predicted_arrival=None),
        notify=True,
        mock_reply='{"notify": true, "notify_at": "2026-11-16T08:05:00-05:00", '
                   '"message": "Orange is due at 8:20 (official time). Leave by 8:12."}',
    ),
    case(
        "bus well before the deadline, short walk: stay quiet",
        Trip("Green", "Hillhouse", needed_at=at(9, 0), walking_minutes=3,
             official_arrival=at(8, 10), predicted_arrival=at(8, 12)),
        notify=False,
        mock_reply='{"notify": false, "notify_at": null, "message": ""}',
    ),
    case(
        "long walk makes an otherwise easy bus tight",
        Trip("Blue", "Prospect Street", needed_at=at(10, 0), walking_minutes=20,
             official_arrival=at(9, 40), predicted_arrival=at(9, 42)),
        notify=True,
        mock_reply='{"notify": true, "notify_at": "2026-11-16T09:20:00-05:00", '
                   '"message": "20 min walk to Prospect St: leave by 9:22 for the 9:42 Blue."}',
    ),
]


def check(case_, decision):
    """Whether a decision is the one the case expects. Returns (ok, why)."""
    if not case_["notify"]:
        return (not decision.notify, "notified, but we expected silence")
    if not decision.notify:
        return False, "stayed quiet, but we expected a notification"
    earliest, latest = window(case_["trip"])
    if not earliest <= decision.notify_at <= latest:
        return False, f"notify_at {decision.notify_at.astimezone(EASTERN):%H:%M} is outside {earliest:%H:%M}-{latest:%H:%M}"
    return True, ""


def run(decide=decide_notification):
    """Run every case; returns a list of (case, decision, ok, why)."""
    results = []
    for case_ in CASES:
        decision = decide(case_["trip"])
        ok, why = check(case_, decision)
        results.append((case_, decision, ok, "" if ok else why))
    return results
