"""Riders' reports of how busy their bus is (#12)."""

from django.conf import settings
from django.db import models
from django.utils import timezone


class CrowdReport(models.Model):
    """One rider's report of how busy a route's bus is, at one moment."""

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

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["route_id", "created_at"]),
            models.Index(fields=["user", "route_id", "created_at"]),
        ]

    def __str__(self):
        return f"{self.route_name}: {self.get_level_display()} at {self.created_at:%Y-%m-%d %H:%M} UTC"
