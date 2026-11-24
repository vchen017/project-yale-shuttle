import dataclasses
import datetime as dt

from django.conf import settings
from django.db import models
from django.utils import timezone

from crowding.models import CrowdReport


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


class AgentAnswer(models.Model):
    """What the trip agent said when a rider asked it about one upcoming trip
    on the when-to-leave page (#148), or why it couldn't say. One per event,
    replaced when the rider asks again. Saved rather than kept in memory: the
    agent answers from a thread of its own, and Render's free plan restarts
    the server when it sleeps."""

    ASKING, ANSWERED, FAILED = "asking", "answered", "failed"
    STATUSES = [(ASKING, "Asking"), (ANSWERED, "Answered"), (FAILED, "Couldn't answer")]
    REUSE = dt.timedelta(minutes=5)  # an answer this fresh is shown again rather than asked for again
    GIVE_UP = dt.timedelta(minutes=3)  # still asking after this long: its thread is gone (a restart)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="agent_answers")
    # The event the way it survives the hourly calendar refresh, as in TripNotification.
    calendar = models.ForeignKey("calendars.Calendar", on_delete=models.CASCADE, related_name="agent_answers")
    event_uid = models.CharField(max_length=255)
    event_start = models.DateTimeField()
    status = models.CharField(max_length=8, choices=STATUSES, default=ASKING)
    asked_at = models.DateTimeField()
    answered_at = models.DateTimeField(null=True, blank=True)
    notify = models.BooleanField(null=True, blank=True)
    notify_at = models.DateTimeField(null=True, blank=True)
    message = models.CharField(max_length=200, blank=True, default="")
    looked_up = models.JSONField(
        default=dict, blank=True, help_text="What the agent's tools read: the next bus's times and crowding."
    )
    reason = models.CharField(
        max_length=200, blank=True, default="", help_text="Why the agent couldn't answer, as the rider reads it."
    )

    class Meta:
        ordering = ["-asked_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["calendar", "event_uid", "event_start"], name="notifications_one_agent_answer_per_trip"
            ),
        ]

    def __str__(self):
        return f"{self.user} · {self.event_uid} at {self.event_start:%Y-%m-%d %H:%M} UTC ({self.status})"

    def gave_up(self, now=None):
        """Asked so long ago without an answer that its thread must be gone."""
        return self.status == self.ASKING and (now or timezone.now()) - self.asked_at >= self.GIVE_UP

    def still_current(self, now=None):
        """Show this again rather than ask again: still on its way, or answered within REUSE."""
        now = now or timezone.now()
        if self.status == self.ASKING:
            return not self.gave_up(now)
        return self.status == self.ANSWERED and now - self.asked_at < self.REUSE

    # What the when-to-leave page shows (templates/notifications/_agent.html).

    @property
    def is_asking(self):
        return self.status == self.ASKING and not self.gave_up()

    @property
    def is_failed(self):
        return self.status == self.FAILED or self.gave_up()

    @property
    def failure(self):
        return "it took too long to answer" if self.gave_up() else self.reason

    @property
    def can_ask_again(self):
        return not self.still_current()

    @property
    def notify_now(self):
        """Its notification would go out right away, not at a later time."""
        return bool(self.notify and self.notify_at and self.answered_at and self.notify_at <= self.answered_at)

    @property
    def timeline(self):
        """The answer as the page draws it, in order: when the rider asked,
        when the alert would go out, when the bus comes (from what the agent
        looked up) and when the event starts."""
        if self.notify_now:
            steps = [Step("alert", "Alert now", self.asked_at, tone="now")]
        else:
            steps = [Step("asked", "Asked", self.asked_at)]
            if self.notify and self.notify_at:
                steps.append(Step("alert", "Alert", self.notify_at))
        bus = self._bus()
        if bus:
            steps.append(bus)
        steps.append(Step("event", "Starts", self.event_start))
        return steps

    def _bus(self):
        """The bus on the timeline: at our prediction's time if the agent had
        one, else the Downtowner's, noting how the two compare. None when it
        looked up neither."""
        seen = self.looked_up or {}
        official, predicted = seen.get("official"), seen.get("predicted")
        has_official = official not in (None, "down")
        if predicted is None:
            return Step("bus", "Bus", dt.datetime.fromisoformat(official)) if has_official else None
        bus = Step("bus", "Bus", dt.datetime.fromisoformat(predicted))
        if not has_official:
            if "official" in seen:
                bus.note = "Feed down" if official == "down" else "Not listed"
            return bus
        later = _minutes_between(official, predicted)
        if later > 0:
            bus.note, bus.tone = f"{later} min late", "late" if later >= Step.LATE else ""
        elif later < 0:
            bus.note, bus.tone = f"{-later} min early", "ok"
        else:
            bus.note, bus.tone = "On time", "ok"
        if later:
            bus.was = dt.datetime.fromisoformat(official)
        return bus

    @property
    def crowd(self):
        """How busy the bus is, if the agent asked, for the page's meter."""
        seen = self.looked_up or {}
        if "crowding" not in seen:
            return None
        reports = seen["crowding"] or {}
        if reports.get("current"):
            return Crowd(_crowd_level(reports["current"]), f"Riders report {reports['current'].lower()}")
        if reports.get("typical_now"):
            return Crowd(_crowd_level(reports["typical_now"]), f"Usually {reports['typical_now'].lower()}")
        return Crowd(None, "No crowding reports")


@dataclasses.dataclass
class Step:
    """One point on the trip agent's timeline on the when-to-leave page."""

    LATE = 5  # minutes: our prediction this far behind the Downtowner's time shows in amber
    ICONS = {"asked": "clock", "alert": "bell-fill", "bus": "bus-front-fill", "event": "geo-alt-fill"}

    kind: str  # asked, alert, bus or event
    label: str
    at: dt.datetime
    tone: str = ""  # now, late or ok: its colour (app.css)
    note: str = ""  # for the bus: how our prediction compares with the Downtowner's time
    was: dt.datetime | None = None  # for the bus: the Downtowner's time, when our prediction moves it

    @property
    def icon(self):
        return self.ICONS[self.kind]


@dataclasses.dataclass
class Crowd:
    """How busy the bus is, for the trip agent's crowding meter."""

    level: int | None  # a CrowdReport.Level, for the meter; None when no one has said
    text: str


def _minutes_between(start, end):
    """Whole minutes from one ISO time in looked_up to another."""
    return round((dt.datetime.fromisoformat(end) - dt.datetime.fromisoformat(start)).total_seconds() / 60)


def _crowd_level(label):
    """The crowding level a rider-facing label names, or None for one it doesn't."""
    return {name: level for level, name in CrowdReport.Level.choices}.get(label)
