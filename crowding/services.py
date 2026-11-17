"""Crowding from riders' reports (#12): each route's current crowding, from the
last 30 minutes, and its typical crowding by hour of the day, from the last
four weeks.

A level is the average of the reports' levels (0 empty to 3 full), rounded to
the nearest level. Hours are Eastern, where riders live; reports are stored
in UTC. The bucketing happens in Python rather than SQL, so SQLite on laptops
and PostgreSQL in CI and on the servers give the same answer.
"""

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django.utils.formats import time_format

from transit.network import text_color

from .models import CrowdReport, ReportBlock

CURRENT_WINDOW = dt.timedelta(minutes=30)  # "current" means reported this recently
REPORT_INTERVAL = dt.timedelta(minutes=10)  # one report per rider per route this often
HOURLY_INTERVAL = dt.timedelta(hours=1)
TYPICAL_WINDOW = dt.timedelta(days=28)  # typical crowding learns from this far back
HOURLY_LIMIT = 6  # reports per rider per hour, across all routes (#19)
MIN_TYPICAL_REPORTS = 3  # fewer than this in an hour isn't a pattern

Level = CrowdReport.Level


class TooSoon(Exception):
    """The rider already reported this route within REPORT_INTERVAL."""


class TooMany(Exception):
    """The rider already made HOURLY_LIMIT reports in the last hour (#19)."""


class Blocked(Exception):
    """An admin blocked this rider from reporting (#19)."""


def is_blocked(user):
    return ReportBlock.objects.filter(user=user, unblocked_at__isnull=True).exists()


@dataclass
class HourCrowding:
    hour: int  # 0-23, Eastern
    level: int
    count: int

    @property
    def label(self):
        return Level(self.level).label

    @property
    def hour_label(self):
        return time_format(dt.time(self.hour), "g A")


@dataclass
class RouteCrowding:
    route_id: int
    name: str
    label: str
    color: str
    current_level: int | None = None
    current_count: int = 0
    latest_at: dt.datetime | None = None
    by_hour: list = field(default_factory=list)  # HourCrowding, for hours with enough reports
    typical_now: HourCrowding | None = None
    includes_samples: bool = False

    @property
    def text_color(self):
        return text_color(self.color)

    @property
    def current_label(self):
        return None if self.current_level is None else Level(self.current_level).label


def average_level(levels):
    """The nearest level to the reports' average, halves rounding up (busier)."""
    return int(sum(levels) / len(levels) + 0.5)


def crowding(routes, now=None):
    """Current and typical crowding for each route.

    `routes` is a list of {"id", "name", "label", "color"}, as transit.network
    gives them; the answer keeps their order.
    """
    now = now or timezone.now()
    ids = [route["id"] for route in routes]
    rows = CrowdReport.objects.counted().filter(
        route_id__in=ids, created_at__gt=now - TYPICAL_WINDOW, created_at__lte=now
    ).values_list("route_id", "level", "created_at", "is_sample")

    current = defaultdict(list)  # route id -> [(created_at, level)]
    hourly = defaultdict(lambda: defaultdict(list))  # route id -> hour -> [level]
    samples = set()
    for route_id, level, created_at, is_sample in rows:
        # Sample (demo) reports shape the typical pattern but never claim to
        # be how busy a bus is right now.
        if created_at > now - CURRENT_WINDOW and not is_sample:
            current[route_id].append((created_at, level))
        hourly[route_id][timezone.localtime(created_at).hour].append(level)
        if is_sample:
            samples.add(route_id)

    this_hour = timezone.localtime(now).hour
    answer = []
    for route in routes:
        route_id = route["id"]
        crowd = RouteCrowding(route_id, route["name"], route["label"], route["color"])
        if current[route_id]:
            crowd.current_level = average_level([level for _, level in current[route_id]])
            crowd.current_count = len(current[route_id])
            crowd.latest_at = max(created_at for created_at, _ in current[route_id])
        crowd.by_hour = [
            HourCrowding(hour, average_level(levels), len(levels))
            for hour, levels in sorted(hourly[route_id].items())
            if len(levels) >= MIN_TYPICAL_REPORTS
        ]
        crowd.typical_now = next((h for h in crowd.by_hour if h.hour == this_hour), None)
        crowd.includes_samples = route_id in samples and bool(crowd.by_hour)
        answer.append(crowd)
    return answer


