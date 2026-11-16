from django.db import models
from django.db.models import Q
from django.utils import timezone


class JobRun(models.Model):
    """One run of a scheduled job: when it started and ended, and how it went."""

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    job = models.CharField(max_length=64)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RUNNING)
    trigger = models.CharField(
        max_length=64, blank=True, help_text="What started it: the schedule, or who ran it by hand."
    )
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    output = models.TextField(blank=True, help_text="The job's summary, or the error if it failed.")

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["job", "-started_at"], name="jobrun_job_started")]
        constraints = [
            # At most one unfinished run per job, so two overlapping runs can't
            # both start: the second one's insert fails, and it steps aside.
            models.UniqueConstraint(
                fields=["job"], condition=Q(status="running"), name="one_running_run_per_job"
            ),
        ]

    def __str__(self):
        return f"{self.job}, {self.started_at:%Y-%m-%d %H:%M} UTC ({self.status})"

    @property
    def duration(self):
        """How long it ran, or None while it's running."""
        if self.finished_at is None:
            return None
        return self.finished_at - self.started_at
