"""The recorded shuttle history, imported by the import_history command (#22).

One Poll per five-minute poll the recorder made. Recorded polls have the rows
the Downtowner feed served; polls listed in the recorder's data/gaps.csv are
stored as MISSING so "we didn't record it" never looks like "no buses ran".
Every child row keeps its own polled_at (denormalized from its poll) plus the
indexes predictions (#23) and the accuracy dashboard (#17) need.

Routes are identified everywhere by their numeric id, never their short name:
short names aren't unique ("BW" is route 4 and route 16).
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


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
        indexes = [
            models.Index(fields=["route_id", "polled_at"]),
            # polled_at alone: the when-to-leave page asks what ran at an
            # event's weekday and hour, without naming routes (calendars.plans).
            models.Index(fields=["polled_at"]),
        ]

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


class RouteStopList(models.Model):
    """One distinct version of a route's stop list, as observed in the
    recorder's routes.json (#57). The newest first_seen per route is the
    stop list the route serves now; earlier rows are how the walk (#57)
    found the route before that.

    The recorder commits routes.json only when the file changes, so
    (route_id, commit_sha) identifies a version and makes re-running the
    walk idempotent.
    """

    route_id = models.IntegerField(db_index=True)
    route_name = models.CharField(max_length=100)
    # Ordered like the feed serves them; order matters (it is the loop order).
    stop_ids = models.JSONField()
    first_seen = models.DateTimeField()
    commit_sha = models.CharField(max_length=40)

    class Meta:
        ordering = ["route_id", "first_seen"]
        constraints = [
            models.UniqueConstraint(
                fields=["route_id", "commit_sha"], name="unique_version_per_route_commit"
            ),
        ]
        indexes = [models.Index(fields=["route_id", "first_seen"])]

    def __str__(self):
        return f"route {self.route_id}: {len(self.stop_ids)} stops as of {self.first_seen:%Y-%m-%d %H:%M}"


class RouteStopChange(models.Model):
    """A route's stop list changing (#57): which stops were added or
    removed, and when the recorder's routes.json first showed it.

    A reordering (same stops, new order) is recorded too, with both lists
    empty, since the order is the loop order.
    """

    route_id = models.IntegerField(db_index=True)
    changed_at = models.DateTimeField()
    commit_sha = models.CharField(max_length=40)
    added_stop_ids = models.JSONField(default=list)
    removed_stop_ids = models.JSONField(default=list)

    class Meta:
        ordering = ["changed_at", "route_id"]
        indexes = [models.Index(fields=["route_id", "changed_at"])]

    def __str__(self):
        return (
            f"route {self.route_id}: +{self.added_stop_ids} "
            f"-{self.removed_stop_ids} at {self.changed_at:%Y-%m-%d %H:%M}"
        )


class RouteSyncState(models.Model):
    """How far the routes.json walk has run, one row (#57). Re-imports fetch
    only commits newer than last_commit_at, so a walk that processed
    hundreds of commits once never refetches them."""

    last_commit_sha = models.CharField(max_length=40, blank=True, default="")
    last_commit_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "route sync state"
        verbose_name_plural = "route sync state"

    def __str__(self):
        return f"routes.json walked through {self.last_commit_sha[:7] or 'nothing yet'}"


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


class AccuracySnapshot(models.Model):
    """One saved score of one date range, written by the score_accuracy job (#17).

    The dashboard reads these instead of scoring in the request: scoring a
    week of real history takes minutes, far past the server's request time
    limit, so no page may compute one. `payload` is `accuracy.to_storage()`'s
    JSON-safe form of the result; the dashboard turns it back with
    `accuracy.from_storage()`.
    """

    range_start = models.DateField()
    range_end = models.DateField()
    computed_at = models.DateTimeField(default=timezone.now)
    duration_seconds = models.FloatField()
    payload = models.JSONField()

    class Meta:
        ordering = ["-range_end", "-computed_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["range_start", "range_end"], name="unique_range_per_snapshot"
            ),
        ]

    def __str__(self):
        return (
            f"{self.range_start} to {self.range_end}, scored in {self.duration_seconds:g}s "
            f"at {self.computed_at:%Y-%m-%d %H:%M} UTC"
        )

    @property
    def computed_eastern(self):
        """When the score was computed, on the Eastern clock the site shows."""
        return timezone.localtime(self.computed_at)


class RiderReport(models.Model):
    """A rider's report that a bus never came or skipped their stop (#6).

    The misses the accuracy dashboard (#17) counts next to the prediction
    errors: a report is a rider telling us the official schedule failed them,
    even when the recorded data can't show why.
    """

    class Kind(models.TextChoices):
        NEVER_CAME = "never_came", "Never came"
        SKIPPED = "skipped_stop", "Skipped my stop"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="rider_reports"
    )
    # Routes are identified by their numeric id: short names aren't unique.
    route_id = models.PositiveIntegerField()
    # The names when reported, for routes and stops the feed no longer lists.
    route_name = models.CharField(max_length=200)
    stop_id = models.PositiveIntegerField()
    stop_name = models.CharField(max_length=100)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    created_at = models.DateTimeField(default=timezone.now)
    is_sample = models.BooleanField(
        default=False,
        help_text="Demo data from the seed command. Never blocks a real report and is counted separately on the accuracy dashboard (#17).",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # The 15-minute rate limit: a rider's last report of a route and stop.
            models.Index(fields=["user", "route_id", "stop_id", "created_at"]),
            # What the dashboard (#17) counts: misses per route and stop.
            models.Index(fields=["route_id", "stop_id", "created_at"]),
        ]

    def __str__(self):
        return f"{self.route_name} at {self.stop_name}: {self.get_kind_display()} ({self.created_at:%Y-%m-%d %H:%M} UTC)"


class Calibration(models.Model):
    """A learned countdown correction (#117): the median minutes the
    recorded history says a countdown of a given size was off by, for one
    route (or every route, where route_id is None). `bucket` slices the
    countdown by size, per predictions.COUNTDOWN_BUCKETS, with bucket -1
    (predictions.OVERALL_BUCKET) holding the fit's overall median as the
    last fallback. The nightly fit_calibration job rewrites the whole
    table; while it is empty or thin, predictions add nothing and the raw
    countdown answers, so a fresh deploy degrades safely.
    """

    route_id = models.IntegerField(null=True, blank=True)
    bucket = models.SmallIntegerField()
    delta_minutes = models.FloatField()
    samples = models.IntegerField(help_text="How many residuals the median was taken over.")
    fitted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["route_id", "bucket"]

    def __str__(self):
        route = f"route {self.route_id}" if self.route_id is not None else "all routes"
        return f"{route} bucket {self.bucket}: {self.delta_minutes:+g} min over {self.samples}"
