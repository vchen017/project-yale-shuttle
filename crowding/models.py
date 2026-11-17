"""Riders' reports of how busy their bus is (#12)."""

from django.conf import settings
from django.db import models
from django.utils import timezone


class CrowdReportQuerySet(models.QuerySet):
    def counted(self):
        """Reports that count toward crowding: not removed by an admin (#16)."""
        return self.filter(removed_at__isnull=True)


class CrowdReport(models.Model):
    """One rider's report of how busy a route's bus is, at one moment.

    An admin can remove a bad one (#16). It stays in the database, marked
    with when and by whom, so it can be restored and #19 can see who keeps
    posting bad reports, but it no longer counts toward crowding.
    """

    class Level(models.IntegerChoices):
        # Numbers, so reports can be averaged: higher is busier.
        EMPTY = 0, "Empty"
        SEATS = 1, "Seats available"
        STANDING = 2, "Standing room"
        FULL = 3, "Full (can't board)"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="crowd_reports"
    )
    # Routes are identified by their numeric id: short names aren't unique.
    route_id = models.PositiveIntegerField()
    # The route's name when reported, for routes the feed no longer lists.
    route_name = models.CharField(max_length=200)
    level = models.PositiveSmallIntegerField(choices=Level.choices)
    created_at = models.DateTimeField(default=timezone.now)
    is_sample = models.BooleanField(
        default=False,
        help_text="Demo data from the seed command (#46). Never counted as current crowding.",
    )
    removed_at = models.DateTimeField(
        null=True, blank=True, help_text="When an admin removed it. Removed reports don't count toward crowding."
    )
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    objects = CrowdReportQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["route_id", "created_at"]),
            models.Index(fields=["user", "route_id", "created_at"]),
        ]

    def __str__(self):
        return f"{self.route_name}: {self.get_level_display()} at {self.created_at:%Y-%m-%d %H:%M} UTC"


class ReportBlock(models.Model):
    """An admin's block on a rider's crowd reports (#19), with the reason.

    A rider is blocked while a row for them has no `unblocked_at`. Unblocking
    keeps the row, so the history of who was blocked and why stays.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="report_blocks")
    reason = models.TextField(help_text="Why, for other admins. Riders aren't shown it.")
    blocked_at = models.DateTimeField(default=timezone.now)
    blocked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    unblocked_at = models.DateTimeField(null=True, blank=True)
    unblocked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-blocked_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"], condition=models.Q(unblocked_at__isnull=True), name="one_active_report_block"
            )
        ]

    def __str__(self):
        return f"{self.user} ({'blocked' if self.unblocked_at is None else 'unblocked'})"
