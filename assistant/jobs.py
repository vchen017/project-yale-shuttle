"""The assistant app's scheduled job: the hourly check that the AI answers (#159)."""

import datetime as dt
from time import monotonic

from django.conf import settings

from jobs.registry import job

from . import evals, openrouter
from .agent import decide_with_tools
from .decide import InvalidAnswer
from .tools import TripRequest

# The trip the check asks about: the evaluation set's first (#39), a rider with
# an hour and a half to spare. Its tools answer from the case itself, never the
# live feed or the database, so the check is of the AI alone, at any hour.
CASE = evals.CASES[0]


class NoAnswer(Exception):
    """The AI didn't answer the check's trip."""


@job(schedule="43 * * * *", timeout=dt.timedelta(minutes=5))
def check_ai():
    """Check that the AI answers: ask it about a fixed test trip, and fail if it doesn't (#159).

    Hourly, between the calendar refresh (:41) and the trip watch (:45), so the
    database it records the run in is already awake. It asks the way the trip
    watch and the when-to-leave page do: the agent, with its tools (#28).
    """
    trip = CASE["trip"]
    request = TripRequest(trip.route, trip.stop, trip.needed_at, trip.walking_minutes)
    started = monotonic()
    try:
        decision = decide_with_tools(
            request, evals.CaseData(trip), now=evals.AGENT_NOW, strict=True, report_invalid=True
        )
    except (openrouter.OpenRouterError, InvalidAnswer) as error:
        # Just the reason: the run log keeps the traceback, and run_job prints
        # it in the workflow's log, which anyone can read.
        raise NoAnswer(f"The AI didn't answer ({settings.OPENROUTER_MODEL}): {error}") from None
    answer = "notify" if decision.notify else "don't notify"
    return f"The AI answered in {monotonic() - started:.1f} s ({settings.OPENROUTER_MODEL}): {answer}"
