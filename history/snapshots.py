"""Saving the dashboard's scores, so no page ever computes one (#17).

Scoring a week of real history takes minutes — well past the server's
request time limit — so the score_accuracy command scores a range and saves
it here, and the dashboard reads the saved result. The review on #17 asked
for exactly this: compute after the import, show the saved number.
"""

import datetime as dt
import time

from django.utils import timezone

from . import accuracy
from .models import AccuracySnapshot


def score(start, end):
    """Score [start, end] (Eastern dates) and save the result, replacing any
    earlier score of the same range. Safe to run twice: the second run
    recomputes and overwrites, so a late or repeated job changes nothing."""
    began = time.monotonic()
    result = accuracy.accuracy(
        accuracy.eastern_midnight(start),
        accuracy.eastern_midnight(end + dt.timedelta(days=1)),
    )
    duration = time.monotonic() - began
    snapshot, _ = AccuracySnapshot.objects.update_or_create(
        range_start=start,
        range_end=end,
        defaults={
            "computed_at": timezone.now(),
            "duration_seconds": duration,
            "payload": accuracy.to_storage(result),
        },
    )
    return snapshot


def describe(snapshot):
    """One run-log line: the range, what it scored, how long it took."""
    return (
        f"{snapshot.range_start} to {snapshot.range_end}: "
        f"{snapshot.payload['overall']['queries']} queries scored "
        f"in {snapshot.duration_seconds:.1f}s"
    )
