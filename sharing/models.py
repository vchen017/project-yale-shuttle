"""Live trips a rider shares with a friend (#10).

A rider on the trip page shares the bus they're taking. Anyone with the link
can follow that bus to the stop where the rider gets off, without logging
in, until the link expires (LIFETIME) or the rider stops sharing. A share
holds nothing personal: no name, and none of the rider's places, only the
route, the bus and the two stops, which are the shuttle's own public data.
"""

import datetime as dt
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

LIFETIME = dt.timedelta(hours=3)


def new_token():
    """The link's secret part: 32 random URL-safe characters, unguessable."""
    return secrets.token_urlsafe(24)


class TripShare(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="trip_shares"
    )
    token = models.CharField(max_length=64, unique=True, default=new_token)
    route_id = models.IntegerField()
    route_name = models.CharField(max_length=100)
    route_color = models.CharField(max_length=6, default="00356b")
    bus_id = models.IntegerField(null=True, blank=True, help_text="The bus to follow, when the feed named it.")
    board_stop_id = models.IntegerField()
    board_stop_name = models.CharField(max_length=100)
    get_off_stop_id = models.IntegerField()
    get_off_stop_name = models.CharField(max_length=100)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    stopped_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"])]

    def __str__(self):
        return f"{self.user} · {self.route_name} to {self.get_off_stop_name}"

    def save(self, *args, **kwargs):
        if self.expires_at is None:
            self.expires_at = self.created_at + LIFETIME
        super().save(*args, **kwargs)

    def is_live(self, now=None):
        """Still being shared: not stopped, and not yet expired."""
        return self.stopped_at is None and (now or timezone.now()) < self.expires_at
