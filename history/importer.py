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

from .models import (
    Announcement,
    BusPosition,
    Eta,
    Poll,
    RouteStatus,
    RouteStopChange,
    RouteStopList,
    RouteSyncState,
)

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
    start = dt.datetime(date.year, date.month, date.day, tzinfo=dt.UTC)
    end = start + dt.timedelta(days=1)
    return start, end


def import_gaps(gap_rows):
    """Store the recorder's missing polls as MISSING, never as 'no buses'."""
    stats = GapStats()
    for row in gap_rows:
        _, created = Poll.objects.get_or_create(
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


@dataclass
class RoutesStats:
    commits_seen: int = 0
    versions_added: int = 0
    changes_added: int = 0


def import_route_history(commits, fetch_routes):
    """Walk the recorder's routes.json versions, oldest first, recording each
    route's distinct stop list and every change between versions (#57).

    `commits` is [(sha, committed_at)]; `fetch_routes(sha)` returns the
    parsed routes.json at that commit, one dict per route. Commits are only
    fetched if newer than the last one the sync state processed, so a
    re-run fetches nothing already walked. A commit whose stop lists all
    match the latest known versions (an active-flag-only change, say)
    records nothing. Each commit is processed inside one transaction and
    only lists that differ from the route's latest version are stored, so
    an interrupted walk reprocesses its last commit and adds nothing.
    """
    state = RouteSyncState.objects.first()
    if state is None:
        state = RouteSyncState.objects.create()
    pending = [
        entry
        for entry in commits
        if state.last_commit_at is None or entry[1] > state.last_commit_at
    ]
    stats = RoutesStats(commits_seen=len(pending))

    latest = {}  # route_id -> its newest RouteStopList so far
    for row in RouteStopList.objects.order_by("first_seen", "id"):
        latest[row.route_id] = row

    for sha, committed_at in pending:
        with transaction.atomic():
            for route in fetch_routes(sha):
                stop_ids = list(route["stops"])
                current = latest.get(route["id"])
                if current is not None and current.stop_ids == stop_ids:
                    # The same list again — an active-flag-only commit, say.
                    continue
                version = RouteStopList.objects.create(
                    route_id=route["id"],
                    route_name=route.get("name", ""),
                    stop_ids=stop_ids,
                    first_seen=committed_at,
                    commit_sha=sha,
                )
                latest[route["id"]] = version
                stats.versions_added += 1
                if current is None:
                    continue  # the route's first observed stop list: no change yet
                # Only reached when the list differs, so a pure reorder (same
                # stops, new order) lands here with both lists empty.
                added = [stop for stop in stop_ids if stop not in current.stop_ids]
                removed = [stop for stop in current.stop_ids if stop not in stop_ids]
                RouteStopChange.objects.create(
                    route_id=route["id"],
                    changed_at=committed_at,
                    commit_sha=sha,
                    added_stop_ids=added,
                    removed_stop_ids=removed,
                )
                stats.changes_added += 1
            state.last_commit_sha = sha
            state.last_commit_at = committed_at
            state.save()
    return stats
