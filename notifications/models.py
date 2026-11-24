from django.conf import settings
from django.db import models


class PushSubscription(models.Model):
    """One device's browser push subscription: where to send a rider's
    notifications on that device, and the keys to encrypt them with (#27)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_subscriptions"
    )
    # The push service's address for this device. A browser gives each device
    # its own, so it identifies the device.
    endpoint = models.URLField(max_length=1000, unique=True)
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=100)
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} on {self.device}"

    @property
    def device(self):
        """A short label for the device, like "Safari on iPhone", from its user agent."""
        agent = self.user_agent
        browser = next(
            (
                name
                for marker, name in [
                    ("Edg/", "Edge"),
                    ("Firefox/", "Firefox"),
                    ("FxiOS/", "Firefox"),
                    ("CriOS/", "Chrome"),
                    ("Chrome/", "Chrome"),
                    ("Safari/", "Safari"),
                ]
                if marker in agent
            ),
            "A browser",
        )
        system = next(
            (
                name
                for marker, name in [
                    ("iPhone", "iPhone"),
                    ("iPad", "iPad"),
                    ("Android", "Android"),
                    ("Mac OS X", "Mac"),
                    ("Windows", "Windows"),
                    ("CrOS", "Chromebook"),
                    ("Linux", "Linux"),
                ]
                if marker in agent
            ),
            None,
        )
        return f"{browser} on {system}" if system else browser

    def info(self):
        """The subscription in the shape the push library expects."""
        return {"endpoint": self.endpoint, "keys": {"p256dh": self.p256dh, "auth": self.auth}}


class TripNotification(models.Model):
    """A notification the trip watch sent a rider about an upcoming event (#29).

    It's recorded before it goes out, under a unique constraint, so two
    overlapping runs, or a rerun, never send the same one twice: the second
    insert fails, and that run stands down. `sequence` counts a trip's
    notifications: 0 for the first, then one more each time the time to
    leave moves by 5 minutes or more (notifications.trips.RENOTIFY_SHIFT).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="trip_notifications"
    )
    # The event the way it survives the hourly calendar refresh, which
    # replaces its rows: its calendar, its UID and its start.
    calendar = models.ForeignKey(
        "calendars.Calendar", on_delete=models.CASCADE, related_name="trip_notifications"
    )
    event_uid = models.CharField(max_length=255)
    event_start = models.DateTimeField()
    event_title = models.CharField(max_length=200)
    sequence = models.PositiveSmallIntegerField(default=0)
    leave_by = models.DateTimeField(help_text="The time to leave this notification was about.")
    message = models.CharField(max_length=200)
    devices = models.PositiveSmallIntegerField(
        default=0, help_text="How many of the rider's devices' push services took it."
    )
    sent_at = models.DateTimeField()

    class Meta:
        ordering = ["-sent_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["calendar", "event_uid", "event_start", "sequence"],
                name="notifications_one_per_trip_and_sequence",
            ),
        ]

    def __str__(self):
        return f"{self.user} · {self.event_title} at {self.event_start:%Y-%m-%d %H:%M} UTC (#{self.sequence})"
