"""Turn parsed recorder rows into database rows. No network access here —
sources.py fetches and parses, this module writes, so tests can feed fixture
rows straight in (#22).

Rerunning adds no duplicates: a poll recorded once is skipped, and the
per-poll unique constraints are the safety net. Each poll is imported inside
one transaction, so an interrupted run can't leave a half-filled poll behind.
"""

import datetime as dt
from dataclasses import dataclass

from django.db import transaction

from .models import Announcement, BusPosition, Eta, Poll, RouteStatus

# Files a poll's rows come from, as {row key: model}, with the DayStats
# field each one reports.
_CHILD_MODELS = {
    "buses": BusPosition,
    "etas": Eta,
    "route_status": RouteStatus,
    "announcements": Announcement,
}
_CHILD_MODEL_STAT_FIELDS = {
    "buses": "buses",
    "etas": "etas",
    "route_status": "route_statuses",
    "announcements": "announcements",
}


@dataclass
class DayStats:
    date: dt.date
    polls_added: int = 0
    polls_skipped: int = 0
    buses: int = 0
    etas: int = 0
    route_statuses: int = 0
    announcements: int = 0


@dataclass
class GapStats:
    polls_added: int = 0
    polls_already_known: int = 0


def _utc_day_range(date):
    start = dt.datetime(date.year, date.month, date.day, tzinfo=dt.timezone.utc)
    end = start + dt.timedelta(days=1)
    return start, end


def import_gaps(gap_rows):
    """Store the recorder's missing polls as MISSING, never as 'no buses'."""
    stats = GapStats()
    for row in gap_rows:
        poll, created = Poll.objects.get_or_create(
            polled_at=row["polled_at"],
            defaults={
                "status": Poll.Status.MISSING,
                "cause": row["cause"],
                "active_routes": row["active_routes"],
                "bus_count": row["bus_count"],
                "announcement_count": row["announcement_count"],
                "eta_count": row["eta_count"],
            },
        )
        if created:
            stats.polls_added += 1
        else:
            stats.polls_already_known += 1
    return stats


def import_day(date, day):
    """Import one UTC date's parsed rows (as read_day returned them)."""
    stats = DayStats(date=date)
    polls = {}
    for key, rows in day.items():
        for row in rows:
            polls.setdefault(row["polled_at"], {}).setdefault(key, []).append(row)

    start, end = _utc_day_range(date)
    recorded = set(
        Poll.objects.filter(polled_at__range=(start, end))
        .values_list("polled_at", flat=True)
    )

    for polled_at, rows_by_key in sorted(polls.items()):
        if polled_at in recorded:
            stats.polls_skipped += 1
            continue
        with transaction.atomic():
            poll, _ = Poll.objects.get_or_create(
                polled_at=polled_at, defaults={"status": Poll.Status.RECORDED}
            )
            for key, model in _CHILD_MODELS.items():
                rows = rows_by_key.get(key, [])
                if rows:
                    model.objects.bulk_create(
                        [model(poll=poll, **row) for row in rows], ignore_conflicts=True
                    )
                    field = _CHILD_MODEL_STAT_FIELDS[key]
                    setattr(stats, field, getattr(stats, field) + len(rows))
        stats.polls_added += 1
    return stats