def report(user, route_id, route_name, level, now=None):
    """Save a rider's report, unless they're blocked (Blocked), already made
    HOURLY_LIMIT reports in the last hour (TooMany, with the time they can
    report again), or reported this route in the last 10 minutes.

    Raises TooSoon with the time they can report again. The rider's row is
    locked while checking, so two quick taps can't both get through
    (PostgreSQL; SQLite runs one write at a time anyway).
    """
    now = now or timezone.now()
    with transaction.atomic():
        get_user_model().objects.select_for_update().filter(pk=user.pk).first()
        if is_blocked(user):
            raise Blocked
        # Counts reports an admin removed too, or removing one would let the rider post another.
        recent = list(
            CrowdReport.objects.filter(user=user, created_at__gt=now - HOURLY_INTERVAL, is_sample=False)
            .order_by("-created_at")
            .values_list("created_at", flat=True)[:HOURLY_LIMIT]
        )
        if len(recent) >= HOURLY_LIMIT:
            raise TooMany(recent[-1] + HOURLY_INTERVAL)
        last = (
            CrowdReport.objects.filter(
                user=user, route_id=route_id, created_at__gt=now - REPORT_INTERVAL, is_sample=False
            )
            .order_by("-created_at")
            .first()
        )
        if last:
            raise TooSoon(last.created_at + REPORT_INTERVAL)
        return CrowdReport.objects.create(
            user=user, route_id=route_id, route_name=route_name, level=level, created_at=now
        )


MAX_SUMMARY_DAYS = 92  # the longest date range the admin summary (#18) covers


@dataclass
class SummaryCell:
    hour: int  # 0-23, Eastern
    average: float  # mean level, 0 (empty) to 3 (full)
    count: int
    full_count: int  # reports of "Full"

    @property
    def level(self):
        """The nearest level, to colour the cell the way the crowding page does."""
        return average_level([self.average])

    @property
    def label(self):
        return Level(self.level).label


@dataclass
class SummaryRow:
    route_id: int
    name: str
    cells: dict  # hour -> SummaryCell, for the hours with reports
    count: int = 0
    full_count: int = 0


def crowding_summary(start, end):
    """Average reported crowding per route and hour of day, for the Eastern
    dates `start` to `end` inclusive (#18).

    Returns (rows, hours): one SummaryRow per route with reports, sorted by
    name, and the hours (Eastern, ascending) that any route has reports for.
    Reports an admin removed (#16) don't count. Sample (demo) reports do, as
    on the crowding page, and `includes_samples` says so. Routes are
    identified by id, named by their latest report.
    """
    tz = timezone.get_current_timezone()
    first = dt.datetime.combine(start, dt.time.min, tzinfo=tz)
    after = dt.datetime.combine(end + dt.timedelta(days=1), dt.time.min, tzinfo=tz)
    reports = (
        CrowdReport.objects.counted()
        .filter(created_at__gte=first, created_at__lt=after)
        .order_by("created_at")
        .values_list("route_id", "route_name", "level", "created_at", "is_sample")
    )
    levels = defaultdict(lambda: defaultdict(list))  # route id -> hour -> [level]
    names = {}
    includes_samples = False
    for route_id, route_name, level, created_at, is_sample in reports:
        names[route_id] = route_name  # oldest first, so the latest name wins
        levels[route_id][timezone.localtime(created_at).hour].append(level)
        includes_samples = includes_samples or is_sample

    rows = []
    for route_id, by_hour in levels.items():
        cells = {
            hour: SummaryCell(hour, sum(found) / len(found), len(found), found.count(Level.FULL))
            for hour, found in sorted(by_hour.items())
        }
        rows.append(
            SummaryRow(
                route_id, names[route_id], cells,
                count=sum(c.count for c in cells.values()),
                full_count=sum(c.full_count for c in cells.values()),
            )
        )
    rows.sort(key=lambda row: (row.name.casefold(), row.route_id))
    hours = sorted({hour for row in rows for hour in row.cells})
    return rows, hours, includes_samples
