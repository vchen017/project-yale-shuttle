"""The evaluation set for the notification decision (issue #37).

Each case is an invented trip, the decision we expect from a good model, and a
`mock_reply`: the answer a good model would give. CI feeds the mock replies
through `decide_notification` with OpenRouter mocked, which checks the prompt
plumbing, the validation and our scoring. `manage.py evaluate_assistant` runs
the same cases against the real model once we have a key.

A case expects either "don't notify", or "notify at some time in
[earliest, latest]" (the message wording isn't scored). The latest time is the
moment the rider has to leave for the stop to catch the bus; the earliest is
when a heads-up stops being useful.
"""

from datetime import datetime, timedelta, timezone

from .decide import DONT_NOTIFY, Decision, Trip, decide_notification

EASTERN = timezone(timedelta(hours=-5))  # EST, the offset on Nov 16, 2026


def at(hour, minute):
    return datetime(2026, 11, 16, hour, minute, tzinfo=EASTERN)


def case(name, trip, notify, mock_reply, window=None):
    return {"name": name, "trip": trip, "notify": notify, "window": window, "mock_reply": mock_reply}


CASES = [
    case(
        "on-time bus and plenty of slack: stay quiet",
        Trip("Blue", "Grove Street", needed_at=at(9, 0), walking_minutes=5,
             official_arrival=at(8, 30), predicted_arrival=at(8, 31)),
        notify=False,
        mock_reply='{"notify": false, "notify_at": null, "message": ""}',
    ),
    case(
        "tight connection: leave by 8:40",
        Trip("Blue", "Grove Street", needed_at=at(9, 0), walking_minutes=10,
             official_arrival=at(8, 45), predicted_arrival=at(8, 50)),
        notify=True, window=(at(8, 25), at(8, 40)),
        mock_reply='{"notify": true, "notify_at": "2026-11-16T08:35:00-05:00", '
                   '"message": "Blue reaches Grove St at 8:50. Leave by 8:40."}',
    ),
    case(
        "our prediction says the bus is late: trust it over the official time",
        Trip("Gold", "Science Hill", needed_at=at(9, 10), walking_minutes=5,
             official_arrival=at(8, 40), predicted_arrival=at(8, 55)),
        notify=True, window=(at(8, 35), at(8, 50)),
        mock_reply='{"notify": true, "notify_at": "2026-11-16T13:40:00+00:00", '
                   '"message": "Gold is running ~15 min late. Leave by 8:50 instead."}',
    ),
    case(
        "no prediction, official time only, tight",
        Trip("Orange", "Broadway", needed_at=at(8, 45), walking_minutes=8,
             official_arrival=at(8, 20), predicted_arrival=None),
        notify=True, window=(at(8, 0), at(8, 12)),
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
        notify=True, window=(at(9, 15), at(9, 22)),
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
    earliest, latest = case_["window"]
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
