"""Demo crowding reports for graders (#46), run by `manage.py seed`.

Sample reports from the rider account on the weekday daytime routes, one per
route per hour (8 AM to 6 PM Eastern) on each weekday of the last two weeks,
busier at rush hours. They're marked is_sample: they fill in the crowding
page's "typical crowding by time of day" (labelled as including samples) and
nothing else. They never count as a bus's crowding right now, and the next
trip card and the AI never see them (#181), so no rider is told a bus is
full when it isn't.

Typical crowding looks back four weeks, so samples age out. A run replaces
them when the newest is over a week old; otherwise it changes nothing, so
running it twice creates nothing twice.
"""

import datetime as dt
from zoneinfo import ZoneInfo

from django.utils import timezone

from .models import CrowdReport

ET = ZoneInfo("America/New_York")
DAYS = 14
REFRESH_AFTER = dt.timedelta(days=7)

# Route ids and names from the live feed (routes_routes.php), Nov 16, 2026.
# Routes are identified by id: short names aren't unique.
ROUTES = {
    1: "Blue - Weekday Daytime",
    2: "Orange - Weekday Daytime",
    3: "Red - Weekday Daytime",
    9: "Green - West Campus",
    10: "Purple - West Campus",
}
Level = CrowdReport.Level
# Eastern hour -> how busy it usually is: rush hours and lunch are busiest.
BY_HOUR = {
    8: Level.STANDING, 9: Level.FULL, 10: Level.SEATS, 11: Level.SEATS, 12: Level.STANDING,
    13: Level.SEATS, 14: Level.EMPTY, 15: Level.SEATS, 16: Level.STANDING, 17: Level.FULL,
    18: Level.SEATS,
}


def seed(accounts, stdout):
    now = timezone.now()
    newest = CrowdReport.objects.filter(is_sample=True).order_by("-created_at").first()
    if newest and newest.created_at > now - REFRESH_AFTER:
        stdout.write("Kept the sample crowding reports")
        return

    # The samples the refresh replaces, and only those: a sample an admin
    # removed through moderation stays removed — its record is the audit
    # trail, and seed never undoes an admin's change (AGENTS.md).
    CrowdReport.objects.filter(is_sample=True, removed_at__isnull=True).delete()
    rider = accounts["rider"]
    today = timezone.localtime(now).date()
    reports = []
    for days_ago in range(1, DAYS + 1):
        day = today - dt.timedelta(days=days_ago)
        if day.weekday() >= 5:  # weekday routes only
            continue
        for route_id, name in ROUTES.items():
            for hour, level in BY_HOUR.items():
                # A little day-to-day variation that still averages to `level`.
                wobble = (-1, 0, 0, 1)[(days_ago + route_id + hour) % 4]
                reports.append(
                    CrowdReport(
                        user=rider,
                        route_id=route_id,
                        route_name=name,
                        level=min(max(level + wobble, Level.EMPTY), Level.FULL),
                        created_at=dt.datetime.combine(day, dt.time(hour, 20), tzinfo=ET),
                        is_sample=True,
                    )
                )
    CrowdReport.objects.bulk_create(reports)
    stdout.write(f"Created {len(reports)} sample crowding reports")
