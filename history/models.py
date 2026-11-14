"""The recorded shuttle history, imported by the import_history command (#22).

One Poll per five-minute poll the recorder made. Recorded polls have the rows
the Downtowner feed served; polls listed in the recorder's data/gaps.csv are
stored as MISSING so "we didn't record it" never looks like "no buses ran".
Every child row keeps its own polled_at (denormalized from its poll) plus the
indexes predictions (#23) and the accuracy dashboard (#17) need.

Routes are identified everywhere by their numeric id, never their short name:
short names aren't unique ("BW" is route 4 and route 16).
"""

from django.db import models


class Poll(models.Model):
    """One five-minute poll of the Downtowner feed by the recorder."""

    class Status(models.TextChoices):
        RECORDED = "recorded", "Recorded"
        MISSING = "missing", "Missing"

    polled_at = models.DateTimeField(unique=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.RECORDED, db_index=True
    )
    # Missing polls only: why the recorder has no rows, and what its job log
    # counted at that moment (all four nullable when the log kept nothing).
    cause = models.CharField(max_length=64, blank=True, default="")
    active_routes = models.IntegerField(null=True, blank=True)
    bus_count = models.IntegerField(null=True, blank=True)
    announcement_count = models.IntegerField(null=True, blank=True)
    eta_count = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ["polled_at"]

    def __str__(self):
        return f"{self.polled_at:%Y-%m-%d %H:%M} UTC ({self.status})"


class RouteStatus(models.Model):
    """A route's row in one poll: whether it was running."""

    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name="+")
    polled_at = models.DateTimeField()
    route_id = models.IntegerField()
    route_name = models.CharField(max_length=100)
    # 50, not a tight fit: short names aren't short — route 19's is
    # "Brown Connector" (15), and PostgreSQL enforces the length where
    # SQLite doesn't (#22 review).
    short_name = models.CharField(max_length=50)
    description = models.CharField(max_length=200, blank=True, default="")
    active = models.BooleanField()
    num_stops = models.IntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["poll", "route_id"], name="unique_route_per_poll"),
        ]
        indexes = [models.Index(fields=["route_id", "polled_at"])]

    def __str__(self):
        return f"{self.route_name} {'active' if self.active else 'inactive'} at {self.polled_at:%H:%M}"


class BusPosition(models.Model):
    """A bus's position in one poll."""

    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name="+")
    polled_at = models.DateTimeField()
    bus_id = models.IntegerField()
    bus_name = models.CharField(max_length=20)
    route_id = models.IntegerField()
    route_name = models.CharField(max_length=100)
    lat = models.FloatField()
    lon = models.FloatField()
    heading = models.IntegerField(null=True, blank=True)
    last_stop_id = models.IntegerField(null=True, blank=True)
    last_stop_name = models.CharField(max_length=100, blank=True, default="")
    last_update = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["poll", "bus_id"], name="unique_bus_per_poll"),
        ]
        indexes = [models.Index(fields=["route_id", "polled_at"])]

    def __str__(self):
        return f"{self.bus_name} on route {self.route_id} at {self.polled_at:%H:%M}"


class Eta(models.Model):
    """One bus's predicted arrival at one stop, in one poll."""

    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name="+")
    polled_at = models.DateTimeField()
    stop_id = models.IntegerField()
    stop_name = models.CharField(max_length=100)
    route_id = models.IntegerField()
    route_name = models.CharField(max_length=100)
    bus_id = models.IntegerField()
    bus_name = models.CharField(max_length=20)
    eta_minutes = models.IntegerField()
    calculation_time = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["poll", "stop_id", "route_id", "bus_id"], name="unique_eta_per_poll"
            ),
        ]
        indexes = [
            # The lookup predictions do over and over: a stop's ETA history.
            models.Index(fields=["route_id", "stop_id", "polled_at"]),
        ]

    def __str__(self):
        return f"{self.bus_name} at {self.stop_name} in {self.eta_minutes} min ({self.polled_at:%H:%M})"


class Announcement(models.Model):
    """A service announcement in effect during one poll."""

    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name="+")
    polled_at = models.DateTimeField()
    announcement_id = models.IntegerField()
    title = models.CharField(max_length=200, blank=True, default="")
    message = models.TextField(blank=True, default="")
    button_text = models.CharField(max_length=200, blank=True, default="")
    button_url = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["poll", "announcement_id"], name="unique_announcement_per_poll"
            ),
        ]
        indexes = [models.Index(fields=["announcement_id", "polled_at"])]

    def __str__(self):
        return f"Announcement {self.announcement_id} at {self.polled_at:%H:%M}"
