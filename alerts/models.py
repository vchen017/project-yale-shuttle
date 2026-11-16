"""Service alerts (#13): the ones our admins post, and when we first saw each of
the Downtowner's own announcements. Custom alerts (#20) build on Alert."""

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class AlertQuerySet(models.QuerySet):
    def current(self, now=None):
        """The alerts riders should see now: started, and not yet ended."""
        now = now or timezone.now()
        return self.filter(Q(starts_at__lte=now) & Q(ends_at__gt=now))


class Alert(models.Model):
    """A service alert an admin posts in /admin/, such as a detour or a cancelled run."""

    title = models.CharField(max_length=200)
    message = models.TextField()
    starts_at = models.DateTimeField(
        default=timezone.now, help_text="Riders see it from this time, which is shown as its posting time."
    )
    ends_at = models.DateTimeField(help_text="It's hidden from riders after this time.")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AlertQuerySet.as_manager()

    class Meta:
        ordering = ["-starts_at"]

    def __str__(self):
        return self.title

    def clean(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "The end has to be after the start."})


class FeedAnnouncement(models.Model):
    """When we first saw one of the Downtowner's announcements.

    The feed doesn't say when an announcement was posted, so we show when it
    first appeared: the earliest poll in the recorded history that has it, or
    else the first time a rider's page asked for it.
    """

    announcement_id = models.IntegerField(unique=True)
    first_seen_at = models.DateTimeField()

    class Meta:
        ordering = ["-first_seen_at"]

    def __str__(self):
        return f"Downtowner announcement {self.announcement_id}, first seen {self.first_seen_at:%Y-%m-%d %H:%M} UTC"
